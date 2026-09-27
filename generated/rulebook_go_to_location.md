# OpenAMRobot Go To Location — Robot Skill Rulebook

## Overview

This rulebook describes going to a location x, y with heading yaw on the map:
bring up, localise, then send the goal pose on the OpenAMRobot through its ROS
2 Jazzy interfaces. Every command assumes CycloneDDS on domain 0
(RMW_IMPLEMENTATION=rmw_cyclonedds_cpp, ROS_DOMAIN_ID=0).

## Skill

**go_to_location** is a high-level skill. It is composed of the following
primitive actions: start localization, start navigation, set initial pose, and
navigate to goal.

## Objects

The objects involved in this skill are: goal pose, map, and robot.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, navigation active,
robot localized, and robot at goal.

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

**navigate_to_goal** — Going to location x, y with heading yaw means publishing
a PoseStamped in the map frame whose position is x, y and whose orientation
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

## Ordering rules

Start localization must happen before start navigation, because it makes
localization active true. Start localization must happen before set initial
pose, because it makes localization active true. Start localization must happen
before set initial pose, because it makes map loaded true. Start navigation
must happen before navigate to goal, because it makes navigation active true.
Set initial pose must happen before navigate to goal, because it makes robot
localized true.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs
