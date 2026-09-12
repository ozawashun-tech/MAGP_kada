#!/usr/bin/env bash
set -e
# ROS の setup.bash は未設定変数を参照するため nounset は使わない
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
if [[ -f /ws/install/setup.bash ]]; then
  # shellcheck disable=SC1091
  source /ws/install/setup.bash
fi
exec "$@"
