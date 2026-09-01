#!/usr/bin/env python3
"""Kinova Gen3 6DoF + Robotiq 2F-85 in the warehouse world (Ignition Fortress).

Replaces kortex_bringup/kortex_sim_control.launch.py, which cannot do this job:

  * it hardcodes `ign_args: "-r -v 3 empty.sdf"` with no world argument;
  * the deb build never spawns the gripper controller -- the gen3 and gen3_lite
    hand-controller spawners are assigned to the same Python name, so the
    gen3 branch is shadowed and its condition is permanently false
    (fixed upstream on humble, still broken in ros-humble-kortex-bringup 0.2.3);
  * it spawns the arm floating at z=0.3 with nothing under it.

Ignition, not Gazebo Classic: with sim_gazebo:=true the robotiq xacro selects
`robotiq_driver/RobotiqGripperHardwareInterface` -- the real serial driver on
/dev/ttyUSB0 -- because the gripper macro has no sim_gazebo branch at all.
Only the Ignition path gives the gripper a simulated hardware interface.
"""

from pathlib import Path

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    OpaqueFunction,
    RegisterEventHandler,
    SetEnvironmentVariable,
)
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, FindExecutable, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

PKG = "grasp_arm_bringup"
ROBOT_NAME = "gen3"


def launch_setup(context, *args, **kwargs):
    share = Path(get_package_share_directory(PKG))

    world = LaunchConfiguration("world").perform(context)
    if not world:
        world = str(share / "worlds" / "generated" / "warehouse_kinova.sdf")
    if not Path(world).is_file():
        raise RuntimeError(
            f"world not found: {world}\n"
            f"Generate it first:  ros2 run {PKG} make_world.py\n"
            f"(and re-run colcon build, unless you built with --symlink-install)"
        )

    resource_path = LaunchConfiguration("resource_path").perform(context)
    if not resource_path:
        marker = share / "worlds" / "generated" / "resource_path.txt"
        resource_path = marker.read_text().strip() if marker.is_file() else ""

    cfg = yaml.safe_load((share / "config" / "workstation.yaml").read_text())
    sp = cfg["robot"]["spawn_pose"]

    launch_rviz = LaunchConfiguration("launch_rviz")
    launch_moveit = LaunchConfiguration("launch_moveit")
    headless = LaunchConfiguration("headless").perform(context)

    # ---- robot description -------------------------------------------------
    controllers_file = PathJoinSubstitution(
        [FindPackageShare("kortex_description"), "arms/gen3/6dof/config", "ros2_controllers.yaml"]
    )
    robot_description_content = Command(
        [
            PathJoinSubstitution([FindExecutable(name="xacro")]), " ",
            PathJoinSubstitution(
                [FindPackageShare("kortex_description"), "robots", "kinova.urdf.xacro"]
            ), " ",
            "robot_ip:=xxx.yyy.zzz.www", " ",
            "name:=", ROBOT_NAME, " ",
            "arm:=gen3", " ",
            "dof:=6", " ",
            "vision:=false", " ",
            "gripper:=robotiq_2f_85", " ",
            "sim_ignition:=true", " ",
            "sim_gazebo:=false", " ",
            "simulation_controllers:=", controllers_file, " ",
        ]
    )
    robot_description = {"robot_description": robot_description_content.perform(context)}

    robot_state_publisher = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        output="both",
        parameters=[robot_description, {"use_sim_time": True}],
    )

    # ---- Ignition ----------------------------------------------------------
    verbosity = "1" if headless == "true" else "3"
    ign_args = f"-r -v {verbosity} {world}"
    if headless == "true":
        ign_args = f"-s {ign_args}"   # server only, no GUI

    ignition = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [FindPackageShare("ros_gz_sim"), "/launch/gz_sim.launch.py"]
        ),
        launch_arguments={"ign_args": ign_args}.items(),
    )

    spawn_robot = Node(
        package="ros_gz_sim",
        executable="create",
        output="screen",
        arguments=[
            "-string", robot_description_content.perform(context),
            "-name", ROBOT_NAME,
            "-allow_renaming", "true",
            "-x", str(sp["x"]), "-y", str(sp["y"]), "-z", str(sp["z"]),
            "-R", str(sp["roll"]), "-P", str(sp["pitch"]), "-Y", str(sp["yaw"]),
        ],
    )

    clock_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="clock_bridge",
        arguments=["/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock"],
        output="screen",
    )

    # Entity spawn / delete / pose services, so the pick-place scripts can read
    # and reset object state without shelling out to `ign service`.
    world_name = _world_name(world)
    world_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="world_bridge",
        arguments=[
            f"/world/{world_name}/create@ros_gz_interfaces/srv/SpawnEntity",
            f"/world/{world_name}/remove@ros_gz_interfaces/srv/DeleteEntity",
            f"/world/{world_name}/set_pose@ros_gz_interfaces/srv/SetEntityPose",
            # Pose_V -> TFMessage, not PoseArray: PoseArray drops the entity
            # names, and the pick/place acknowledgements are verified by
            # reading named object poses back out of Ignition.
            f"/world/{world_name}/pose/info@tf2_msgs/msg/TFMessage["
            f"ignition.msgs.Pose_V",
        ],
        parameters=[{"use_sim_time": True}],
        output="screen",
    )

    # ---- controllers -------------------------------------------------------
    def spawner(name, *extra):
        return Node(
            package="controller_manager",
            executable="spawner",
            arguments=[name, "-c", "/controller_manager", *extra],
            output="screen",
        )

    jsb = spawner("joint_state_broadcaster")
    arm_controller = spawner("joint_trajectory_controller")
    # THE FIX: upstream never reaches this spawner on a gen3.
    gripper_controller = spawner("robotiq_gripper_controller")
    twist = spawner("twist_controller", "--inactive")

    # Chain them: controller_manager only exists once Ignition has loaded the
    # ign_ros2_control plugin, and stacking four spawners in parallel against a
    # cold controller_manager is how you get flaky "service not available".
    chain = [
        RegisterEventHandler(OnProcessExit(target_action=jsb, on_exit=[arm_controller])),
        RegisterEventHandler(
            OnProcessExit(target_action=arm_controller, on_exit=[gripper_controller])
        ),
        RegisterEventHandler(OnProcessExit(target_action=gripper_controller, on_exit=[twist])),
    ]

    # ---- MoveIt ------------------------------------------------------------
    moveit = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([str(share / "launch" / "moveit.launch.py")]),
        launch_arguments={"launch_rviz": launch_rviz}.items(),
        condition=IfCondition(launch_moveit),
    )

    actions = [
        SetEnvironmentVariable("IGN_GAZEBO_RESOURCE_PATH", resource_path),
        clock_bridge,
        world_bridge,
        robot_state_publisher,
        ignition,
        spawn_robot,
        jsb,
        *chain,
        moveit,
    ]
    return actions


def _world_name(world_file: str) -> str:
    """Ignition topics/services are namespaced by the world's *name*, not its
    filename."""
    import re

    m = re.search(r'<world\s+name=["\']([^"\']+)["\']', Path(world_file).read_text())
    return m.group(1) if m else "warehouse"


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "world", default_value="",
                description="World SDF. Default: this package's generated warehouse world."),
            DeclareLaunchArgument(
                "resource_path", default_value="",
                description="IGN_GAZEBO_RESOURCE_PATH for model:// lookups. Default: the "
                            "path recorded by make_world.py."),
            DeclareLaunchArgument(
                "headless", default_value="false",
                description="Run the Ignition server without the GUI."),
            DeclareLaunchArgument(
                "launch_moveit", default_value="true",
                description="Start move_group."),
            DeclareLaunchArgument(
                "launch_rviz", default_value="true",
                description="Start RViz (requires launch_moveit)."),
            OpaqueFunction(function=launch_setup),
        ]
    )
