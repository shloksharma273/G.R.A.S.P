#!/usr/bin/env python3
"""Which (block, place) pairs are actually achievable? Answer once, offline.

For every pair it IK-checks *every waypoint of the whole step* -- above the
block, at the block, lifted, above the destination, at the destination -- not
just the destination point. That distinction matters: a destination can be
reachable while the pose 12 cm above it is not, which is exactly how an early
version of this demo failed mid-air with a block in the gripper.

Nothing moves. It only needs the sim up so /compute_ik has a robot model.

Writes config/capabilities.yaml, which the plan bridge then consults as a table
lookup -- so validating a plan costs nothing and needs no simulator.

    ros2 run grasp_arm_bringup calibrate_reach.py
"""

from __future__ import annotations

import argparse
import itertools
import math
import random
import sys
import time
from pathlib import Path

import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from moveit_msgs.srv import GetPositionIK
from rclpy.node import Node
from sensor_msgs.msg import JointState

ARM_JOINTS = [f"joint_{i}" for i in range(1, 7)]
TWO_PI = 2.0 * math.pi


def _nearest_branch(value: float, reference: float) -> float:
    while value - reference > math.pi:
        value -= TWO_PI
    while reference - value > math.pi:
        value += TWO_PI
    return value

PKG = "grasp_arm_bringup"


