# OpenAMRobot Undock From Charger — Robot Skill Rulebook

## Overview

This rulebook describes leaving the charging dock on the OpenAMRobot through
its ROS 2 Jazzy interfaces. Every command assumes CycloneDDS on domain 0
(RMW_IMPLEMENTATION=rmw_cyclonedds_cpp, ROS_DOMAIN_ID=0).

## Skill

**undock_from_charger** is a high-level skill. It is composed of the following
primitive actions: start localization and undock robot.

## Objects

The objects involved in this skill are: dock, map, and robot.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, and robot undocked.

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

**undock_robot** — The robot leaves the dock by reversing 0.7 m at 0.10 m/s in
the odom frame and then spinning about 180 degrees: `ros2 topic pub --once
/undock_robot std_msgs/msg/Bool "{data: true}"`. `/dock_trigger_status` reports
undocking, then idle on success. Sending a goal on `/goal_pose` while docked
triggers this same undock automatically ahead of forwarding the goal. It is
executed by publishing to the topic `/undock_robot` of type
`std_msgs/msg/Bool`. This action requires that docking layer running is already
true as a precondition. After this action, robot undocked is true.

## Ordering rules

Start localization must happen before undock robot, because it makes docking
layer running true.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs
