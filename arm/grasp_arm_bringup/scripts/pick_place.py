#!/usr/bin/env python3
"""Pick and place the workstation objects, acknowledging every pick and drop.

Acknowledgements are *verified*, not asserted: after each lift and each release
the object's real pose is read back out of Ignition and checked before the
event is emitted. A pick that slipped reports pick_failed, not picked.

Events go to /grasp_arm/events as JSON (std_msgs/String) and to stdout.

    ros2 run grasp_arm_bringup pick_place.py                # all objects
    ros2 run grasp_arm_bringup pick_place.py --only red_cube
    ros2 run grasp_arm_bringup pick_place.py --reset        # objects back to start

Motion uses MoveIt's /compute_ik for the kinematics and executes joint
waypoints on joint_trajectory_controller directly. That is deliberate: the
waypoint sequence (approach from above, descend, lift straight up) is safe for
this layout, and it keeps the demo off MoveIt's Cartesian planner, which needs
the table and objects mirrored into the planning scene to be trustworthy.
Collision-aware planning through /move_action is the natural next step.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from control_msgs.action import FollowJointTrajectory, GripperCommand
from geometry_msgs.msg import Pose
from moveit_msgs.srv import GetPositionIK
from rclpy.action import ActionClient
from rclpy.node import Node
from ros_gz_interfaces.msg import Entity
from ros_gz_interfaces.srv import SetEntityPose
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

ARM_JOINTS = [f"joint_{i}" for i in range(1, 7)]
PKG = "grasp_arm_bringup"


class PickPlace(Node):
    def __init__(self, world: str):
        super().__init__("pick_place")
        share = Path(get_package_share_directory(PKG))
        self.cfg = yaml.safe_load((share / "config" / "workstation.yaml").read_text())
        self.g = self.cfg["gripper"]
        self.m = self.cfg["motion"]
        self.v = self.cfg["verify"]
        b = self.cfg["robot"]["spawn_pose"]
        self.base = (float(b["x"]), float(b["y"]), float(b["z"]))
        self.pad_top = float(self.cfg["table"]["top_height"]) + float(
            self.cfg["place_pad"]["size"][2]
        )

        self.events = self.create_publisher(String, "/grasp_arm/events", 10)

        # ground truth object poses, straight from Ignition
        self.poses: dict[str, tuple[float, float, float]] = {}
        self.create_subscription(
            TFMessage, f"/world/{world}/pose/info", self._on_poses, 10
        )
        self.js: JointState | None = None
        self.create_subscription(
            JointState, "/joint_states", lambda msg: setattr(self, "js", msg), 10
        )

        self.ik = self.create_client(GetPositionIK, "/compute_ik")
        self.set_pose = self.create_client(SetEntityPose, f"/world/{world}/set_pose")
        self.arm = ActionClient(
            self, FollowJointTrajectory,
            "/joint_trajectory_controller/follow_joint_trajectory",
        )
        self.grip = ActionClient(self, GripperCommand, self.g["action"])

        self._require(self.ik.wait_for_service(timeout_sec=30), "/compute_ik")
        self._require(self.arm.wait_for_server(timeout_sec=30), "arm action server")
        self._require(self.grip.wait_for_server(timeout_sec=30), "gripper action server")
        self._wait_for(lambda: self.js is not None, "joint states")
        self._wait_for(lambda: bool(self.poses), "Ignition pose feed")

    # ---------------------------------------------------------------- plumbing
    def _require(self, ok, what):
        if not ok:
            raise RuntimeError(f"{what} not available -- is the sim running?")

    def _wait_for(self, pred, what, timeout=30.0):
        t0 = time.time()
        while not pred():
            if time.time() - t0 > timeout:
                raise RuntimeError(f"timed out waiting for {what}")
            rclpy.spin_once(self, timeout_sec=0.2)

    def _on_poses(self, msg: TFMessage):
        for t in msg.transforms:
            tr = t.transform.translation
            self.poses[t.child_frame_id] = (tr.x, tr.y, tr.z)

    def _spin(self, fut, timeout=90.0):
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout)
        return fut.result()

    def refresh(self, settle: float | None = None):
        """Pump callbacks so self.poses reflects the present, not the past."""
        dur = self.m["settle_secs"] if settle is None else settle
        t0 = time.time()
        while time.time() - t0 < dur:
            rclpy.spin_once(self, timeout_sec=0.1)

    def emit(self, event: str, **fields):
        payload = {"event": event, "stamp": time.time(), **fields}
        self.events.publish(String(data=json.dumps(payload)))
        detail = "  ".join(
            f"{k}={round(v, 4) if isinstance(v, float) else v}"
            for k, v in fields.items()
        )
        print(f"  [ACK] {event:14s} {detail}", flush=True)

    # ------------------------------------------------------------------ motion
    def world_to_base(self, x, y, z):
        return x - self.base[0], y - self.base[1], z - self.base[2]

    def q_for_width(self, width_m: float, squeeze: float | None = None) -> float:
        """Knuckle angle that closes on an object this wide, plus a squeeze.

        The gripper stalls against the object, so a larger squeeze buys grip
        force rather than crushing anything -- a smooth cylinder needs much
        more of it than a cube, because it contacts along a line instead of a
        face and slips out at the squeeze that holds a cube fine.
        """
        mm = width_m * 1000.0
        sq = self.g["squeeze"] if squeeze is None else float(squeeze)
        q = (self.g["gap_at_zero_mm"] - mm) / self.g["mm_per_rad"] + sq
        return max(self.g["open_position"], min(0.75, q))

    def solve_ik(self, x, y, z, seed=None, tries=4):
        """IK in base_link with the gripper pointing straight down.

        /compute_ik seeds from a random state on each call and will happily
        return a solution on a different wrist branch. Executing that as a
        single waypoint slews several joints at once and flings whatever is in
        the gripper across the room -- which is exactly how the first blue_can
        run ended, 1.2 m away on the floor. So sample a few solutions and keep
        the one closest to where the arm already is.
        """
        reference = list(seed) if seed else [
            self.js.position[self.js.name.index(j)] for j in ARM_JOINTS
        ]
        best, best_cost = None, float("inf")
        for _ in range(tries):
            req = GetPositionIK.Request()
            r = req.ik_request
            r.group_name = "manipulator"
            r.ik_link_name = "end_effector_link"
            r.avoid_collisions = False
            r.timeout.sec = 2
            r.pose_stamped.header.frame_id = "base_link"
            r.pose_stamped.pose.position.x = float(x)
            r.pose_stamped.pose.position.y = float(y)
            r.pose_stamped.pose.position.z = float(z)
            r.pose_stamped.pose.orientation.x = 1.0   # 180 deg about x: +z down
            r.pose_stamped.pose.orientation.w = 0.0
            r.robot_state.joint_state.name = list(self.js.name)
            r.robot_state.joint_state.position = list(self.js.position)
            if seed:
                for j, val in zip(ARM_JOINTS, seed):
                    r.robot_state.joint_state.position[
                        r.robot_state.joint_state.name.index(j)
                    ] = val

            res = self._spin(self.ik.call_async(req), timeout=30.0)
            if res is None or res.error_code.val != 1:
                continue
            sol = dict(
                zip(res.solution.joint_state.name, res.solution.joint_state.position)
            )
            q = [sol[j] for j in ARM_JOINTS]
            cost = max(abs(a - b) for a, b in zip(q, reference))
            if cost < best_cost:
                best, best_cost = q, cost
            if best_cost < 0.35:      # close enough to the current pose; stop early
                break
        return best

    def move_joints(self, q, secs):
        """Execute a joint waypoint, stretching the duration so no joint has to
        exceed max_joint_speed. A 5 s move is gentle for a 0.3 rad step and
        violent for a 3 rad one, and the payload only survives the gentle
        version."""
        current = [self.js.position[self.js.name.index(j)] for j in ARM_JOINTS]
        delta = max(abs(a - b) for a, b in zip(q, current))
        needed = delta / float(self.m["max_joint_speed"])
        secs = max(float(secs), needed)

        goal = FollowJointTrajectory.Goal()
        goal.trajectory = JointTrajectory(joint_names=ARM_JOINTS)
        pt = JointTrajectoryPoint()
        pt.positions = [float(v) for v in q]
        pt.time_from_start.sec = int(secs)
        pt.time_from_start.nanosec = int((secs % 1) * 1e9)
        goal.trajectory.points = [pt]
        gh = self._spin(self.arm.send_goal_async(goal), timeout=30.0)
        if gh is None or not gh.accepted:
            return False
        res = self._spin(gh.get_result_async(), timeout=secs + 30)
        return res is not None and res.result.error_code == 0

    def set_gripper(self, q):
        goal = GripperCommand.Goal()
        goal.command.position = float(q)
        goal.command.max_effort = float(self.g["max_effort"])
        gh = self._spin(self.grip.send_goal_async(goal), timeout=30.0)
        if gh is None or not gh.accepted:
            return None
        res = self._spin(gh.get_result_async(), timeout=40.0)
        return res.result if res else None

    def go_home(self):
        return self.move_joints(self.m["home"], self.m["move_secs"])

    # ------------------------------------------------------------------- cycle
    @staticmethod
    def grasp_width(obj) -> float:
        return (
            float(obj["size"][0]) if obj["type"] == "box"
            else 2.0 * float(obj["radius"])
        )

    @staticmethod
    def half_height(obj) -> float:
        return (
            float(obj["size"][2]) / 2.0 if obj["type"] == "box"
            else float(obj["length"]) / 2.0
        )

    def reset_objects(self):
        self._require(self.set_pose.wait_for_service(timeout_sec=20), "set_pose service")
        # Release first: teleporting an object that is still clamped in the
        # fingers just drags it back out again.
        self.set_gripper(self.g["open_position"])
        self.go_home()
        for obj in self.cfg["objects"]:
            req = SetEntityPose.Request()
            req.entity = Entity(name=obj["name"], type=Entity.MODEL)
            p = Pose()
            p.position.x, p.position.y, p.position.z = [float(v) for v in obj["pose"]]
            p.orientation.w = 1.0
            req.pose = p
            res = self._spin(self.set_pose.call_async(req), timeout=30.0)
            ok = bool(res and res.success)
            print(f"  reset {obj['name']:11s} -> {'ok' if ok else 'FAILED'}")
        self.refresh(1.0)

    def run_object(self, obj) -> bool:
        name = obj["name"]
        width = self.grasp_width(obj)
        hh = self.half_height(obj)
        slot = self.cfg["place_pad"]["slots"][name]

        print(f"\n=== {name} (width {width*1000:.0f} mm) ===", flush=True)
        self.refresh(0.4)
        start = self.poses.get(name)
        if start is None:
            self.emit("pick_failed", object=name, reason="object not visible in sim")
            return False

        cx, cy, cz = self.world_to_base(*start)
        grasp_z = cz + self.g["tcp_offset_z"]
        above_z = grasp_z + self.m["approach_height"]

        # --- pick ---------------------------------------------------------
        self.set_gripper(self.g["open_position"])

        q_above = self.solve_ik(cx, cy, above_z)
        if q_above is None:
            self.emit("pick_failed", object=name, reason="IK failed at approach pose")
            return False
        if not self.move_joints(q_above, self.m["move_secs"]):
            self.emit("pick_failed", object=name, reason="approach move rejected")
            return False

        q_grasp = self.solve_ik(cx, cy, grasp_z, seed=q_above)
        if q_grasp is None:
            self.emit("pick_failed", object=name, reason="IK failed at grasp pose")
            return False
        self.move_joints(q_grasp, self.m["fine_secs"])

        base_squeeze = obj.get("squeeze", self.g["squeeze"])
        attempts = int(self.m.get("pick_retries", 0)) + 1
        step = float(self.m.get("retry_squeeze_step", 0.04))

        rise, q_close, closed_at, q_lift = 0.0, 0.0, float("nan"), None
        for attempt in range(attempts):
            q_close = self.q_for_width(width, base_squeeze + attempt * step)
            result = self.set_gripper(q_close)
            closed_at = result.position if result else float("nan")

            q_lift = self.solve_ik(cx, cy, grasp_z + self.m["lift_height"], seed=q_grasp)
            self.move_joints(q_lift or q_above, self.m["fine_secs"])
            self.refresh()

            rise = self.poses.get(name, start)[2] - start[2]
            if rise >= self.v["pick_min_rise"]:
                break
            if attempt + 1 < attempts:
                self.emit(
                    "pick_retry", object=name, attempt=attempt + 1,
                    rise_m=rise, gripper_cmd=q_close, gripper_actual=closed_at,
                )
                self.set_gripper(self.g["open_position"])
                self.move_joints(q_grasp, self.m["fine_secs"])
                self.refresh(0.5)

        if rise < self.v["pick_min_rise"]:
            self.emit(
                "pick_failed", object=name, reason="object did not leave the surface",
                rise_m=rise, attempts=attempts,
                gripper_cmd=q_close, gripper_actual=closed_at,
            )
            self.set_gripper(self.g["open_position"])
            return False
        self.emit(
            "picked", object=name, rise_m=rise,
            gripper_cmd=q_close, gripper_actual=closed_at,
        )

        # --- place --------------------------------------------------------
        tx, ty = float(slot[0]), float(slot[1])
        release_z_world = self.pad_top + hh + self.m["release_gap"]
        px, py, pz = self.world_to_base(tx, ty, release_z_world)
        pz += self.g["tcp_offset_z"]
        above_pz = pz + self.m["approach_height"]

        q_above_place = self.solve_ik(px, py, above_pz, seed=q_lift or q_above)
        if q_above_place is None:
            self.emit("place_failed", object=name, reason="IK failed above the pad")
            return False
        self.move_joints(q_above_place, self.m["move_secs"])

        q_place = self.solve_ik(px, py, pz, seed=q_above_place)
        self.move_joints(q_place or q_above_place, self.m["fine_secs"])

        self.set_gripper(self.g["open_position"])
        self.refresh()
        self.move_joints(q_above_place, self.m["fine_secs"])
        self.refresh()

        final = self.poses.get(name, (0.0, 0.0, 0.0))
        dxy = math.dist((final[0], final[1]), (tx, ty))
        expected_z = self.pad_top + hh
        dz = abs(final[2] - expected_z)
        if dxy > self.v["place_xy_tol"] or dz > self.v["place_z_tol"]:
            self.emit(
                "place_failed", object=name, reason="object not resting on the target",
                xy_error_m=dxy, z_error_m=dz,
                final=[round(c, 3) for c in final],
            )
            return False
        self.emit(
            "placed", object=name, xy_error_m=dxy, z_error_m=dz,
            final=[round(c, 3) for c in final],
        )
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=None,
                    help="object name; repeatable. Default: every object.")
    ap.add_argument("--reset", action="store_true",
                    help="teleport objects back to their configured poses and exit")
    ap.add_argument("--world", default="warehouse")
    args = ap.parse_args()

    rclpy.init()
    node = PickPlace(args.world)
    try:
        if args.reset:
            print("resetting objects")
            node.reset_objects()
            return 0

        objects = node.cfg["objects"]
        if args.only:
            objects = [o for o in objects if o["name"] in args.only]
            if not objects:
                print(f"no such object(s): {args.only}", file=sys.stderr)
                return 2

        print("moving to home")
        node.go_home()
        node.set_gripper(node.g["open_position"])

        done = []
        for obj in objects:
            done.append((obj["name"], node.run_object(obj)))
            node.go_home()

        print("\n=== summary ===")
        for name, ok in done:
            print(f"  {name:12s} {'OK' if ok else 'FAILED'}")
        n_ok = sum(1 for _, ok in done if ok)
        print(f"  {n_ok}/{len(done)} pick-and-place cycles completed")
        node.emit("run_complete", completed=n_ok, total=len(done))
        return 0 if n_ok == len(done) else 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
