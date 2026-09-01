#!/usr/bin/env python3
"""move_group for the Gen3 6DoF + 2F-85, wired for simulated time.

Written instead of reusing kinova_gen3_6dof_robotiq_2f_85_moveit_config/
launch/sim.launch.py, which has two problems for this demo:
  * use_sim_time defaults to False, so move_group's trajectory timing drifts
    against Ignition's clock;
  * move_group itself is gated on `launch_rviz`, so there is no way to run it
    headless.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from moveit_configs_utils import MoveItConfigsBuilder

MOVEIT_PKG = "kinova_gen3_6dof_robotiq_2f_85_moveit_config"


def generate_launch_description():
    launch_rviz = LaunchConfiguration("launch_rviz")

    moveit_config = (
        MoveItConfigsBuilder("gen3", package_name=MOVEIT_PKG)
        .robot_description(
            mappings={
                "robot_ip": "xxx.yyy.zzz.www",
                "use_fake_hardware": "false",
                "gripper": "robotiq_2f_85",
                "dof": "6",
                "sim_ignition": "true",
            }
        )
        .trajectory_execution(file_path="config/moveit_controllers.yaml")
        .planning_scene_monitor(
            publish_robot_description=True, publish_robot_description_semantic=True
        )
        .planning_pipelines(pipelines=["ompl", "pilz_industrial_motion_planner"])
        .to_moveit_configs()
    )

    move_group = Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=[
            moveit_config.to_dict(),
            {"use_sim_time": True},
            # The 2F-85 mimic joints are commanded by gz_ros2_control, not by a
            # ros2_control interface, so MoveIt never sees them and logs a wall
            # of "unknown joint" noise. Keep the log usable without hiding real
            # errors the way upstream's --log-level fatal does.
            {"publish_robot_description_semantic": True},
        ],
        arguments=["--ros-args", "--log-level", "warn"],
    )

    rviz = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="log",
        arguments=[
            "-d",
            str(moveit_config.package_path / "config" / "moveit.rviz"),
        ],
        parameters=[
            moveit_config.robot_description,
            moveit_config.robot_description_semantic,
            moveit_config.robot_description_kinematics,
            moveit_config.planning_pipelines,
            moveit_config.joint_limits,
            {"use_sim_time": True},
        ],
        condition=IfCondition(launch_rviz),
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("launch_rviz", default_value="true"),
            move_group,
            rviz,
        ]
    )
