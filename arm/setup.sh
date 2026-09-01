#!/usr/bin/env bash
# One-shot setup for the robotic-arm demo.
#
#   cd <workspace>/src/G.R.A.S.P/arm && ./setup.sh && cd ../../.. && colcon build ...
#
# Idempotent: safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT="$(cd "$HERE/../../.." && pwd)"        # the colcon workspace (has src/)
PKG="$HERE/grasp_arm_bringup"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1/5  fetching third-party sources into vendor/"
mkdir -p "$HERE/vendor"
if command -v vcs >/dev/null 2>&1; then
  vcs import "$HERE/vendor" < "$HERE/arm_demo.repos" || true
else
  echo "vcstool not found; falling back to git clone"
  clone() {  # url branch dir
    [ -d "$HERE/vendor/$3" ] || git clone --depth 1 --branch "$2" "$1" "$HERE/vendor/$3"
  }
  clone https://github.com/Kinovarobotics/ros2_kortex.git humble ros2_kortex
  clone https://github.com/aalmrad/ros2_robotiq_gripper.git main ros2_robotiq_gripper
  clone https://github.com/leonhartyao/gazebo_models_worlds_collection.git master \
        gazebo_models_worlds_collection
fi

say "2/5  patching robotiq_description"
# The four 2F-85 mimic joints are declared type="continuous" with no <limit>,
# which is invalid URDF -- urdfdom defaults them to effort=0, so the finger
# links cannot transmit any grip force. See patches/ for the diff.
pushd "$HERE/vendor/ros2_robotiq_gripper" >/dev/null
for p in "$HERE"/patches/*.patch; do
  if git apply --reverse --check "$p" 2>/dev/null; then
    echo "  already applied: $(basename "$p")"
  else
    git apply "$p" && echo "  applied: $(basename "$p")"
  fi
done
popd >/dev/null

say "3/5  marking packages colcon should not build"
# kortex_api pulls a large Kinova binary SDK and kortex_driver is hardware-only;
# both come from the ros-humble-kortex-* debs. kortex_bringup is replaced by
# grasp_arm_bringup. robotiq_driver needs a serial port.
for p in ros2_kortex/kortex_api ros2_kortex/kortex_driver ros2_kortex/kortex_bringup \
         ros2_robotiq_gripper/robotiq_driver ros2_robotiq_gripper/robotiq_controllers \
         ros2_robotiq_gripper/robotiq_hardware_tests \
         gazebo_models_worlds_collection; do
  [ -d "$HERE/vendor/$p" ] && touch "$HERE/vendor/$p/COLCON_IGNORE"
done
echo "  done"

say "4/5  generating the warehouse world"
python3 "$PKG/scripts/make_world.py"

say "5/5  building"
cd "$WS_ROOT"
# ROS setup.bash reads unset variables; nounset has to come off around it.
set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
set -u
colcon build --base-paths src/G.R.A.S.P/arm --symlink-install

cat <<'DONE'

== ready ==

  source install/setup.bash

  # sim + MoveIt + RViz
  ros2 launch grasp_arm_bringup warehouse_sim.launch.py

  # headless
  ros2 launch grasp_arm_bringup warehouse_sim.launch.py headless:=true launch_rviz:=false

  # in a second terminal
  ros2 run grasp_arm_bringup pick_place.py            # all three objects
  ros2 run grasp_arm_bringup pick_place.py --reset    # back to the start pose
  ros2 topic echo /grasp_arm/events                   # acknowledgements
DONE
