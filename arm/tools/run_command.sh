#!/usr/bin/env bash
# One command, end to end: natural language -> PlanGraph -> arm.
#
#   ./tools/run_command.sh "make a magic sequence"
#   ./tools/run_command.sh "arrange the blocks in a straight line"
#   ./tools/run_command.sh "make a magic sequence" --offline   # in-memory graph
#   ./tools/run_command.sh "make a magic sequence" --dry-run   # validate, don't move
#
# Requires the sim to be running:
#   ros2 launch grasp_arm_bringup warehouse_sim.launch.py
#
# Deliberately a wrapper rather than G.R.A.S.P imports inside plan_bridge: the
# bridge consumes plan.json and nothing else, so it stays testable against a
# hand-written plan and independent of G.R.A.S.P internals.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARM="$(dirname "$HERE")"
REPO="$(dirname "$ARM")"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

COMMAND="${1:-}"
if [ -z "$COMMAND" ]; then
  echo "usage: $(basename "$0") \"<command>\" [--offline] [--dry-run] [--no-reset]" >&2
  exit 2
fi
shift

MODE=--live
RESET=--reset
DRY=""
for arg in "$@"; do
  case "$arg" in
    --offline)  MODE="" ;;
    --live)     MODE=--live ;;
    --no-reset) RESET="" ;;
    --dry-run)  DRY=--dry-run; RESET="" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 not on PATH -- source /opt/ros/humble/setup.bash and the workspace's install/setup.bash" >&2
  exit 2
fi

PLAN="$(mktemp -t grasp_plan_XXXXXX.json)"
trap 'rm -f "$PLAN"' EXIT

echo "== planning: \"$COMMAND\""
# emit_plan exits 2 and writes a clarification when nothing matches well enough.
if ! "$PY" "$HERE/emit_plan.py" "$COMMAND" $MODE -o "$PLAN"; then
  echo
  echo "No plan: G.R.A.S.P could not resolve that command to a skill." >&2
  echo "Ask it what it knows:  $HERE/emit_plan.py --list $MODE" >&2
  exit 1
fi

echo
echo "== executing"
exec ros2 run grasp_arm_bringup plan_bridge.py "$PLAN" $RESET $DRY
