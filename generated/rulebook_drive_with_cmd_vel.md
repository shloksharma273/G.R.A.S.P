# OpenAMRobot Drive With Cmd Vel — Robot Skill Rulebook

## Overview

This rulebook describes driving the robot directly with a velocity command
(cmd_vel Twist), including stopping with zeros on the OpenAMRobot through its
ROS 2 Jazzy interfaces. Every command assumes CycloneDDS on domain 0
(RMW_IMPLEMENTATION=rmw_cyclonedds_cpp, ROS_DOMAIN_ID=0).

## Skill

**drive_with_cmd_vel** is a high-level skill. It is composed of the following
primitive actions: start localization and drive with velocity.

## Objects

The objects involved in this skill are: map, motors, and robot.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, and robot moving.

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

## Ordering rules

Start localization must happen before drive with velocity, because it makes
drivers running true.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs
