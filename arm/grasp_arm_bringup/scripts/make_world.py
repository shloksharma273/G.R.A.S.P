#!/usr/bin/env python3
"""Build the Ignition-Fortress warehouse world for the Kinova demo.

The upstream world (leonhartyao/gazebo_models_worlds_collection, GPL-3.0) is a
Gazebo Classic file: SDF 1.5, classic <physics>, and four model:// references
the collection does not actually ship. Rather than copy a GPL world into this
repo, we read the user's own clone and emit a fresh SDF 1.7 world that reuses
its layout (the <include> poses) and its models -- resolved at runtime through
IGN_GAZEBO_RESOURCE_PATH.

What gets fixed on the way through:
  * sun / ground_plane          -> Ignition-native light + inline ground plane
  * grey_wall / trash_can       -> inline primitives (not in the collection)
  * classic ODE <physics>       -> Ignition physics + the system plugins
                                   Fortress needs declared explicitly
  * every included model        -> forced <static> (see workstation.yaml)

Then it appends the workstation: table, the robot's mount point, the graspable
objects and the place pad, all from config/workstation.yaml.

    ros2 run grasp_arm_bringup make_world.py [--vendor DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import yaml
except ModuleNotFoundError as exc:   # pragma: no cover
    raise SystemExit(
        "PyYAML is missing from the interpreter running this node.\n"
        "This usually means a project virtualenv is active: ROS injects rclpy "
        "through PYTHONPATH, which bypasses venv isolation, but PyYAML does "
        "not come that way.\n"
        "Fix it with either:\n"
        "    pip install pyyaml          # into the active venv\n"
        "    deactivate                  # and run ROS commands outside it"
    ) from exc

HERE = Path(__file__).resolve().parent
PKG = HERE.parent

# Fortress does not load default system plugins for a world that declares none
# of its own in every launch path, so be explicit. Contact is here because the
# grasp harness needs contact sensing later.
SYSTEM_PLUGINS = [
    ("ignition-gazebo-physics-system", "ignition::gazebo::systems::Physics"),
    ("ignition-gazebo-user-commands-system", "ignition::gazebo::systems::UserCommands"),
    ("ignition-gazebo-scene-broadcaster-system", "ignition::gazebo::systems::SceneBroadcaster"),
    ("ignition-gazebo-contact-system", "ignition::gazebo::systems::Contact"),
]

# model:// entries we replace rather than include.
INLINE_SUBSTITUTES = ("grey_wall", "first_2015_trash_can")
DROPPED_INCLUDES = ("sun", "ground_plane")


def box_inertia(mass: float, sx: float, sy: float, sz: float) -> dict[str, float]:
    k = mass / 12.0
    return {
        "ixx": k * (sy * sy + sz * sz),
        "iyy": k * (sx * sx + sz * sz),
        "izz": k * (sx * sx + sy * sy),
    }


def cylinder_inertia(mass: float, radius: float, length: float) -> dict[str, float]:
    ixx = mass * (3.0 * radius * radius + length * length) / 12.0
    return {"ixx": ixx, "iyy": ixx, "izz": 0.5 * mass * radius * radius}


def indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def surface(mu: float = 1.2, mu2: float = 1.2) -> str:
    """High friction. The 2F-85 has no tactile feedback, so the contact patch
    is all that keeps a grasped object from squirting out sideways."""
    return f"""<surface>
  <friction>
    <ode><mu>{mu}</mu><mu2>{mu2}</mu2></ode>
  </friction>
  <contact>
    <ode><kp>1e6</kp><kd>1e2</kd></ode>
  </contact>
</surface>"""


def visual_material(color: list[float], alpha: float = 1.0) -> str:
    r, g, b = color
    return f"""<material>
  <ambient>{r * 0.4:.3f} {g * 0.4:.3f} {b * 0.4:.3f} {alpha}</ambient>
  <diffuse>{r:.3f} {g:.3f} {b:.3f} {alpha}</diffuse>
  <specular>0.1 0.1 0.1 {alpha}</specular>
</material>"""


def static_box(name, size, pose, color) -> str:
    sx, sy, sz = size
    geom = f"<box><size>{sx} {sy} {sz}</size></box>"
    return f"""<model name="{name}">
  <static>true</static>
  <pose>{pose}</pose>
  <link name="link">
    <collision name="collision">
      <geometry>{geom}</geometry>
{indent(surface(), 6)}
    </collision>
    <visual name="visual">
      <geometry>{geom}</geometry>
{indent(visual_material(color), 6)}
    </visual>
  </link>
</model>"""


def static_cylinder(name, radius, length, pose, color) -> str:
    geom = f"<cylinder><radius>{radius}</radius><length>{length}</length></cylinder>"
    return f"""<model name="{name}">
  <static>true</static>
  <pose>{pose}</pose>
  <link name="link">
    <collision name="collision">
      <geometry>{geom}</geometry>
{indent(surface(), 6)}
    </collision>
    <visual name="visual">
      <geometry>{geom}</geometry>
{indent(visual_material(color), 6)}
    </visual>
  </link>
</model>"""


def marker(name: str, x: float, y: float, z: float, color: list[float],
           radius: float = 0.030) -> str:
    """A flat visual-only disc, so the demo shows where the named points are."""
    return f"""<model name="{name}">
  <static>true</static>
  <pose>{x} {y} {z} 0 0 0</pose>
  <link name="link">
    <visual name="visual">
      <geometry><cylinder><radius>{radius}</radius><length>0.002</length></cylinder></geometry>
{indent(visual_material(color), 6)}
    </visual>
  </link>
</model>"""


def graspable(obj: dict) -> str:
    """A dynamic, grippable object with correct inertia."""
    name = obj["name"]
    mass = float(obj["mass"])
    x, y, z = obj["pose"]
    if obj["type"] == "box":
        sx, sy, sz = obj["size"]
        geom = f"<box><size>{sx} {sy} {sz}</size></box>"
        inertia = box_inertia(mass, sx, sy, sz)
    elif obj["type"] == "cylinder":
        r, ln = float(obj["radius"]), float(obj["length"])
        geom = f"<cylinder><radius>{r}</radius><length>{ln}</length></cylinder>"
        inertia = cylinder_inertia(mass, r, ln)
    else:
        raise ValueError(f"{name}: unknown object type {obj['type']!r}")

    mu = float(obj.get("mu", 1.4))
    return f"""<model name="{name}">
  <pose>{x} {y} {z} 0 0 0</pose>
  <link name="link">
    <inertial>
      <mass>{mass}</mass>
      <inertia>
        <ixx>{inertia['ixx']:.8f}</ixx><ixy>0</ixy><ixz>0</ixz>
        <iyy>{inertia['iyy']:.8f}</iyy><iyz>0</iyz>
        <izz>{inertia['izz']:.8f}</izz>
      </inertia>
    </inertial>
    <collision name="collision">
      <geometry>{geom}</geometry>
{indent(surface(mu, mu), 6)}
    </collision>
    <visual name="visual">
      <geometry>{geom}</geometry>
{indent(visual_material(obj['color']), 6)}
    </visual>
  </link>
</model>"""


def build_table(cfg: dict) -> str:
    t = cfg["table"]
    cx, cy = t["centre"]
    sx, sy, thick = t["top_size"]
    top_z = float(t["top_height"])
    leg = float(t["leg"])
    colour = t["color"]

    parts = [
        f"""  <link name="top">
    <pose>{cx} {cy} {top_z - thick / 2.0:.4f} 0 0 0</pose>
    <collision name="collision">
      <geometry><box><size>{sx} {sy} {thick}</size></box></geometry>
{indent(surface(1.0, 1.0), 6)}
    </collision>
    <visual name="visual">
      <geometry><box><size>{sx} {sy} {thick}</size></box></geometry>
{indent(visual_material(colour), 6)}
    </visual>
  </link>"""
    ]

    leg_h = top_z - thick
    inset = leg  # keep legs just inside the slab edge
    for i, (dx, dy) in enumerate(
        [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    ):
        lx = cx + dx * (sx / 2.0 - inset)
        ly = cy + dy * (sy / 2.0 - inset)
        parts.append(
            f"""  <link name="leg_{i}">
    <pose>{lx:.4f} {ly:.4f} {leg_h / 2.0:.4f} 0 0 0</pose>
    <collision name="collision">
      <geometry><box><size>{leg} {leg} {leg_h}</size></box></geometry>
    </collision>
    <visual name="visual">
      <geometry><box><size>{leg} {leg} {leg_h}</size></box></geometry>
{indent(visual_material([c * 0.7 for c in colour]), 6)}
    </visual>
  </link>"""
        )

    body = "\n".join(parts)
    return f"""<model name="{t['name']}">
  <static>true</static>
{body}
</model>"""


def parse_includes(world_path: Path) -> list[dict]:
    """Pull (uri, name, pose, static) out of the upstream world's includes."""
    raw = world_path.read_text()
    out = []
    for block in re.finditer(r"<include>(.*?)</include>", raw, re.S):
        b = block.group(1)
        uri = re.search(r"model://([\w/.\-]+)", b)
        if not uri:
            continue
        pose = re.search(r"<pose>([^<]*)</pose>", b)
        name = re.search(r"<name>([^<]*)</name>", b)
        static = re.search(r"<static>([^<]*)</static>", b)
        out.append(
            {
                "model": uri.group(1),
                "name": name.group(1).strip() if name else None,
                "pose": pose.group(1).strip() if pose else "0 0 0 0 0 0",
                "static": static.group(1).strip() if static else None,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendor", type=Path,
                    default=PKG.parent / "vendor" / "gazebo_models_worlds_collection",
                    help="clone of leonhartyao/gazebo_models_worlds_collection")
    ap.add_argument("--out", type=Path,
                    default=PKG / "worlds" / "generated" / "warehouse_kinova.sdf")
    ap.add_argument("--config", type=Path, default=PKG / "config" / "workstation.yaml")
    args = ap.parse_args()

    upstream = args.vendor / "worlds" / "warehouse.world"
    if not upstream.is_file():
        print(f"error: {upstream} not found.\n"
              f"       run:  vcs import vendor < arm_demo.repos", file=sys.stderr)
        return 1

    cfg = yaml.safe_load(args.config.read_text())
    force_static = bool(cfg["world"].get("force_static_includes", True))
    subs = cfg["world"].get("substitute_models", {})

    includes = parse_includes(upstream)
    chunks: list[str] = []

    # --- lighting + ground (Ignition-native, replacing the classic includes) --
    chunks.append("""<light type="directional" name="sun">
  <cast_shadows>true</cast_shadows>
  <pose>0 0 12 0 0 0</pose>
  <diffuse>0.9 0.9 0.9 1</diffuse>
  <specular>0.25 0.25 0.25 1</specular>
  <attenuation>
    <range>1000</range><constant>0.9</constant>
    <linear>0.01</linear><quadratic>0.001</quadratic>
  </attenuation>
  <direction>-0.5 0.4 -1</direction>
</light>""")

    chunks.append(f"""<model name="ground_plane">
  <static>true</static>
  <link name="link">
    <collision name="collision">
      <geometry><plane><normal>0 0 1</normal><size>200 200</size></plane></geometry>
{indent(surface(1.0, 1.0), 6)}
    </collision>
    <visual name="visual">
      <geometry><plane><normal>0 0 1</normal><size>200 200</size></plane></geometry>
{indent(visual_material([0.55, 0.55, 0.58]), 6)}
    </visual>
  </link>
</model>""")

    # --- the warehouse itself, reusing upstream's layout --------------------
    used_names: dict[str, int] = {}
    kept = subbed = 0
    for inc in includes:
        model = inc["model"]
        if model in DROPPED_INCLUDES:
            continue

        # Upstream reuses some <name> values (e.g. "pallet B-1-1-1 support"),
        # which is fatal in Ignition -- entity names must be unique.
        base = inc["name"] or model
        base = re.sub(r"\s+", "_", base)
        n = used_names.get(base, 0)
        used_names[base] = n + 1
        name = base if n == 0 else f"{base}_{n}"

        if model in INLINE_SUBSTITUTES:
            spec = subs.get(model)
            if spec is None:
                continue
            if spec["type"] == "box":
                chunks.append(static_box(name, spec["size"], inc["pose"], spec["color"]))
            else:
                chunks.append(
                    static_cylinder(name, spec["radius"], spec["length"],
                                    inc["pose"], spec["color"])
                )
            subbed += 1
            continue

        static = "true" if force_static else (inc["static"] or "false")
        chunks.append(f"""<include>
  <uri>model://{model}</uri>
  <name>{name}</name>
  <pose>{inc['pose']}</pose>
  <static>{static}</static>
</include>""")
        kept += 1

    # --- the workstation ----------------------------------------------------
    chunks.append(build_table(cfg))

    pad = cfg["place_pad"]
    px, py = pad["centre"]
    psx, psy, psz = pad["size"]
    top_z = float(cfg["table"]["top_height"])
    chunks.append(
        static_box(pad["name"], [psx, psy, psz],
                   f"{px} {py} {top_z + psz / 2.0:.4f} 0 0 0", pad["color"])
    )

    # Mark the named points on the board. Visual only -- no <collision>, or a
    # block would come to rest on the marker instead of the pad and every
    # placement check would be off by the marker's thickness.
    sites = cfg.get("sites", {})
    group_of = {
        name: group
        for group, names in sites.get("groups", {}).items()
        for name in names
    }
    group_colour = {"triangle": [0.90, 0.35, 0.10], "line": [0.20, 0.55, 0.90]}
    for place, (mx, my) in sites.get("places", {}).items():
        colour = group_colour.get(group_of.get(place), [0.5, 0.5, 0.5])
        chunks.append(marker(f"mark_{place}", mx, my, top_z + psz + 0.001, colour))
    for name, (mx, my) in sites.get("staging", {}).items():
        chunks.append(marker(f"stage_{name}", mx, my, top_z + 0.001,
                             [0.45, 0.45, 0.45], radius=0.035))

    for obj in cfg["objects"]:
        chunks.append(graspable(obj))

    body = "\n".join(indent(c, 4) for c in chunks)
    plugins = "\n".join(
        f'    <plugin filename="{f}" name="{n}"/>' for f, n in SYSTEM_PLUGINS
    )

    sdf = f"""<?xml version="1.0" ?>
<!-- GENERATED by grasp_arm_bringup/scripts/make_world.py - do not edit.
     Layout derived from {upstream}
     (leonhartyao/gazebo_models_worlds_collection, GPL-3.0); models are
     resolved at runtime via IGN_GAZEBO_RESOURCE_PATH. Workstation from
     {args.config.name}. -->
<sdf version="1.7">
  <world name="warehouse">

    <physics name="default" type="dart">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>1000</real_time_update_rate>
    </physics>
    <gravity>0 0 -9.8066</gravity>

{plugins}

    <scene>
      <ambient>0.5 0.5 0.5 1</ambient>
      <background>0.7 0.75 0.8 1</background>
      <shadows>true</shadows>
    </scene>

{body}

  </world>
</sdf>
"""

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(sdf)

    # The launch file needs to put the collection's models on
    # IGN_GAZEBO_RESOURCE_PATH, but it runs from the install share dir and has
    # no way to find the vendor clone. Record it next to the world.
    models_dir = (args.vendor / "models").resolve()
    (args.out.parent / "resource_path.txt").write_text(str(models_dir) + "\n")

    print(f"wrote {args.out}")
    print(f"  resource path             : {models_dir}")
    print(f"  warehouse models included : {kept}")
    print(f"  inline substitutes        : {subbed}")
    print(f"  graspable objects         : {len(cfg['objects'])}")
    print(f"  robot spawn               : {cfg['robot']['spawn_pose']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