class Calibrator(Node):
    def __init__(self):
        super().__init__("calibrate_reach")
        self.share = Path(get_package_share_directory(PKG))
        self.cfg = yaml.safe_load((self.share / "config" / "workstation.yaml").read_text())
        self.g, self.m = self.cfg["gripper"], self.cfg["motion"]
        self.sites = self.cfg["sites"]
        b = self.cfg["robot"]["spawn_pose"]
        self.base = (float(b["x"]), float(b["y"]), float(b["z"]))
        self.pad_top = float(self.cfg["table"]["top_height"]) + \
            float(self.cfg["place_pad"]["size"][2])
        self.objects = {o["name"]: o for o in self.cfg["objects"]}

        self.ik = self.create_client(GetPositionIK, "/compute_ik")
        self.js = None
        self.create_subscription(JointState, "/joint_states",
                                 lambda msg: setattr(self, "js", msg), 10)
        if not self.ik.wait_for_service(timeout_sec=45.0):
            raise RuntimeError("/compute_ik unavailable -- start the sim launch first")
        t0 = time.time()
        while self.js is None and time.time() - t0 < 30:
            rclpy.spin_once(self, timeout_sec=0.3)
        if self.js is None:
            raise RuntimeError("no /joint_states")

    def solvable(self, x, y, z, seed, tries=4):
        """Returns a joint solution or None. x,y,z in base_link, gripper down."""
        best, best_cost = None, float("inf")
        for attempt in range(tries):
            req = GetPositionIK.Request()
            r = req.ik_request
            r.group_name = "manipulator"
            r.ik_link_name = "end_effector_link"
            r.avoid_collisions = False
            r.timeout.nanosec = 50_000_000
            r.pose_stamped.header.frame_id = "base_link"
            r.pose_stamped.pose.position.x = float(x)
            r.pose_stamped.pose.position.y = float(y)
            r.pose_stamped.pose.position.z = float(z)
            r.pose_stamped.pose.orientation.x = 1.0
            r.pose_stamped.pose.orientation.w = 0.0
            r.robot_state.joint_state.name = list(self.js.name)
            pos = list(self.js.position)
            start = list(seed)
            # Same reason as in task_executor.solve_ik: identical seeds give
            # identical KDL failures, so perturb after the first attempt.
            if attempt:
                start = [v + random.uniform(-0.6, 0.6) for v in start]
            for j, val in zip(ARM_JOINTS, start):
                pos[r.robot_state.joint_state.name.index(j)] = val
            r.robot_state.joint_state.position = pos
            fut = self.ik.call_async(req)
            rclpy.spin_until_future_complete(self, fut, timeout_sec=10.0)
            res = fut.result()
            if res is None or res.error_code.val != 1:
                continue
            sol = dict(zip(res.solution.joint_state.name, res.solution.joint_state.position))
            # See task_executor._nearest_branch: continuous joints can come
            # back a full turn away.
            q = [_nearest_branch(sol[j], ref) for j, ref in zip(ARM_JOINTS, seed)]
            cost = max(abs(a - b) for a, b in zip(q, seed))
            if cost < best_cost:
                best, best_cost = q, cost
            if best_cost < 0.35:
                break
        return best

    def check_pair(self, object_id, place_id, tries=4):
        """Every waypoint of the step. Returns (ok, first failing waypoint)."""
        obj = self.objects[object_id]
        half_h = float(obj["size"][2]) / 2.0
        sx, sy = self.sites["staging"][object_id]
        start_z = float(self.cfg["table"]["top_height"]) + half_h
        tx, ty = self.sites["places"][place_id]

        cx, cy, cz = (sx - self.base[0], sy - self.base[1], start_z - self.base[2])
        grasp_z = cz + self.g["tcp_offset_z"]
        release_world = self.pad_top + half_h + self.m["release_gap"]
        px, py = tx - self.base[0], ty - self.base[1]
        pz = release_world - self.base[2] + self.g["tcp_offset_z"]

        # above_block is checked against the lowest fallback height, matching
        # what the executor will actually settle for.
        waypoints = [
            ("above_block", cx, cy, grasp_z + min(self.m["approach_heights"])),
            ("at_block", cx, cy, grasp_z),
            ("lifted", cx, cy, grasp_z + self.m["lift_height"]),
            ("above_place", px, py, pz + min(self.m["approach_heights"])),
            ("at_place", px, py, pz),
        ]
        seed = list(self.m["home"])
        for name, x, y, z in waypoints:
            q = self.solvable(x, y, z, seed, tries=tries)
            if q is None:
                return False, name
            seed = q
        return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None,
                    help="default: the package's config/capabilities.yaml (source tree)")
    args = ap.parse_args()

    rclpy.init()
    cal = Calibrator()
    objects = list(cal.objects)
    places = list(cal.sites["places"])

    print(f"checking {len(objects)}x{len(places)} pairs, 5 waypoints each\n")
    table: dict[str, dict[str, bool]] = {}
    failures: dict[str, str] = {}
    for obj in objects:
        table[obj] = {}
        row = f"  {obj:12s} "
        for place in places:
            ok, where = cal.check_pair(obj, place)
            if not ok:
                # KDL is iterative on a short timeout and produces spurious
                # misses -- a pair can fail at a waypoint its siblings pass.
                # Re-check once, harder, before recording a "no".
                ok, where = cal.check_pair(obj, place, tries=10)
            table[obj][place] = ok
            if not ok:
                failures[f"{obj}->{place}"] = where
            row += f"{('OK' if ok else 'no'):>4}"
        print(row + "        " + " ".join(f"{p}" for p in []), flush=True)
    print("  " + " " * 12 + "".join(f"{p.split('_')[-1]:>4}" for p in places))

    # --- can each group actually be built? bipartite matching over the table --
    print("\ngroup feasibility (distinct block per point):")
    group_ok = {}
    for group, names in cal.sites["groups"].items():
        found = None
        for perm in itertools.permutations(objects, len(names)):
            if all(table[o][p] for o, p in zip(perm, names)):
                found = dict(zip(names, perm))
                break
        group_ok[group] = found is not None
        if found:
            print(f"  {group:9s} FEASIBLE  " +
                  ", ".join(f"{p}<-{o}" for p, o in found.items()))
        else:
            print(f"  {group:9s} NOT FEASIBLE with these blocks/points")

    # --- static clearance report (informational across groups) ---------------
    floor = float(cal.sites["clearance_min"])
    print(f"\nclearance (minimum {floor*100:.0f} cm between co-occupied points):")
    for group, names in cal.sites["groups"].items():
        worst = min((math.dist(cal.sites["places"][a], cal.sites["places"][b]), a, b)
                    for a, b in itertools.combinations(names, 2))
        flag = "OK" if worst[0] >= floor else "TOO CLOSE"
        print(f"  within {group:9s} closest {worst[1]}-{worst[2]}: "
              f"{worst[0]*100:.0f} cm  {flag}")
    cross = [(math.dist(cal.sites["places"][a], cal.sites["places"][b]), a, b)
             for a, b in itertools.combinations(places, 2)
             if not any(a in n and b in n for n in cal.sites["groups"].values())]
    tight = sorted(c for c in cross if c[0] < floor)
    if tight:
        print(f"  cross-group pairs closer than {floor*100:.0f} cm "
              f"(fine -- never occupied together, enforced at runtime):")
        for d, a, b in tight[:4]:
            print(f"    {a}-{b}: {d*100:.0f} cm")

    out = args.out or (Path(__file__).resolve().parent.parent / "config" / "capabilities.yaml")
    doc = {
        "generated_by": "calibrate_reach.py",
        "note": ("Every waypoint of the step was IK-checked, not just the "
                 "destination. Regenerate after moving the board, the staging "
                 "column, the robot, or any object size."),
        "pairs": table,
        "groups_feasible": group_ok,
        "waypoint_failures": failures,
    }
    out.write_text(yaml.safe_dump(doc, sort_keys=False))
    print(f"\nwrote {out}")
    n_ok = sum(1 for o in table for p in table[o] if table[o][p])
    print(f"  {n_ok}/{len(objects)*len(places)} pairs usable")
    cal.destroy_node(); rclpy.shutdown()
    return 0 if all(group_ok.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
