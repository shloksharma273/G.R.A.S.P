#!/usr/bin/env python3
"""Standalone demo CLI: submit pick-and-place tasks to the arm's task server.

This used to be the whole implementation. It is now a thin client, so there is
exactly one copy of the motion and verification logic (in task_executor.py) and
this and the G.R.A.S.P plan bridge drive it the same way.

    ros2 run grasp_arm_bringup pick_place.py                     # configured slots
    ros2 run grasp_arm_bringup pick_place.py --only red_block
    ros2 run grasp_arm_bringup pick_place.py red_block:dot_x     # explicit task
    ros2 run grasp_arm_bringup pick_place.py --reset
"""

from __future__ import annotations

import argparse
import sys
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
from grasp_arm_msgs.action import ExecuteTask
from rclpy.action import ActionClient
from rclpy.node import Node
from std_srvs.srv import Trigger

PKG = "grasp_arm_bringup"


class Client(Node):
    def __init__(self):
        super().__init__("pick_place_client")
        share = Path(get_package_share_directory(PKG))
        self.cfg = yaml.safe_load((share / "config" / "workstation.yaml").read_text())
        self.task = ActionClient(self, ExecuteTask, "/task_executor/execute_task")
        self.reset = self.create_client(Trigger, "/task_executor/reset_scene")
        if not self.task.wait_for_server(timeout_sec=60.0):
            raise RuntimeError("task_executor not running -- start the sim launch first")

    def spin(self, future, timeout=600.0):
        rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
        return future.result()

    def run_task(self, object_id, place_id):
        goal = ExecuteTask.Goal(object_id=object_id, place_id=place_id)
        print(f"\n=== {object_id} -> {place_id} ===", flush=True)
        handle = self.spin(self.task.send_goal_async(
            goal, feedback_callback=lambda fb: print(
                f"    ..{fb.feedback.phase}", flush=True)))
        if handle is None or not handle.accepted:
            print("  REJECTED (server busy?)")
            return False
        res = self.spin(handle.get_result_async())
        r = res.result if res else None
        if r is None:
            print("  no result")
            return False
        if r.success:
            print(f"  OK   rise={r.pick_rise*100:.1f}cm  "
                  f"xy_err={r.place_xy_error*100:.1f}cm  attempts={r.grasp_attempts}")
        else:
            print(f"  FAIL [{r.failure_code}] {r.message}")
        return r.success

    def do_reset(self):
        if not self.reset.wait_for_service(timeout_sec=30.0):
            print("reset service unavailable")
            return False
        res = self.spin(self.reset.call_async(Trigger.Request()), timeout=180.0)
        print(f"reset: {res.message if res else 'no response'}")
        return bool(res and res.success)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="*", metavar="OBJECT:PLACE",
                    help="explicit tasks; default is the configured slots")
    ap.add_argument("--only", action="append", help="restrict to these objects")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    rclpy.init()
    node = Client()
    try:
        if args.reset:
            return 0 if node.do_reset() else 1

        if args.tasks:
            pairs = []
            for spec in args.tasks:
                if ":" not in spec:
                    print(f"bad task {spec!r}, expected OBJECT:PLACE", file=sys.stderr)
                    return 2
                obj, place = spec.split(":", 1)
                pairs.append((obj, place))
        else:
            slots = node.cfg["place_pad"]["slots"]
            pairs = [(o, p) for o, p in slots.items()
                     if not args.only or o in args.only]
        if not pairs:
            print("nothing to do", file=sys.stderr)
            return 2

        done = [(o, p, node.run_task(o, p)) for o, p in pairs]
        print("\n=== summary ===")
        for o, p, ok in done:
            print(f"  {o:12s} -> {p:9s} {'OK' if ok else 'FAILED'}")
        n = sum(1 for *_, ok in done if ok)
        print(f"  {n}/{len(done)} tasks completed")
        return 0 if n == len(done) else 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
