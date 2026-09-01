#!/usr/bin/env python3
"""Layer 3: G.R.A.S.P plan.json -> robot tasks. Purely rule-based, no LLM.

plan.py calls plan.json "the boundary to Layer 3". This is Layer 3. It consumes
the contract -- never the PlanGraph or the database -- so it has no dependency
on G.R.A.S.P internals and can be driven by a hand-written plan.

Four tables and a state set:

  verbs       which action names mean "pick and place"      bindings.yaml
  objects     rulebook word     -> scene object id          bindings.yaml
  places      rulebook word     -> site id                  bindings.yaml
  capability  (object, place)   -> verified reachable?      capabilities.yaml
  state set   the step tokens currently believed true       runtime

The state set never interprets a `requires`/`produces` string. They are opaque
tokens: `requires` means "these are in the set", `produces` means "add these".
That keeps the bridge rule-based with no string parsing, and lets you rename
states in a rulebook without touching this file.

The important line is in run(): `produces` is asserted ONLY after the arm
reports a *measured* success. A symbolic postcondition becomes true because
something was physically verified -- that is the whole point of this layer.

    ros2 run grasp_arm_bringup plan_bridge.py plan.json
    ros2 run grasp_arm_bringup plan_bridge.py plan.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
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
from grasp_arm_msgs.action import ExecuteTask
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import Trigger

PKG = "grasp_arm_bringup"


class PlanRejected(Exception):
    """The plan cannot be executed. Raised before anything moves."""


class PlanBridge(Node):
    def __init__(self, need_robot: bool = True):
        super().__init__("plan_bridge")
        share = Path(get_package_share_directory(PKG))
        self.bind = yaml.safe_load((share / "config" / "bindings.yaml").read_text())
        self.work = yaml.safe_load((share / "config" / "workstation.yaml").read_text())
        caps_path = share / "config" / "capabilities.yaml"
        if not caps_path.is_file():
            raise PlanRejected(
                "no capabilities.yaml -- run `ros2 run grasp_arm_bringup "
                "calibrate_reach.py` once with the sim up")
        self.caps = yaml.safe_load(caps_path.read_text())

        self._check_bindings()

        self.state: set[str] = set()
        self.occupied: dict[str, str] = {}
        self.events = self.create_publisher(String, "/grasp_arm/plan_events", 10)

        self.task = None
        self.reset = None
        if need_robot:
            self.task = ActionClient(self, ExecuteTask, "/task_executor/execute_task")
            self.reset = self.create_client(Trigger, "/task_executor/reset_scene")
            if not self.task.wait_for_server(timeout_sec=60.0):
                raise PlanRejected("task_executor is not running")

    # --------------------------------------------------------------- tables
    def _check_bindings(self):
        """Every binding must land on something real. Catches drift early."""
        places = self.work["sites"]["places"]
        objects = {o["name"] for o in self.work["objects"]}
        bad = [f"place {k}->{v}" for k, v in self.bind["places"].items() if v not in places]
        bad += [f"object {k}->{v}" for k, v in self.bind["objects"].items() if v not in objects]
        if bad:
            raise PlanRejected("bindings.yaml points at things that do not exist: "
                               + ", ".join(bad))

    def is_place_action(self, action: str) -> bool:
        prefixes = self.bind["verbs"]["pick_and_place"]["action_prefixes"]
        return any(action.startswith(p) for p in prefixes)

    def resolve(self, step: dict) -> tuple[str, str]:
        """Arguments by TYPE, not position.

        G.R.A.S.P emits `uses` in no particular order -- one step lists the
        vertex first, the next lists the block first -- so roles are assigned by
        which table each name is found in.
        """
        uses = list(step.get("uses", []))
        objs = [self.bind["objects"][u] for u in uses if u in self.bind["objects"]]
        places = [self.bind["places"][u] for u in uses if u in self.bind["places"]]
        unknown = [u for u in uses
                   if u not in self.bind["objects"] and u not in self.bind["places"]]
        where = f"step {step['order']} ({step['action']})"
        if unknown:
            raise PlanRejected(f"{where}: unknown name(s) in uses: {unknown}")
        if len(objs) != 1 or len(places) != 1:
            raise PlanRejected(
                f"{where}: need exactly one object and one place in uses, "
                f"got objects={objs} places={places}")
        return objs[0], places[0]

    def capable(self, object_id: str, place_id: str) -> bool:
        return bool(self.caps.get("pairs", {}).get(object_id, {}).get(place_id))

    # ----------------------------------------------------------- validation
    def validate(self, plan: dict) -> list[tuple[dict, str, str]]:
        """Everything checkable, before anything moves. Reports all problems."""
        if plan.get("clarification_needed"):
            raise PlanRejected(
                "G.R.A.S.P returned a clarification, not a plan: "
                + plan.get("reason", "").splitlines()[0])

        steps = sorted(plan.get("steps", []), key=lambda s: s["order"])
        if not steps:
            raise PlanRejected("plan has no steps")

        problems: list[str] = []
        tasks: list[tuple[dict, str, str]] = []
        seen_places: dict[str, int] = {}
        # Walk the plan with a simulated state set, so an internally
        # inconsistent plan is caught here rather than halfway through.
        sim = set(self.state)

        for step in steps:
            where = f"step {step['order']} ({step['action']})"
            if not self.is_place_action(step["action"]):
                problems.append(f"{where}: not a known verb")
                continue
            try:
                object_id, place_id = self.resolve(step)
            except PlanRejected as exc:
                problems.append(str(exc))
                continue

            if not self.capable(object_id, place_id):
                reason = self.caps.get("waypoint_failures", {}).get(
                    f"{object_id}->{place_id}", "not in the capability table")
                problems.append(
                    f"{where}: {object_id} -> {place_id} is not reachable ({reason})")
            if place_id in seen_places:
                problems.append(
                    f"{where}: {place_id} already targeted by step {seen_places[place_id]}")
            seen_places[place_id] = step["order"]
            if place_id in self.occupied:
                problems.append(f"{where}: {place_id} already holds "
                                f"{self.occupied[place_id]}")

            missing = [t for t in step.get("requires", []) if t not in sim]
            if missing:
                problems.append(f"{where}: unmet precondition(s) {missing}")
            sim.update(step.get("produces", []))
            tasks.append((step, object_id, place_id))

        if problems:
            raise PlanRejected("plan rejected:\n  - " + "\n  - ".join(problems))
        return tasks

    # ------------------------------------------------------------ execution
    def emit(self, event: str, **fields):
        payload = {"event": event, "stamp": time.time(), **fields}
        self.events.publish(String(data=json.dumps(payload)))
        detail = "  ".join(
            f"{k}={round(v, 4) if isinstance(v, float) else v}" for k, v in fields.items())
        print(f"  [PLAN] {event:16s} {detail}", flush=True)

    def _spin(self, future, timeout=900.0):
        rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
        return future.result()

    def do_reset(self):
        if self.reset is None or not self.reset.wait_for_service(timeout_sec=30.0):
            return False
        res = self._spin(self.reset.call_async(Trigger.Request()), timeout=240.0)
        self.state.clear()
        self.occupied.clear()
        return bool(res and res.success)

    def run(self, plan: dict, tasks) -> bool:
        policy = self.bind.get("policy", {}).get("on_step_failure", "abort")
        self.emit("plan_started", goal=plan.get("goal"), steps=len(tasks),
                  command=plan.get("command"))
        completed = 0
        for step, object_id, place_id in tasks:
            # Guard again at execution time: an earlier failure under the
            # `continue` policy may have invalidated a later precondition.
            missing = [t for t in step.get("requires", []) if t not in self.state]
            if missing:
                self.emit("step_skipped", order=step["order"], action=step["action"],
                          reason=f"unmet precondition(s) {missing}")
                if policy == "abort":
                    break
                continue

            self.emit("step_started", order=step["order"], action=step["action"],
                      object=object_id, place=place_id)
            goal = ExecuteTask.Goal(object_id=object_id, place_id=place_id)
            handle = self._spin(self.task.send_goal_async(goal))
            if handle is None or not handle.accepted:
                self.emit("step_failed", order=step["order"], code="REJECTED",
                          reason="task server refused the goal")
                if policy == "abort":
                    break
                continue

            res = self._spin(handle.get_result_async())
            r = res.result if res else None
            if r is None or not r.success:
                self.emit("step_failed", order=step["order"], action=step["action"],
                          code=(r.failure_code if r else "NO_RESULT"),
                          reason=(r.message if r else "no result from the arm"))
                if policy == "abort":
                    self.emit("plan_aborted", at_step=step["order"],
                              completed=completed, total=len(tasks))
                    return False
                continue

            # The one line that matters: a measured success is what licenses
            # asserting the symbolic postcondition.
            self.state.update(step.get("produces", []))
            self.occupied[place_id] = object_id
            completed += 1
            self.emit("step_complete", order=step["order"], action=step["action"],
                      produces=list(step.get("produces", [])),
                      place_xy_error=r.place_xy_error, attempts=r.grasp_attempts)

        ok = completed == len(tasks)
        self.emit("plan_complete" if ok else "plan_incomplete",
                  goal=plan.get("goal"), completed=completed, total=len(tasks),
                  state=sorted(self.state))
        return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", type=Path, help="plan.json from tools/emit_plan.py")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate only; never contacts the arm")
    ap.add_argument("--reset", action="store_true",
                    help="reset the scene before executing")
    args = ap.parse_args()

    rclpy.init()
    node = None
    try:
        node = PlanBridge(need_robot=not args.dry_run)
        plan = json.loads(args.plan.read_text())
        tasks = node.validate(plan)
        print(f"plan accepted: {plan.get('goal')} -- {len(tasks)} step(s)")
        for step, obj, place in tasks:
            print(f"  {step['order']}. {step['action']:36s} {obj} -> {place}")
        if args.dry_run:
            print("\ndry run: nothing executed")
            return 0
        if args.reset:
            print("resetting scene")
            node.do_reset()
        print()
        return 0 if node.run(plan, tasks) else 1
    except PlanRejected as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
