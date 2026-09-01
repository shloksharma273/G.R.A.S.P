#!/usr/bin/env python3
"""The arm's skill server: one symbolic pick-and-place task at a time.

This is the only thing that knows how to move the robot. It owns the site map,
the gripper calibration, the waypoint sequence, the occupancy state, and the
measurement that decides whether a task actually succeeded. It knows nothing
about G.R.A.S.P, rulebooks, or "magic sequences" -- callers name an object and
a destination, and get back measured numbers.

Interface:
    action   ~/execute_task   (grasp_arm_msgs/ExecuteTask)
    service  ~/reset_scene    (std_srvs/Trigger)  -- dev utility
    topic    /grasp_arm/events (std_msgs/String, JSON)

An action rather than a service because a task takes about a minute: the caller
needs its goal accepted or rejected up front, phase feedback while it runs, the
ability to cancel, and a result carrying what was measured.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from pathlib import Path

import rclpy
try:
    import yaml
except ModuleNotFoundError as exc:   # pragma: no cover
    raise SystemExit(
        "PyYAML is missing from the interpreter running this node.\n"
        "This usually means a project virtualenv is active: ROS injects rclpy "
        "through PYTHONPATH, which bypasses venv isolation, but PyYAML does "
        "not come that way.\n"
        "Fix it with either:\n"
        "    pip install pyyaml          # into the active venv\n"
        "    deactivate                  # and run ROS commands outside it"
    ) from exc
from ament_index_python.packages import get_package_share_directory
from control_msgs.action import FollowJointTrajectory, GripperCommand
from geometry_msgs.msg import Pose
from grasp_arm_msgs.action import ExecuteTask
from moveit_msgs.srv import GetPositionIK
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from ros_gz_interfaces.msg import Entity
from ros_gz_interfaces.srv import SetEntityPose
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import Trigger
from tf2_msgs.msg import TFMessage
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

ARM_JOINTS = [f"joint_{i}" for i in range(1, 7)]
TWO_PI = 2.0 * math.pi
# Only these three are continuous. Wrapping a LIMITED joint by 2*pi yields a
# value outside its range, which the trajectory controller rejects outright --
# that is a MOVE_REJECTED, not an IK failure.
CONTINUOUS = {"joint_1", "joint_4", "joint_6"}


def _nearest_branch(joint: str, value: float, reference: float) -> float:
    """Same angle, expressed closest to `reference` -- continuous joints only."""
    if joint not in CONTINUOUS:
        return value
    while value - reference > math.pi:
        value -= TWO_PI
    while reference - value > math.pi:
        value += TWO_PI
    return value

PKG = "grasp_arm_bringup"
PHASES = ["opening", "approaching", "grasping", "lifting", "verifying",
          "transiting", "releasing", "retreating"]


class TaskFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class TaskExecutor(Node):
    def __init__(self, world: str):
        super().__init__("task_executor")
        share = Path(get_package_share_directory(PKG))
        self.cfg = yaml.safe_load((share / "config" / "workstation.yaml").read_text())
        self.g = self.cfg["gripper"]
        self.m = self.cfg["motion"]
        self.v = self.cfg["verify"]
        self.sites = self.cfg["sites"]
        b = self.cfg["robot"]["spawn_pose"]
        self.base = (float(b["x"]), float(b["y"]), float(b["z"]))
        self.pad_top = float(self.cfg["table"]["top_height"]) + float(
            self.cfg["place_pad"]["size"][2]
        )
        self.objects = {o["name"]: o for o in self.cfg["objects"]}

        # place_id -> object_id currently resting there. Seeded empty: the
        # blocks start in staging, not on the board.
        self.occupancy: dict[str, str | None] = {p: None for p in self.sites["places"]}
        self._busy = threading.Lock()

        io = ReentrantCallbackGroup()          # must stay serviceable while a task runs
        srv = MutuallyExclusiveCallbackGroup()  # one task at a time

        self.events = self.create_publisher(String, "/grasp_arm/events", 10)
        self.poses: dict[str, tuple[float, float, float]] = {}
        self.create_subscription(TFMessage, f"/world/{world}/pose/info",
                                 self._on_poses, 10, callback_group=io)
        self.js: JointState | None = None
        self.create_subscription(JointState, "/joint_states",
                                 lambda msg: setattr(self, "js", msg), 10,
                                 callback_group=io)

        self.ik = self.create_client(GetPositionIK, "/compute_ik", callback_group=io)
        self.set_pose = self.create_client(SetEntityPose, f"/world/{world}/set_pose",
                                           callback_group=io)
        self.arm = ActionClient(self, FollowJointTrajectory,
                                "/joint_trajectory_controller/follow_joint_trajectory",
                                callback_group=io)
        self.grip = ActionClient(self, GripperCommand, self.g["action"],
                                 callback_group=io)

        self.server = ActionServer(
            self, ExecuteTask, "~/execute_task",
            execute_callback=self._execute,
            goal_callback=self._accept,
            cancel_callback=lambda _g: CancelResponse.ACCEPT,
            callback_group=srv,
        )
        self.create_service(Trigger, "~/reset_scene", self._reset_scene,
                            callback_group=srv)
        self.get_logger().info("task_executor ready, waiting for the robot stack")

    # ------------------------------------------------------------- plumbing
    def _on_poses(self, msg: TFMessage):
        for t in msg.transforms:
            tr = t.transform.translation
            self.poses[t.child_frame_id] = (tr.x, tr.y, tr.z)

    def wait_ready(self, timeout=90.0):
        """Block until the robot stack is actually up."""
        for what, ok in (
            ("/compute_ik", lambda: self.ik.wait_for_service(timeout_sec=timeout)),
            ("arm action", lambda: self.arm.wait_for_server(timeout_sec=timeout)),
            ("gripper action", lambda: self.grip.wait_for_server(timeout_sec=timeout)),
        ):
            if not ok():
                raise RuntimeError(f"{what} never appeared -- is the sim running?")
        t0 = time.time()
        while (self.js is None or not self.poses) and time.time() - t0 < timeout:
            time.sleep(0.2)
        if self.js is None or not self.poses:
            raise RuntimeError("no joint states / pose feed")
        self.get_logger().info("robot stack up; accepting tasks")

    def _wait(self, future, timeout: float):
        """Wait on a future from inside a callback.

        spin_until_future_complete would re-enter the executor from a thread it
        already owns; this lets the other executor threads deliver the result.
        """
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        if not done.wait(timeout):
            return None
        return future.result()

    def emit(self, event: str, **fields):
        payload = {"event": event, "stamp": time.time(), **fields}
        self.events.publish(String(data=json.dumps(payload)))
        detail = "  ".join(
            f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in fields.items()
        )
        self.get_logger().info(f"[{event}] {detail}")

    # ---------------------------------------------------------------- motion
    def world_to_base(self, x, y, z):
        return x - self.base[0], y - self.base[1], z - self.base[2]

    def q_for_width(self, width_m, squeeze=None):
        sq = self.g["squeeze"] if squeeze is None else float(squeeze)
        q = (self.g["gap_at_zero_mm"] - width_m * 1000.0) / self.g["mm_per_rad"] + sq
        return max(self.g["open_position"], min(0.75, q))

    def solve_ik(self, x, y, z, seed=None, tries=None, max_delta=None):
        tries = int(self.m.get("ik_tries", 12)) if tries is None else tries
        reference = list(seed) if seed else [
            self.js.position[self.js.name.index(j)] for j in ARM_JOINTS
        ]
        best, best_cost = None, float("inf")
        for attempt in range(tries):
            req = GetPositionIK.Request()
            r = req.ik_request
            r.group_name = "manipulator"
            r.ik_link_name = "end_effector_link"
            r.avoid_collisions = False
            r.timeout.nanosec = 50_000_000
            r.pose_stamped.header.frame_id = "base_link"
            r.pose_stamped.pose.position.x = float(x)
            r.pose_stamped.pose.position.y = float(y)
            r.pose_stamped.pose.position.z = float(z)
            r.pose_stamped.pose.orientation.x = 1.0
            r.pose_stamped.pose.orientation.w = 0.0
            r.robot_state.joint_state.name = list(self.js.name)
            r.robot_state.joint_state.position = list(self.js.position)
            start = list(seed) if seed else reference
            # KDL is a deterministic Newton solve from the seed, so retrying
            # with the SAME seed re-runs the same failure. Random restarts after
            # the first attempt are what actually buy coverage here.
            if attempt:
                start = [v + random.uniform(-0.6, 0.6) for v in start]
            for j, val in zip(ARM_JOINTS, start):
                r.robot_state.joint_state.position[
                    r.robot_state.joint_state.name.index(j)] = val
            res = self._wait(self.ik.call_async(req), 20.0)
            if res is None or res.error_code.val != 1:
                continue
            sol = dict(zip(res.solution.joint_state.name, res.solution.joint_state.position))
            # joint_1/4/6 are continuous, so IK may hand back a value a full
            # turn away -- kinematically identical, but the controller would
            # then swing the joint all the way round. Pull each onto the branch
            # nearest where the arm already is.
            q = [_nearest_branch(j, sol[j], ref) for j, ref in zip(ARM_JOINTS, reference)]
            bad = self.out_of_limits(q)
            if bad:
                # Sending this would be rejected by the controller as an
                # unreachable goal, which surfaces as a confusing
                # MOVE_REJECTED. Discard it here instead.
                continue
            cost = max(abs(a - b) for a, b in zip(q, reference))
            if cost < best_cost:
                best, best_cost = q, cost
            if best_cost < 0.35:
                break
        # Random restarts find solutions the exact seed misses, but they can
        # also land on a far-away configuration. For a fine waypoint -- the
        # descent onto a block, the lift, the release -- a far solution means
        # the arm sweeps sideways through what it is trying to grasp, so refuse
        # it and report an honest failure instead.
        if max_delta is not None and best is not None and best_cost > max_delta:
            return None
        return best

    def joint_error(self, q):
        """Largest per-joint distance between the arm and a target, or None."""
        if self.js is None:
            return None
        try:
            actual = [self.js.position[self.js.name.index(j)] for j in ARM_JOINTS]
        except ValueError:
            return None
        return max(abs(a - b) for a, b in zip(actual, q))

    def out_of_limits(self, q):
        """Joints whose target is outside the URDF position limits, if any."""
        limits = self.m.get("joint_position_limits", {})
        bad = []
        for joint, value in zip(ARM_JOINTS, q):
            span = limits.get(joint)
            if span and not (float(span[0]) <= value <= float(span[1])):
                bad.append(f"{joint}={value:.3f} outside [{span[0]}, {span[1]}]")
        return bad

    def move_joints(self, q, secs):
        current = [self.js.position[self.js.name.index(j)] for j in ARM_JOINTS]
        delta = max(abs(a - b) for a, b in zip(q, current))
        secs = max(float(secs), delta / float(self.m["max_joint_speed"]))
        goal = FollowJointTrajectory.Goal()
        goal.trajectory = JointTrajectory(joint_names=ARM_JOINTS)
        pt = JointTrajectoryPoint()
        pt.positions = [float(v) for v in q]
        pt.time_from_start.sec = int(secs)
        pt.time_from_start.nanosec = int((secs % 1) * 1e9)
        goal.trajectory.points = [pt]
        bad = self.out_of_limits(q)
        if bad:
            self.get_logger().error(f"refusing move, out of limits: {'; '.join(bad)}")
            return False
        gh = self._wait(self.arm.send_goal_async(goal), 20.0)
        if gh is None:
            self.get_logger().error("arm action: no response to the goal")
            return False
        if not gh.accepted:
            self.get_logger().error(f"arm action rejected the goal "
                                    f"(target={[round(v, 3) for v in q]}, {secs:.1f}s)")
            return False
        budget = secs + float(self.m.get("move_result_grace", 60.0))
        res = self._wait(gh.get_result_async(), budget)
        if res is None:
            # The controller does not always report completion: with
            # goal_time=0 and stopped_velocity_tolerance=0 in the kortex
            # controller config it can sit waiting on tolerances it never
            # declares met. Judge the move by where the arm actually is.
            reached = self.joint_error(q)
            if reached is not None and reached <= float(self.m.get("move_reached_tol", 0.05)):
                self.get_logger().warn(
                    f"arm action gave no result in {budget:.0f}s, but the arm is "
                    f"within {reached:.3f} rad of the target -- continuing")
                return True
            self.get_logger().error(
                f"arm action timed out after {budget:.0f}s"
                + (f", still {reached:.3f} rad from the target" if reached is not None else ""))
            return False
        if res.result.error_code != 0:
            self.get_logger().error(
                f"arm action failed: error_code={res.result.error_code} "
                f"{res.result.error_string}")
            return False
        return True

    def set_gripper(self, q):
        goal = GripperCommand.Goal()
        goal.command.position = float(q)
        goal.command.max_effort = float(self.g["max_effort"])
        gh = self._wait(self.grip.send_goal_async(goal), 20.0)
        if gh is None or not gh.accepted:
            return None
        res = self._wait(gh.get_result_async(), 40.0)
        return res.result if res else None

    def settle(self, secs=None):
        time.sleep(self.m["settle_secs"] if secs is None else secs)

    def go_home(self):
        return self.move_joints(self.m["home"], self.m["move_secs"])

    # ------------------------------------------------------------ validation
    def check_task(self, object_id: str, place_id: str):
        if object_id not in self.objects:
            raise TaskFailure("UNKNOWN_OBJECT", f"no object called {object_id!r}")
        if place_id not in self.sites["places"]:
            raise TaskFailure("UNKNOWN_PLACE", f"no place called {place_id!r}")
        holder = self.occupancy.get(place_id)
        if holder not in (None, object_id):
            raise TaskFailure("PLACE_OCCUPIED", f"{place_id} already holds {holder}")

        # Clearance against points that are actually occupied -- not against all
        # six, because the triangle and the line are never built together.
        target = self.sites["places"][place_id]
        floor = float(self.sites["clearance_min"])
        for other, held in self.occupancy.items():
            if held is None or other == place_id:
                continue
            gap = math.dist(target, self.sites["places"][other])
            if gap < floor:
                raise TaskFailure(
                    "NO_CLEARANCE",
                    f"{place_id} is {gap*100:.0f} cm from {other} which holds {held}; "
                    f"minimum is {floor*100:.0f} cm",
                )

    # ------------------------------------------------------------- the skill
    def _accept(self, goal):
        if self._busy.locked():
            self.get_logger().warn("task rejected: already executing one")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _feedback(self, handle, phase):
        fb = ExecuteTask.Feedback()
        fb.phase = phase
        fb.phase_index = PHASES.index(phase) + 1
        fb.phase_total = len(PHASES)
        handle.publish_feedback(fb)

    def _execute(self, handle):
        goal = handle.request
        result = ExecuteTask.Result()
        with self._busy:
            try:
                self._run(handle, goal.object_id, goal.place_id, result)
                result.success = True
                result.message = f"{goal.object_id} placed at {goal.place_id}"
                self.occupancy[goal.place_id] = goal.object_id
                handle.succeed()
            except TaskFailure as exc:
                result.success = False
                result.failure_code = exc.code
                result.message = exc.message
                self.emit("task_failed", object=goal.object_id, place=goal.place_id,
                          code=exc.code, reason=exc.message)
                handle.abort()
            return result

    def _run(self, handle, object_id, place_id, result):
        self.check_task(object_id, place_id)
        obj = self.objects[object_id]
        width = float(obj["size"][0])
        half_h = float(obj["size"][2]) / 2.0
        tx, ty = self.sites["places"][place_id]

        self.emit("task_started", object=object_id, place=place_id)

        self._feedback(handle, "opening")
        self.set_gripper(self.g["open_position"])
        self.settle(0.3)

        start = self.poses.get(object_id)
        if start is None:
            raise TaskFailure("UNKNOWN_OBJECT", f"{object_id} is not in the scene")
        cx, cy, cz = self.world_to_base(*start)
        grasp_z = cz + self.g["tcp_offset_z"]

        self._feedback(handle, "approaching")
        # Try progressively lower approach heights. The tall approach pose over
        # a block near the inner edge of the workspace is the first thing to go
        # unsolvable, and a shorter one is still well clear of a 55 mm block.
        q_above = None
        for height in self.m["approach_heights"]:
            q_above = self.solve_ik(cx, cy, grasp_z + height)
            if q_above is not None:
                break
        if q_above is None:
            raise TaskFailure("IK_FAILED", f"no approach pose above {object_id}")
        if not self.move_joints(q_above, self.m["move_secs"]):
            raise TaskFailure("MOVE_REJECTED", f"approach move to {object_id} failed")

        q_grasp = self.solve_ik(cx, cy, grasp_z, seed=q_above, max_delta=self.m["fine_max_delta"])
        if q_grasp is None:
            raise TaskFailure("IK_FAILED", f"no grasp pose at {object_id}")

        self._feedback(handle, "grasping")
        attempts = int(self.m.get("pick_retries", 0)) + 1
        step = float(self.m.get("retry_squeeze_step", 0.02))
        base_sq = obj.get("squeeze", self.g["squeeze"])
        rise, q_lift = 0.0, None
        for attempt in range(attempts):
            if not self.move_joints(q_grasp, self.m["fine_secs"]):
                raise TaskFailure("MOVE_REJECTED", f"descent onto {object_id} failed")
            q_close = self.q_for_width(width, base_sq + attempt * step)
            closed = self.set_gripper(q_close)
            result.grasp_attempts = attempt + 1

            self._feedback(handle, "lifting")
            q_lift = self.solve_ik(cx, cy, grasp_z + self.m["lift_height"],
                                   seed=q_grasp, max_delta=self.m["fine_max_delta"])
            self.move_joints(q_lift or q_above, self.m["fine_secs"])

            self._feedback(handle, "verifying")
            self.settle()
            rise = self.poses.get(object_id, start)[2] - start[2]
            result.pick_rise = rise
            if rise >= self.v["pick_min_rise"]:
                break
            if attempt + 1 < attempts:
                self.emit("pick_retry", object=object_id, attempt=attempt + 1, rise_m=rise)
                self.set_gripper(self.g["open_position"])
        if rise < self.v["pick_min_rise"]:
            self.set_gripper(self.g["open_position"])
            raise TaskFailure("PICK_NOT_VERIFIED",
                              f"{object_id} rose only {rise*100:.1f} cm")
        self.emit("picked", object=object_id, rise_m=rise, attempts=result.grasp_attempts)

        self._feedback(handle, "transiting")
        release_world = self.pad_top + half_h + self.m["release_gap"]
        px, py, pz = self.world_to_base(tx, ty, release_world)
        pz += self.g["tcp_offset_z"]
        q_above_place = None
        for height in self.m["approach_heights"]:
            q_above_place = self.solve_ik(px, py, pz + height, seed=q_lift or q_above)
            if q_above_place is not None:
                break
        if q_above_place is None:
            raise TaskFailure("IK_FAILED", f"cannot reach above {place_id}")
        if not self.move_joints(q_above_place, self.m["move_secs"]):
            raise TaskFailure("MOVE_REJECTED", f"transit move to {place_id} failed")

        self._feedback(handle, "releasing")
        q_place = self.solve_ik(px, py, pz, seed=q_above_place, max_delta=self.m["fine_max_delta"])
        self.move_joints(q_place or q_above_place, self.m["fine_secs"])
        self.set_gripper(self.g["open_position"])
        self.settle()

        self._feedback(handle, "retreating")
        self.move_joints(q_above_place, self.m["fine_secs"])
        self.settle()

        final = self.poses.get(object_id, (0.0, 0.0, 0.0))
        dxy = math.dist((final[0], final[1]), (tx, ty))
        dz = abs(final[2] - (self.pad_top + half_h))
        result.place_xy_error = dxy
        result.place_z_error = dz
        if dxy > self.v["place_xy_tol"] or dz > self.v["place_z_tol"]:
            raise TaskFailure(
                "PLACE_NOT_VERIFIED",
                f"{object_id} is {dxy*100:.1f} cm from {place_id} "
                f"and {dz*100:.1f} cm off the expected height",
            )
        self.emit("placed", object=object_id, place=place_id,
                  xy_error_m=dxy, z_error_m=dz)
        self.go_home()

    # ----------------------------------------------------------------- reset
    def _reset_scene(self, _req, response):
        if not self.set_pose.wait_for_service(timeout_sec=10.0):
            response.success = False
            response.message = "set_pose service unavailable"
            return response
        self.set_gripper(self.g["open_position"])
        self.go_home()
        moved = []
        for obj in self.cfg["objects"]:
            req = SetEntityPose.Request()
            req.entity = Entity(name=obj["name"], type=Entity.MODEL)
            p = Pose()
            p.position.x, p.position.y, p.position.z = [float(v) for v in obj["pose"]]
            p.orientation.w = 1.0
            req.pose = p
            res = self._wait(self.set_pose.call_async(req), 20.0)
            if res and res.success:
                moved.append(obj["name"])
        self.occupancy = {p: None for p in self.sites["places"]}
        self.settle(1.0)
        response.success = len(moved) == len(self.cfg["objects"])
        response.message = f"reset {', '.join(moved)}; occupancy cleared"
        self.emit("scene_reset", objects=moved)
        return response


def main():
    rclpy.init()
    node = TaskExecutor("warehouse")
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    try:
        node.wait_ready()
        node.go_home()
        node.set_gripper(node.g["open_position"])
        node.get_logger().info("homed; idle")
        spinner.join()
    except KeyboardInterrupt:
        pass
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main()
