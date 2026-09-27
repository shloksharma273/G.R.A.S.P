# OpenAMRobot Navigation, Motion and Docking — Robot Skill Rulebook

## Overview

This rulebook describes how to operate the OpenAMRobot differential-drive AMR
through the ROS 2 Jazzy interfaces its platform software exposes: bringing up
localization and Nav2, localising on a map, going to a location, planning and
following paths, running recovery behaviours, commanding velocity directly,
docking on an AprilTag bundle, building and loading maps, and testing the
motors. Every primitive names the exact topic, service or action that performs
it. All commands assume CycloneDDS on domain 0
(RMW_IMPLEMENTATION=rmw_cyclonedds_cpp, ROS_DOMAIN_ID=0); with the default
FastDDS the robot's topics are invisible. The operator UI (openamrobot-ui over
rosbridge on port 9090) uses the same interfaces. This is not a certified
safety system: there is no hardware E-stop, the fastest stop is a zero
`/cmd_vel` or letting the 200 ms firmware watchdog expire, and the pack should
read 25 V or more at rest ahead of any navigation test.

## Skill

**operate_openamrobot** is a high-level skill. It is composed of the following
primitive actions: start localization, start navigation, set initial pose, go
to location, navigate to pose, navigate through poses, follow waypoints,
compute path to pose, follow path, cancel navigation, spin in place, back up,
drive on heading, clear local costmap, clear global costmap, drive with
velocity, drive with guarded velocity, dock robot, undock robot, enable
apriltag detection, restart lidar motor, set docking stop distance, save map,
load map, and run open loop motor test.

## Objects

The objects involved in this skill are: robot, map, goal pose, waypoints, path,
global costmap, local costmap, motors, lidar, camera, dock, and apriltag
bundle.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, navigation active,
robot localized, robot at goal, waypoints visited, path computed, navigation
cancelled, robot rotated, robot reversed, robot advanced, local costmap
cleared, global costmap cleared, robot moving, robot docked, robot undocked,
apriltag detection enabled, docking stop distance set, map saved, and motors
verified.

## Primitive actions and their rules

**start_localization** — The robot brings up its data sources and AMCL
localization on a saved map. On the real robot every terminal first runs
`source /opt/ros/jazzy/setup.bash`, `export
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` and `export ROS_DOMAIN_ID=0`, then the
Pi runs `ros2 launch openamrobot_bringup bringup_composed.launch.py
map:=$HOME/maps/<your_map>.yaml` (camera plus docking) or `ros2 launch
openamrobot_bringup bringup.launch.py map:=<map.yaml> use_camera:=false
use_docking:=false` (navigation only); in simulation it runs `ros2 launch
openamrobot_bringup bringup.launch.py sim:=true use_rviz:=true`. The launch
starts map_server and amcl under lifecycle_manager_localization, and the robot
confirms the pair is up with `ros2 service call
/lifecycle_manager_localization/is_active std_srvs/srv/Trigger`, which answers
success true once both nodes are active. The same launch starts the micro-ROS
agent and RPLIDAR drivers (or the Gazebo bridge), and with use_docking:=true or
the composed profile also the docking layer (dock_trigger and
detected_dock_pose_publisher). It is executed by calling the service
`/lifecycle_manager_localization/is_active` of type `std_srvs/srv/Trigger`.
After this action, drivers running is true. After this action, lidar spinning
is true. After this action, localization active is true. After this action, map
loaded is true. After this action, docking layer running is true.

**start_navigation** — The robot confirms the Nav2 navigation layer that the
same bring-up started: controller_server, planner_server, smoother_server,
behavior_server, bt_navigator, waypoint_follower, velocity_smoother and
collision_monitor, all managed by lifecycle_manager_navigation with a 60 s bond
timeout. It calls `ros2 service call /lifecycle_manager_navigation/is_active
std_srvs/srv/Trigger` and expects success true; `ros2 lifecycle get
/controller_server` answering active is the equivalent per-node check. Nodes
are never hand-activated, because hand activation comes up mis-initialised;
relaunch the navigation layer instead. It is executed by calling the service
`/lifecycle_manager_navigation/is_active` of type `std_srvs/srv/Trigger`. This
action requires that localization active is already true as a precondition.
After this action, navigation active is true.

**set_initial_pose** — The robot tells AMCL where it stands on the map, which
is mandatory on the real robot because set_initial_pose is disabled there and
without it no map to odom transform exists and the costmaps stay empty. This is
exactly what the RViz 2D Pose Estimate tool sends. From a terminal: `ros2 topic
pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "{header:
{frame_id: map}, pose: {pose: {position: {x: X, y: Y, z: 0.0}, orientation: {z:
sin(YAW/2), w: cos(YAW/2)}}}}"`. The robot then reads its estimate back on
`/amcl_pose` (geometry_msgs/msg/PoseWithCovarianceStamped). It is executed by
publishing to the topic `/initialpose` of type
`geometry_msgs/msg/PoseWithCovarianceStamped`. This action requires that
localization active is already true as a precondition. This action requires
that map loaded is already true as a precondition. After this action, robot
localized is true.

**go_to_location** — Going to location x, y with heading yaw means publishing a
PoseStamped in the map frame whose position is x, y and whose orientation
quaternion is z = sin(yaw/2), w = cos(yaw/2), for example `ros2 topic pub
--once /goal_pose geometry_msgs/msg/PoseStamped "{header: {frame_id: map},
pose: {position: {x: 2.0, y: 1.0, z: 0.0}, orientation: {z: 0.7071, w:
0.7071}}}"` to reach (2.0, 1.0) facing 90 degrees. This is the RViz 2D Goal
Pose tool, not the Nav2 Goal tool. bt_navigator listens on `/goal_pose_nav`, so
exactly one forwarder has to be running: dock_trigger when docking is enabled
(it undocks first if the robot is docked, then forwards), or `ros2 launch
openamrobot_bringup goal_relay.launch.py` in navigation-only mode, never both.
The goal is reached within 0.35 m and 0.35 rad. It is executed by publishing to
the topic `/goal_pose` of type `geometry_msgs/msg/PoseStamped`. This action
requires that navigation active is already true as a precondition. This action
requires that robot localized is already true as a precondition. After this
action, robot at goal is true.

**navigate_to_pose** — The robot sends one pose goal straight to bt_navigator
and gets feedback, a result and a cancel handle, the way docking phase 1 drives
to its staging pose: `ros2 action send_goal /navigate_to_pose
nav2_msgs/action/NavigateToPose "{pose: {header: {frame_id: map}, pose:
{position: {x: 2.0, y: 1.0}, orientation: {w: 1.0}}}}" --feedback`. The action
skips the dock_trigger undock gate, so on the dock the robot undocks first
itself. It is executed by sending a goal to the action `/navigate_to_pose` of
type `nav2_msgs/action/NavigateToPose`. This action requires that navigation
active is already true as a precondition. This action requires that robot
localized is already true as a precondition. After this action, robot at goal
is true.

**navigate_through_poses** — The robot drives through an ordered list of poses
as one continuous plan without stopping at the intermediate ones: `ros2 action
send_goal /navigate_through_poses nav2_msgs/action/NavigateThroughPoses
"{poses: [{header: {frame_id: map}, pose: {position: {x: 1.0, y: 0.0},
orientation: {w: 1.0}}}, {header: {frame_id: map}, pose: {position: {x: 2.0, y:
1.0}, orientation: {w: 1.0}}}]}"`. It is executed by sending a goal to the
action `/navigate_through_poses` of type
`nav2_msgs/action/NavigateThroughPoses`. This action requires that navigation
active is already true as a precondition. This action requires that robot
localized is already true as a precondition. After this action, robot at goal
is true.

**follow_waypoints** — The robot visits a list of waypoints one by one through
waypoint_follower, pausing at each with the wait_at_waypoint task executor:
`ros2 action send_goal /follow_waypoints nav2_msgs/action/FollowWaypoints
"{poses: [{header: {frame_id: map}, pose: {position: {x: 1.0, y: 0.0},
orientation: {w: 1.0}}}, {header: {frame_id: map}, pose: {position: {x: 2.0, y:
1.0}, orientation: {w: 1.0}}}]}"`. The result lists missed_waypoints. It is
executed by sending a goal to the action `/follow_waypoints` of type
`nav2_msgs/action/FollowWaypoints`. This action requires that navigation active
is already true as a precondition. This action requires that robot localized is
already true as a precondition. After this action, waypoints visited is true.

**compute_path_to_pose** — Generating a path means asking planner_server
(SmacPlanner2D A-star on the global costmap, planner id GridBased, 0.5 m
tolerance) for a global path without moving: `ros2 action send_goal
/compute_path_to_pose nav2_msgs/action/ComputePathToPose "{goal: {header:
{frame_id: map}, pose: {position: {x: 2.0, y: 1.0}, orientation: {w: 1.0}}},
planner_id: GridBased, use_start: false}"`. The result holds a
nav_msgs/msg/Path, and the planned path is also shown on `/plan`. It is
executed by sending a goal to the action `/compute_path_to_pose` of type
`nav2_msgs/action/ComputePathToPose`. This action requires that navigation
active is already true as a precondition. This action requires that robot
localized is already true as a precondition. After this action, path computed
is true.

**follow_path** — The robot drives along a given nav_msgs/msg/Path with
controller_server, which runs RotationShim wrapping DWB (rotate in place first
when the heading error exceeds 45 degrees) at no more than 0.20 m/s and 0.5
rad/s: `ros2 action send_goal /follow_path nav2_msgs/action/FollowPath "{path:
<path from compute_path_to_pose>, controller_id: FollowPath, goal_checker_id:
general_goal_checker}"`. The controller output goes to `/cmd_vel_nav` and
through the safety chain; the local plan is shown on `/local_plan`. It is
executed by sending a goal to the action `/follow_path` of type
`nav2_msgs/action/FollowPath`. This action requires that path computed is
already true as a precondition. After this action, robot at goal is true.

**cancel_navigation** — The robot abandons the active navigation goal (the UI
nav cancel does the same) by calling the action's cancel service with an empty
goal id, which cancels every goal: `ros2 service call
/navigate_to_pose/_action/cancel_goal action_msgs/srv/CancelGoal "{}"`. The
same `_action/cancel_goal` service exists on every other Nav2 action, and
Ctrl-C on a `ros2 action send_goal` also cancels. The navigation status is on
`/navigate_to_pose/_action/status`. It is executed by calling the service
`/navigate_to_pose/_action/cancel_goal` of type `action_msgs/srv/CancelGoal`.
This action requires that navigation active is already true as a precondition.
After this action, navigation cancelled is true.

**spin_in_place** — The robot rotates in place by a relative angle through the
behavior_server spin recovery: `ros2 action send_goal /spin
nav2_msgs/action/Spin "{target_yaw: 1.57, time_allowance: {sec: 20}}"`. Keep
the rotation speed at or above the 0.15 rad/s stiction floor, since slower yaw
stalls the wheels. It is executed by sending a goal to the action `/spin` of
type `nav2_msgs/action/Spin`. This action requires that navigation active is
already true as a precondition. After this action, robot rotated is true.

**back_up** — The robot reverses a set distance through the behavior_server
backup recovery: `ros2 action send_goal /backup nav2_msgs/action/BackUp
"{target: {x: 0.3}, speed: 0.05, time_allowance: {sec: 20}}"`. Speeds below the
0.04 m/s linear floor judder or stall. The 2D lidar sees nothing below about 18
cm, and nothing guards the rear. It is executed by sending a goal to the action
`/backup` of type `nav2_msgs/action/BackUp`. This action requires that
navigation active is already true as a precondition. After this action, robot
reversed is true.

**drive_on_heading** — The robot drives straight along its current heading for
a set distance through the behavior_server drive_on_heading behaviour: `ros2
action send_goal /drive_on_heading nav2_msgs/action/DriveOnHeading "{target:
{x: 0.5}, speed: 0.1, time_allowance: {sec: 20}}"`. It is executed by sending a
goal to the action `/drive_on_heading` of type
`nav2_msgs/action/DriveOnHeading`. This action requires that navigation active
is already true as a precondition. After this action, robot advanced is true.

**clear_local_costmap** — The robot wipes stale obstacles from the 3 by 3 m
rolling local costmap when it is stuck on a ghost obstacle: `ros2 service call
/local_costmap/clear_entirely_local_costmap nav2_msgs/srv/ClearEntireCostmap
"{}"`. Inflation can be tuned live with `ros2 param set
/local_costmap/local_costmap inflation_layer.inflation_radius 0.15`. It is
executed by calling the service `/local_costmap/clear_entirely_local_costmap`
of type `nav2_msgs/srv/ClearEntireCostmap`. This action requires that
navigation active is already true as a precondition. After this action, local
costmap cleared is true.

**clear_global_costmap** — The robot wipes the obstacle layer of the global
costmap so the planner can find a path again: `ros2 service call
/global_costmap/clear_entirely_global_costmap nav2_msgs/srv/ClearEntireCostmap
"{}"`. An empty global costmap (read on `/global_costmap/costmap`) means the
robot has no map to odom transform yet, which set_initial_pose fixes, not this
call. It is executed by calling the service
`/global_costmap/clear_entirely_global_costmap` of type
`nav2_msgs/srv/ClearEntireCostmap`. This action requires that navigation active
is already true as a precondition. After this action, global costmap cleared is
true.

**drive_with_velocity** — Giving cmd_vel means publishing a Twist straight to
the base (the Teensy through the micro-ROS agent on the real robot, the Gazebo
DiffDrive plugin in simulation) with linear.x in m/s and angular.z in rad/s:
`ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1},
angular: {z: 0.0}}"`. Publish continuously at 10 Hz or faster, because the
firmware watchdog zeroes the motors if no command arrives for about 200 ms;
publish all zeros to stop. Stay within 0.20 m/s and 0.5 rad/s and above the
0.04 m/s and 0.15 rad/s floors. This topic is the final one and bypasses
velocity_smoother and collision_monitor, and only one source may own it, so
kill any teleop_twist_keyboard or manual publisher ahead of navigating. It is
executed by publishing to the topic `/cmd_vel` of type
`geometry_msgs/msg/Twist`. This action requires that drivers running is already
true as a precondition. After this action, robot moving is true.

**drive_with_guarded_velocity** — The robot sends a manual Twist into the top
of the reactive safety chain instead of to the wheels, so velocity_smoother
clamps it to 0.20 m/s and 0.5 rad/s and collision_monitor (FootprintApproach,
0.8 s look-ahead on `/scan_filtered`) slows it ahead of a projected collision:
`ros2 topic pub -r 10 /cmd_vel_nav geometry_msgs/msg/Twist "{linear: {x: 0.1},
angular: {z: 0.0}}"`. The chain is `/cmd_vel_nav` to `/cmd_vel_smoothed` to
`/cmd_vel`, and `/collision_monitor_state` shows when the monitor is engaged.
Controller output uses this same topic, so do not publish here while a
navigation goal is running. It is executed by publishing to the topic
`/cmd_vel_nav` of type `geometry_msgs/msg/Twist`. This action requires that
navigation active is already true as a precondition. After this action, robot
moving is true.

**dock_robot** — The robot docks on the 3-tag AprilTag 36h11 bundle (IDs 0, 1
and 2) configured by dock_pose_x, dock_pose_y and dock_pose_yaw in
`config/dock_trigger.yaml`: `ros2 topic pub --once /dock_trigger
std_msgs/msg/Bool "{data: true}"`. dock_trigger drives to the staging pose with
NavigateToPose, centres the bundle in the camera, estimates the dock normal,
drives onto it and makes a lidar-controlled final approach that stops about
0.13 m from the dock. Progress is published on `/dock_trigger_status`
(std_msgs/msg/String: idle, docking, docked, undocking or failed, with a 2 s
heartbeat), and the tag pose is on `/detected_dock_pose`. A trigger that
arrives while a sequence is running is ignored. The docking layer runs only
with use_docking:=true or the composed bring-up. It is executed by publishing
to the topic `/dock_trigger` of type `std_msgs/msg/Bool`. This action requires
that navigation active is already true as a precondition. This action requires
that robot localized is already true as a precondition. This action requires
that docking layer running is already true as a precondition. This action
requires that lidar spinning is already true as a precondition. After this
action, robot docked is true.

**undock_robot** — The robot leaves the dock by reversing 0.7 m at 0.10 m/s in
the odom frame and then spinning about 180 degrees: `ros2 topic pub --once
/undock_robot std_msgs/msg/Bool "{data: true}"`. `/dock_trigger_status` reports
undocking, then idle on success. Sending a goal on `/goal_pose` while docked
triggers this same undock automatically ahead of forwarding the goal. It is
executed by publishing to the topic `/undock_robot` of type
`std_msgs/msg/Bool`. This action requires that robot docked is already true as
a precondition. After this action, robot undocked is true.

**enable_apriltag_detection** — The robot switches the on-demand AprilTag
camera gate on or off by hand. The gate exists only on the non-composed camera
path (apriltag_gate.py forwarding `/camera/image_raw` to `/apriltag/image_in`);
on the composed pipeline the call does nothing. dock_trigger toggles it itself
during docking: `ros2 service call /apriltag/set_enabled std_srvs/srv/SetBool
"{data: true}"`. Detections are on `/apriltag/detections`. It is executed by
calling the service `/apriltag/set_enabled` of type `std_srvs/srv/SetBool`.
This action requires that docking layer running is already true as a
precondition. After this action, apriltag detection enabled is true.

**restart_lidar_motor** — The robot spins the RPLIDAR motor back up.
dock_trigger stops it with `/stop_motor` (std_srvs/srv/Empty) during the camera
phases so the laser dot does not sweep the tags, and an interrupted dock can
leave it stopped, with `/scan` silent: `ros2 service call /start_motor
std_srvs/srv/Empty "{}"`. It is executed by calling the service `/start_motor`
of type `std_srvs/srv/Empty`. This action requires that drivers running is
already true as a precondition. After this action, lidar spinning is true.

**set_docking_stop_distance** — The robot retunes the docking stop live with no
relaunch; dock_trigger accepts docking_distance, stop_lidar_distance,
stop_on_lidar and detection_max_age at runtime, applied on the next dock: `ros2
param set /dock_trigger stop_lidar_distance 0.13`, which is the parameter
service call shown. It is executed by calling the service
`/dock_trigger/set_parameters` of type `rcl_interfaces/srv/SetParameters`. This
action requires that docking layer running is already true as a precondition.
After this action, docking stop distance set is true.

**save_map** — The robot builds a map with SLAM Toolbox in asynchronous mapping
mode (`ros2 launch openamrobot_nav2 online_async_launch.py`, 0.05 m cells,
reading `/scan`), is driven around the area, and then saves the map to a pgm
plus yaml pair: `ros2 service call /slam_toolbox/save_map
slam_toolbox/srv/SaveMap "{name: {data: '/home/<user>/maps/my_map'}}"`, or
equivalently `ros2 run nav2_map_server map_saver_cli -f ~/maps/my_map` as the
docs show. It is executed by calling the service `/slam_toolbox/save_map` of
type `slam_toolbox/srv/SaveMap`. This action requires that drivers running is
already true as a precondition. This action requires that lidar spinning is
already true as a precondition. After this action, map saved is true.

**load_map** — The robot swaps the map served on `/map` without relaunching:
`ros2 service call /map_server/load_map nav2_msgs/srv/LoadMap "{map_url:
'/home/<user>/maps/my_map.yaml'}"`. The usual route is passing map:= to the
bring-up, which is mandatory on the real robot. Localise again with
set_initial_pose on the new map. It is executed by calling the service
`/map_server/load_map` of type `nav2_msgs/srv/LoadMap`. This action requires
that localization active is already true as a precondition. This action
requires that map saved is already true as a precondition. After this action,
map loaded is true.

**run_open_loop_motor_test** — The robot proves its motors respond
independently of Nav2 by driving both wheels at one fixed PWM with the firmware
PID bypassed, wheels in the air and 24 V at 25 V or more at rest: `ros2 topic
pub -r 20 /debug/openloop geometry_msgs/msg/Vector3 "{x: 60.0}"` (x is PWM), or
`python3 tools/diagnostics/openloop_test.py [pwm] [duration]`. The firmware
cuts the drive if no open-loop command arrives for 300 ms. Per-wheel rpm is
read on `/debug/left` and `/debug/right`, published best effort, so subscribe
with `--qos-reliability best_effort`. It is executed by publishing to the topic
`/debug/openloop` of type `geometry_msgs/msg/Vector3`. This action requires
that drivers running is already true as a precondition. After this action,
motors verified is true.

## Ordering rules

Every terminal exports RMW_IMPLEMENTATION=rmw_cyclonedds_cpp and
ROS_DOMAIN_ID=0 ahead of any other command. Localization has to be active with
a map loaded before the robot can be given its initial pose, and the navigation
layer comes up only once localization publishes map to odom. The robot has to
be localized with set_initial_pose before going to a location, navigating to a
pose, navigating through poses, following waypoints, computing a path or
docking, because without map to odom the costmaps are empty and Nav2 does
nothing. A path has to be computed before it can be followed. Goals sent on
/goal_pose reach Nav2 only through exactly one forwarder on /goal_pose_nav:
dock_trigger when docking is enabled, or goal_relay.launch.py in
navigation-only mode, never both. The robot has to be docked before it can be
undocked; a goal sent on /goal_pose while docked undocks the robot first and
then forwards the goal, but the Nav2 actions skip that gate. A map has to be
saved before it can be loaded, and a newly loaded map needs a fresh initial
pose. Only one source may publish /cmd_vel at a time, so teleop and manual
velocity commands stop before any navigation goal is sent, and manual commands
on /cmd_vel_nav never run alongside an active goal. The motors are verified
with the open-loop test, and the battery checked at 25 V or more at rest,
before blaming Nav2 for a robot that does not follow its plan.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs

## Interface reference

Every user-facing ROS 2 interface found in the openamr-platform-sw docs and the
code they describe. Direction is from the point of view of a user or client of
the robot.

### Topics you publish (commands)

| Topic | Type | Consumer | Purpose |
|---|---|---|---|
| `/goal_pose` | `geometry_msgs/msg/PoseStamped` | dock_trigger or goal_pose_relay | Go to a pose (RViz 2D Goal Pose); gated by docking |
| `/goal_pose_nav` | `geometry_msgs/msg/PoseStamped` | bt_navigator | Nav2's real goal input; written only by the single forwarder |
| `/initialpose` | `geometry_msgs/msg/PoseWithCovarianceStamped` | amcl | Set the initial pose (RViz 2D Pose Estimate) |
| `/cmd_vel` | `geometry_msgs/msg/Twist` | Teensy via micro-ROS / Gazebo DiffDrive | Final velocity command; 200 ms watchdog |
| `/cmd_vel_nav` | `geometry_msgs/msg/Twist` | velocity_smoother | Top of the safety chain (controller output) |
| `/dock_trigger` | `std_msgs/msg/Bool` | dock_trigger | `true` starts docking (`false` undocks only if `undock_on_false`) |
| `/undock_robot` | `std_msgs/msg/Bool` | dock_trigger | `true` runs the undock manoeuvre |
| `/debug/openloop` | `geometry_msgs/msg/Vector3` | Teensy firmware | Open-loop PWM motor test (x = PWM) |

### Topics you subscribe to (state and telemetry)

| Topic | Type | Publisher | Purpose |
|---|---|---|---|
| `/amcl_pose` | `geometry_msgs/msg/PoseWithCovarianceStamped` | amcl | Robot pose in the map |
| `/map` | `nav_msgs/msg/OccupancyGrid` | map_server / slam_toolbox | Static map (transient local) |
| `/odom` | `nav_msgs/msg/Odometry` | EKF (real) / Gazebo (sim) | Fused odometry |
| `/odom/unfiltered` | `nav_msgs/msg/Odometry` | Teensy | Raw wheel odometry (real) |
| `/imu/data` (`/imu` in sim) | `sensor_msgs/msg/Imu` | Teensy / Gazebo | IMU, only gyro-Z is used |
| `/scan` | `sensor_msgs/msg/LaserScan` | rplidar / Gazebo | Raw lidar |
| `/scan_filtered` | `sensor_msgs/msg/LaserScan` | scan_body_filter | Lidar minus chassis reflections; feeds costmaps and collision_monitor |
| `/scan_forward` | `sensor_msgs/msg/LaserScan` | dock_trigger | Forward stop-cone used by the docking lidar stop |
| `/cmd_vel_smoothed` | `geometry_msgs/msg/Twist` | velocity_smoother | Clamped command fed to collision_monitor |
| `/collision_monitor_state` | `nav2_msgs/msg/CollisionMonitorState` | collision_monitor | Which polygon/action is engaged |
| `/plan` | `nav_msgs/msg/Path` | planner_server | Global plan |
| `/local_plan` | `nav_msgs/msg/Path` | controller_server | Local trajectory |
| `/global_costmap/costmap` | `nav_msgs/msg/OccupancyGrid` | global_costmap | Global costmap (empty = not localised) |
| `/local_costmap/costmap` | `nav_msgs/msg/OccupancyGrid` | local_costmap | Local costmap |
| `/local_costmap/published_footprint` | `geometry_msgs/msg/PolygonStamped` | local_costmap | Robot footprint |
| `/dock_trigger_status` | `std_msgs/msg/String` | dock_trigger | idle, docking, docked, undocking, failed (2 s heartbeat) |
| `/detected_dock_pose` | `geometry_msgs/msg/PoseStamped` | detected_dock_pose_publisher | Centre-tag pose in map, 10 Hz |
| `/apriltag/detections` | `apriltag_msgs/msg/AprilTagDetectionArray` | apriltag_node | Tag detections (IDs 0, 1, 2) |
| `/docking/debug_markers` | `visualization_msgs/msg/MarkerArray` | dock_trigger | Dock normal and line for RViz |
| `/docking/lateral_error` | `std_msgs/msg/Float32` | dock_trigger | Signed offset from the dock line (m) |
| `/camera/image_raw`, `/camera/camera_info` | `sensor_msgs/msg/Image`, `sensor_msgs/msg/CameraInfo` | camera_ros (real) | Camera (IMX708) |
| `/rgb_image`, `/camera_info`, `/camera_info_synced` | `sensor_msgs/msg/Image`, `sensor_msgs/msg/CameraInfo` | Gazebo / camera_info_sync (sim) | Sim camera and synced intrinsics |
| `/debug/left`, `/debug/right`, `/debug/enc_cal` | firmware telemetry (best effort) | Teensy | Per-wheel rpm and encoder calibration |
| `/tf`, `/tf_static` | `tf2_msgs/msg/TFMessage` | amcl, EKF, static publishers | map to odom to base_link to sensors |
| `/clock` | `rosgraph_msgs/msg/Clock` | Gazebo | Sim time |

### Services

| Service | Type | Server | Purpose |
|---|---|---|---|
| `/lifecycle_manager_localization/is_active` | `std_srvs/srv/Trigger` | lifecycle manager | Localization up? |
| `/lifecycle_manager_navigation/is_active` | `std_srvs/srv/Trigger` | lifecycle manager | Navigation up? |
| `/<node>/get_state`, `/<node>/change_state` | `lifecycle_msgs/srv/GetState`, `ChangeState` | every Nav2 node | Lifecycle (dock_trigger pauses `/collision_monitor` this way) |
| `/navigate_to_pose/_action/cancel_goal` (and every action) | `action_msgs/srv/CancelGoal` | bt_navigator | Cancel navigation |
| `/local_costmap/clear_entirely_local_costmap` | `nav2_msgs/srv/ClearEntireCostmap` | local_costmap | Clear local costmap |
| `/global_costmap/clear_entirely_global_costmap` | `nav2_msgs/srv/ClearEntireCostmap` | global_costmap | Clear global costmap |
| `/map_server/load_map` | `nav2_msgs/srv/LoadMap` | map_server | Swap maps |
| `/slam_toolbox/save_map` | `slam_toolbox/srv/SaveMap` | slam_toolbox | Save a SLAM map |
| `/apriltag/set_enabled` | `std_srvs/srv/SetBool` | apriltag_gate | On-demand tag detection (non-composed path only) |
| `/start_motor`, `/stop_motor` | `std_srvs/srv/Empty` | rplidar | Lidar motor on/off |
| `/dock_trigger/set_parameters` | `rcl_interfaces/srv/SetParameters` | dock_trigger | Live docking tuning (`ros2 param set`) |
| `/amcl/set_parameters` | `rcl_interfaces/srv/SetParameters` | amcl | dock_trigger pauses AMCL updates during docking |
| `/marker_array` | Gazebo marker service (`gz` CLI) | Gazebo | Sim debug markers from dock_trigger |

### Actions

| Action | Type | Server | Purpose |
|---|---|---|---|
| `/navigate_to_pose` | `nav2_msgs/action/NavigateToPose` | bt_navigator | Go to one pose |
| `/navigate_through_poses` | `nav2_msgs/action/NavigateThroughPoses` | bt_navigator | Go through several poses |
| `/follow_waypoints` | `nav2_msgs/action/FollowWaypoints` | waypoint_follower | Visit waypoints, pausing at each |
| `/compute_path_to_pose` | `nav2_msgs/action/ComputePathToPose` | planner_server | Generate a path (GridBased) |
| `/follow_path` | `nav2_msgs/action/FollowPath` | controller_server | Follow a path (FollowPath) |
| `/smooth_path` | `nav2_msgs/action/SmoothPath` | smoother_server | Smooth a path (simple_smoother) |
| `/spin`, `/backup`, `/drive_on_heading`, `/wait`, `/assisted_teleop` | `nav2_msgs/action/Spin`, `BackUp`, `DriveOnHeading`, `Wait`, `AssistedTeleop` | behavior_server | Recovery behaviours |
| `/undock_robot` | `nav2_msgs/action/UndockRobot` | opennav_docking docking_server | Client exists in dock_trigger; docking_server is not launched by default |

### Launch entry points

| Command | Starts |
|---|---|
| `ros2 launch openamrobot_bringup bringup_composed.launch.py map:=<yaml>` | Real robot: drivers, EKF, Nav2, composed camera/AprilTag, docking (the default for camera or docking) |
| `ros2 launch openamrobot_bringup bringup.launch.py map:=<yaml> use_camera:=false use_docking:=false` | Real robot, navigation only |
| `ros2 launch openamrobot_bringup bringup.launch.py sim:=true use_rviz:=true` | Gazebo, Nav2 and docking in one command |
| `ros2 launch openamrobot_bringup real_bringup.launch.py` | Real data sources only (micro-ROS agent, RPLIDAR, scan filter, camera, EKF, static TFs) |
| `ros2 launch openamrobot_gazebo gz_simulator.launch.py gui:=true` | Gazebo world, robot and bridge |
| `ros2 launch openamrobot_nav2 sim_bringup_launch.py use_rviz:=true` | Sim localization, navigation and RViz |
| `ros2 launch openamrobot_nav2 localization_launch.py map:=<yaml>` / `navigation_launch.py` | Nav2 layers individually |
| `ros2 launch openamrobot_nav2 online_async_launch.py` | SLAM Toolbox mapping |
| `ros2 launch openamrobot_bringup goal_relay.launch.py` | `/goal_pose` to `/goal_pose_nav` relay (navigation only) |
| `ros2 launch openamrobot_docking openamrobot_docking.launch.py` / `docking_real.launch.py` | Docking layer (sim / real) |
