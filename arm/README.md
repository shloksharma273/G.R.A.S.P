# G.R.A.S.P — robotic arm demo

Kinova Gen3 (6 DoF) with a Robotiq 2F-85, spawned in the **warehouse** world
from [leonhartyao/gazebo_models_worlds_collection][worlds], picking and placing
three objects and acknowledging every pick and drop.

Verified end to end: **3/3 pick-and-place cycles**, acknowledgements checked
against Ignition ground truth rather than assumed.

[worlds]: https://github.com/leonhartyao/gazebo_models_worlds_collection

## Layout

```
arm/
  setup.sh                  one-shot: fetch, patch, generate world, build
  arm_demo.repos            third-party sources (vcs import)
  patches/                  the one vendor patch we carry
  vendor/                   third-party clones (gitignored, ~1.7 GB)
  grasp_arm_bringup/
    config/workstation.yaml single source of truth: layout, gripper, tolerances
    launch/warehouse_sim.launch.py   sim + robot + controllers + bridges
    launch/moveit.launch.py          move_group, wired for sim time
    scripts/make_world.py            builds the Ignition world from vendor/
    scripts/pick_place.py            the demo
    worlds/generated/                generated world (gitignored)
```

Third-party sources are fetched rather than vendored into git: `ros2_kortex` plus
the worlds collection is ~1.7 GB, and the collection is GPL-3.0. `make_world.py`
reads the upstream world and emits our own SDF, so no GPL file is copied into
this repo.

## Quick start

```bash
cd <workspace>/src/G.R.A.S.P/arm && ./setup.sh
cd <workspace> && source install/setup.bash

ros2 launch grasp_arm_bringup warehouse_sim.launch.py       # sim + MoveIt + RViz
ros2 run grasp_arm_bringup pick_place.py                    # second terminal
```

`--reset` returns the objects to their start poses. Acknowledgements are printed
and published as JSON on `/grasp_arm/events`:

```json
{"event": "picked", "object": "red_cube", "rise_m": 0.0727, "gripper_cmd": 0.3637, "gripper_actual": 0.354}
{"event": "placed", "object": "red_cube", "xy_error_m": 0.0146, "z_error_m": 0.0, "final": [0.115, 1.1, 0.795]}
```

Events: `picked`, `placed`, `pick_retry`, `pick_failed`, `place_failed`,
`run_complete`.

## Why this doesn't just use kortex_bringup

Five things had to be fixed. All were found by reading the installed packages
and then reproducing each one in the simulator.

**1. The world is hardcoded.** `kortex_sim_control.launch.py` passes
`ign_args: "-r -v 3 empty.sdf"` and declares no `world` argument (still true on
upstream `humble`). The Gazebo Classic branch is worse — `gzserver` is invoked
with an empty string where the world path belongs. Hence our own launch file.

**2. The gripper controller is never spawned** (in `ros-humble-kortex-bringup`
0.2.3). `robot_hand_controller_spawner` is assigned twice and the second
assignment shadows the first, leaving only the `gen3_lite` branch — so on a
`gen3` its condition is permanently false. Confirmed live: only three
controllers came up, and spawning `robotiq_gripper_controller` by hand worked
fine. Fixed upstream on `humble`, which is one reason we build from source.

**3. Gazebo Classic cannot drive this gripper.** With `sim_gazebo:=true` the
xacro gives the arm `gazebo_ros2_control/GazeboSystem` but the gripper
`robotiq_driver/RobotiqGripperHardwareInterface` — the real serial driver on
`/dev/ttyUSB0` — because the gripper macro has no `sim_gazebo` branch at all.
Only the Ignition path gives the gripper a simulated hardware interface, so the
Classic-format warehouse world is converted for Ignition instead.

**4. Commanding the gripper to 0.0 locks it permanently.** This is the one that
costs a day. `robotiq_85_left_knuckle_joint` has `lower="0.0"`. Command exactly
that and the joint jams for the rest of the session: the first close works, the
open to 0.0 works, and every command after is silently ignored while the
controller reports success. It is not the controller — a plain
`joint_trajectory_controller` on the same joint logs "Goal reached, success!"
while the joint sits at 0.0000. Holding 0.05 of margin cycles cleanly
indefinitely (7/7).

Note the SRDF ships `Open = 0.0` and `Close = 0.8` as its named gripper states —
**both** hard stops. Using MoveIt's named gripper targets walks straight into
this. `config/workstation.yaml` defines 0.05 / width-derived instead.

**5. Closing position must come from the object width.** Closing to a fixed
"closed" value shuts the gripper to a 9 mm gap, which sails past a 50 mm cube
and knocks it aside — the first grasp attempt moved the cube 21 mm and lifted
nothing. Sweeping the joint and reading fingertip separation out of Ignition
gives a clean linear map:

| q | 0.05 | 0.20 | 0.35 | 0.50 | 0.65 | 0.79 |
|---|------|------|------|------|------|------|
| separation (mm) | 131.1 | 116.8 | 101.2 | 84.8 | 67.8 | 51.8 |

i.e. `gap_mm = 84.7 − 107.2·q`, and 84.7 mm at q=0 matches the 2F-85's 85 mm
spec. A 50 mm cube wants q ≈ 0.32, not 0.70. With that, the cube lifted 7.8 cm
on the first try.

## Known limitations

**Cylinders are not reliable.** The fingers are position-controlled with no
tactile feedback, so a smooth cylinder is held on line contact. Measured: at
60 mm diameter it never leaves the table; at 46 mm it lifts ~49 mm and then
slips out during transit and rolls away. Boxes ran 8/8. All three demo objects
are therefore boxes. Reliable cylinder handling needs effort control on the
gripper joint — `gz_ros2_control` supports an effort command interface, so this
is a xacro change plus `effort_controllers/GripperActionController`, not a
redesign.

**Transit speed matters.** `/compute_ik` seeds randomly and will return a
solution on a different wrist branch; executing that as one waypoint slews
several joints at once and flung a grasped object 1.2 m across the room.
`pick_place.py` now samples several IK solutions and keeps the one nearest the
current pose, and stretches every move to respect `motion.max_joint_speed`.

**No collision-aware planning.** Motion is IK plus joint waypoints straight to
`joint_trajectory_controller`. The waypoint sequence (approach from above,
descend, lift straight up) is safe for this layout, but the table and objects
are not mirrored into the MoveIt planning scene, so `/move_action` is not
trustworthy here yet. That is the natural next step.

**Warehouse furniture is forced static.** 67 free-floating pallet models make
the solver crawl and let stacked pallets settle and jitter for the first few
seconds — exactly when planning starts. See `world.force_static_includes`.

**Four models the collection does not ship** (`sun`, `ground_plane`,
`grey_wall`, `first_2015_trash_can`) are substituted: the first two with
Ignition-native equivalents, the other two with inline primitives at the same
poses. `shelves_high2` uses a Gazebo Classic `<material><script>` block that
Ignition ignores, so those shelves render untextured. Cosmetic only.

## Environment

Ubuntu 22.04, ROS 2 Humble, Ignition Fortress (`ign gazebo` 6.17.1). Built from
source: `kortex_description`, `kortex_moveit_config/*`, `robotiq_description`.
From debs: `kortex_api`, `kortex_driver`, MoveIt, ros2_control, `ros_gz`.
There is no `moveit_py` on Humble (`import moveit` resolves to
`moveit_task_constructor`), which is why the demo talks to `/compute_ik` and the
controller actions directly rather than using a Python MoveIt binding.
