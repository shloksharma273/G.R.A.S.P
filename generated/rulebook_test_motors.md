# OpenAMRobot Test Motors — Robot Skill Rulebook

## Overview

This rulebook describes checking the motors respond with an open-loop PWM test,
independent of Nav2 on the OpenAMRobot through its ROS 2 Jazzy interfaces.
Every command assumes CycloneDDS on domain 0
(RMW_IMPLEMENTATION=rmw_cyclonedds_cpp, ROS_DOMAIN_ID=0).

## Skill

**test_motors** is a high-level skill. It is composed of the following
primitive actions: start localization and run open loop motor test.

## Objects

The objects involved in this skill are: map, motors, and robot.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, and motors verified.

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

Start localization must happen before run open loop motor test, because it
makes drivers running true.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs
