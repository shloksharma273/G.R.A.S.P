# OpenAMRobot Drive Planned Path — Robot Skill Rulebook

## Overview

This rulebook describes generating a global path to a location and then driving
along it with the controller on the OpenAMRobot through its ROS 2 Jazzy
interfaces. Every command assumes CycloneDDS on domain 0
(RMW_IMPLEMENTATION=rmw_cyclonedds_cpp, ROS_DOMAIN_ID=0).

## Skill

**drive_planned_path** is a high-level skill. It is composed of the following
primitive actions: start localization, start navigation, set initial pose,
compute path to pose, and follow path.

## Objects

The objects involved in this skill are: global costmap, local costmap, map,
path, and robot.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, navigation active,
robot localized, path computed, and robot at goal.

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

## Ordering rules

Start localization must happen before start navigation, because it makes
localization active true. Start localization must happen before set initial
pose, because it makes localization active true. Start localization must happen
before set initial pose, because it makes map loaded true. Start navigation
must happen before compute path to pose, because it makes navigation active
true. Set initial pose must happen before compute path to pose, because it
makes robot localized true. Compute path to pose must happen before follow
path, because it makes path computed true.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs
