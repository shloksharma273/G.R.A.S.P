# G.R.A.S.P — robotic arm demo

Kinova Gen3 (6 DoF) with a Robotiq 2F-85, spawned in the **warehouse** world
from [leonhartyao/gazebo_models_worlds_collection][worlds], picking and placing
three objects and acknowledging every pick and drop.

Driven either directly, or from a G.R.A.S.P plan: say *"make a magic sequence"*
and the arm lays the three blocks out as a triangle, because a rulebook says
that is what a magic sequence is.

Verified end to end: **3/3 steps for both sequences**, acknowledgements checked
against Ignition ground truth rather than assumed. The placed line is collinear
to 1.7 mm.

[worlds]: https://github.com/leonhartyao/gazebo_models_worlds_collection

## Three layers

```
  natural command  ->  G.R.A.S.P Layer 2  ->  plan.json
                                                  |
                                       plan_bridge.py   (Layer 3: this repo)
                                       tables + state set, no LLM
                                                  |
                                       ExecuteTask action
                                                  |
                                       task_executor.py (the arm's only skill)
                                       poses, gripper, verification
```

`plan.py` in G.R.A.S.P calls plan.json *"the boundary to Layer 3"*. `plan_bridge`
is Layer 3. It consumes the contract, never the PlanGraph or the database, so it
has no dependency on G.R.A.S.P internals and can be driven by a hand-written
plan.

The division that matters: **the bridge never sees a coordinate, and the arm has
never heard of a magic sequence.**

```
arm/
  setup.sh                  one-shot: fetch, patch, generate world, build
  arm_demo.repos            third-party sources (vcs import)
  patches/                  the one vendor patch we carry
  vendor/                   third-party clones (gitignored, ~1.7 GB)
  tools/emit_plan.py        rulebook -> G.R.A.S.P -> plan.json (offline, no LLM)
  grasp_arm_msgs/           the ExecuteTask action
  grasp_arm_bringup/
    config/workstation.yaml physical truth: sites, gripper calibration, limits
    config/bindings.yaml    Layer 3 vocabulary: rulebook words -> site ids
    config/capabilities.yaml which (block, place) pairs are verified reachable
    rulebooks/              the block rulebooks
    launch/warehouse_sim.launch.py   sim + robot + controllers + bridges + server
    launch/moveit.launch.py          move_group, wired for sim time
    scripts/task_executor.py         the action server: the only thing that moves
    scripts/plan_bridge.py           Layer 3
    scripts/calibrate_reach.py       builds capabilities.yaml
    scripts/pick_place.py            thin CLI over the task server
    scripts/make_world.py            builds the Ignition world from vendor/
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

# --- second terminal ---
# once, with the sim up: which (block, place) pairs are actually achievable
ros2 run grasp_arm_bringup calibrate_reach.py

# the G.R.A.S.P-driven path
cd src/G.R.A.S.P/arm
./tools/emit_plan.py "make a magic sequence" -o /tmp/plan.json
ros2 run grasp_arm_bringup plan_bridge.py /tmp/plan.json --reset
ros2 run grasp_arm_bringup plan_bridge.py /tmp/plan.json --dry-run   # validate only

# or drive the arm directly
ros2 run grasp_arm_bringup pick_place.py red_block:dot_x
ros2 run grasp_arm_bringup pick_place.py --reset
```

`--reset` returns the objects to their start poses. Acknowledgements are printed
and published as JSON on `/grasp_arm/events`:

```json
{"event": "picked", "object": "red_cube", "rise_m": 0.0727, "gripper_cmd": 0.3637, "gripper_actual": 0.354}
{"event": "placed", "object": "red_cube", "xy_error_m": 0.0146, "z_error_m": 0.0, "final": [0.115, 1.1, 0.795]}
```

Arm events on `/grasp_arm/events`: `task_started`, `picked`, `pick_retry`,
`placed`, `task_failed`, `scene_reset`. Plan events on `/grasp_arm/plan_events`:
`plan_started`, `step_started`, `step_complete`, `step_failed`, `step_skipped`,
`plan_complete`, `plan_aborted`.

## How Layer 3 works

Four tables and a state set. No LLM, no parsing.

| | |
|---|---|
| verbs | which action names mean pick-and-place (`bindings.yaml`) |
| objects | rulebook word -> scene object id (`bindings.yaml`) |
| places | rulebook word -> site id (`bindings.yaml`) |
| capability | (object, place) -> verified reachable (`capabilities.yaml`) |
| state set | the step tokens currently believed true (runtime) |

Arguments are resolved **by type, not position**: G.R.A.S.P emits `uses` in no
fixed order — one step lists the vertex first, the next lists the block first —
so each name is looked up in both tables and the roles fall out. The state set
never interprets a `requires`/`produces` string; they are opaque tokens, which
keeps the bridge free of string parsing and lets you rename states in a rulebook
without touching code.

Everything checkable is checked **before anything moves**: known verbs, resolvable
arguments, pairs present in the capability table, no two steps targeting the same
point, nothing already occupied, and the precondition chain walked with a
simulated state set so an internally inconsistent plan is caught up front. All
problems are reported at once.

Then, per step: send the task, wait, and — the line that matters — assert
`produces` **only after the arm reports a measured success**. A symbolic
postcondition becomes true because something was physically verified. That is the
whole reason this layer exists.

`policy.on_step_failure` in `bindings.yaml` decides abort vs continue. It
defaults to abort: two thirds of a triangle is not a partial success.

## Rulebook authoring rules

Two constraints, both learned the hard way:

**Place names must be words, not letters.** The ingestion parser strips `a` as an
article, so `vertex a` tokenises to just `["vertex"]` and would match every
sentence mentioning any vertex. The rulebooks say *left vertex*, *first dot*, and
`bindings.yaml` maps those to `vertex_a`, `dot_x`.

**One unique action name per step.** The graph keys on the action name, so three
steps cannot all be called `place_block`. The name therefore carries the
specifics (`place_red_block_at_left_vertex`) and the *arguments* come from
`uses`.

`bindings.yaml` is validated on every run: any binding pointing at a place or
object that does not exist is a hard error, so drift surfaces immediately.

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

**Three IK lessons, all of which cost real time.** The stack uses KDL on a
short timeout, and it is the least reliable component here.

*Retrying with the same seed is pointless.* KDL is a deterministic Newton solve
from its seed state, so four attempts from one seed re-run one failure. Adding
random restarts after the first attempt took the capability table from 17/18
pairs to 18/18 and removed the intermittent grasp failures. If you want a real
fix, swap the solver.

*Continuous joints come back a full turn away.* `joint_1/4/6` are continuous, so
a solution can be 2π off — kinematically identical, but the controller then
swings the joint all the way round. Solutions are pulled onto the branch nearest
the arm's current pose.

*A destination being reachable does not mean the pose above it is.* The approach
pose is usually the binding one, which is why `calibrate_reach.py` checks all
five waypoints and why the executor falls back through
`motion.approach_heights`.

**Kill leftover action servers between runs.** `task_executor` is an action
server. A leftover one from a previous simulator stays bound to
`/task_executor/execute_task` and answers clients using poses frozen at that
sim's startup — reporting the fingertips 1.2 m in the air. It looks exactly like
a grasping bug, and it is not. `ros2 node list | grep task_executor` should
print one line.

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
