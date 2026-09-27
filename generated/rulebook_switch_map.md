# OpenAMRobot Switch Map — Robot Skill Rulebook

## Overview

This rulebook describes loading a different saved map and localising on it on
the OpenAMRobot through its ROS 2 Jazzy interfaces. Every command assumes
CycloneDDS on domain 0 (RMW_IMPLEMENTATION=rmw_cyclonedds_cpp,
ROS_DOMAIN_ID=0).

## Skill

**switch_map** is a high-level skill. It is composed of the following primitive
actions: start localization, load map, and set initial pose.

## Objects

The objects involved in this skill are: map and robot.

## States

The relevant states of the world are: drivers running, lidar spinning,
localization active, map loaded, docking layer running, and robot localized.

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

**load_map** — The robot swaps the map served on `/map` without relaunching:
`ros2 service call /map_server/load_map nav2_msgs/srv/LoadMap "{map_url:
'/home/<user>/maps/my_map.yaml'}"`. The usual route is passing map:= to the
bring-up, which is mandatory on the real robot. Localise again with
set_initial_pose on the new map. It is executed by calling the service
`/map_server/load_map` of type `nav2_msgs/srv/LoadMap`. This action requires
that localization active is already true as a precondition. After this action,
map loaded is true.

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

## Ordering rules

Start localization must happen before load map, because it makes localization
active true. Start localization must happen before set initial pose, because it
makes localization active true. Load map must happen before set initial pose,
because it makes map loaded true.

## Source

Generated from
https://github.com/openAMRobot/openamr-platform-sw/tree/main/docs
