#!/bin/bash
set -euo pipefail

echo "Starting Joy PWM Controller..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

source /opt/ros/humble/setup.bash
source "${REPO_ROOT}/install/setup.bash"

# ros2 run joy joy_node --ros-args -p dev:=/dev/input/js0 &
# sleep 1
ros2 run pwm_controller pwm_pca9685_controller
