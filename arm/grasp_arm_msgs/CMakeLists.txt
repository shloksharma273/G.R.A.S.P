cmake_minimum_required(VERSION 3.16)
project(grasp_arm_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/ExecuteTask.action"
  DEPENDENCIES action_msgs
)

ament_package()
