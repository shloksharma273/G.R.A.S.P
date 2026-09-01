cmake_minimum_required(VERSION 3.16)
project(grasp_arm_bringup)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY launch config
  DESTINATION share/${PROJECT_NAME}
)

# worlds/ holds only worlds/generated/, which make_world.py writes and .gitignore
# excludes -- so on a fresh clone the directory does not exist yet and an
# unconditional install(DIRECTORY) would fail the build. setup.sh generates the
# world before building; this keeps a plain `colcon build` working either way.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/worlds")
  install(DIRECTORY worlds DESTINATION share/${PROJECT_NAME})
else()
  message(WARNING
    "grasp_arm_bringup: no worlds/ yet. Run scripts/make_world.py (or setup.sh) "
    "and rebuild, or warehouse_sim.launch.py will not find a world.")
endif()

install(
  PROGRAMS
    scripts/make_world.py
    scripts/pick_place.py
    scripts/task_executor.py
    scripts/calibrate_reach.py
    scripts/plan_bridge.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
