# OpenAMRobot Cancel Navigation Goal — Robot Skill Rulebook

## Overview

This rulebook describes cancelling the active navigation goal on the
OpenAMRobot through its ROS 2 Jazzy interfaces. Every command assumes
CycloneDDS on domain 0 (RMW_IMPLEMENTATION=rmw_cyclonedds_cpp,
ROS_DOMAIN_ID=0).

## Skill

**cancel_navigation_goal** is a high-level skill. It is composed of the
following primitive actions: start localization, start navigation, and cancel
navigation.

## Objects

The objects involved in this skill are: map and robot.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, navigation active, and
navigation cancelled.

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

## Ordering rules

Start localization must happen before start navigation, because it makes
localization active true. Start navigation must happen before cancel
navigation, because it makes navigation active true.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs
