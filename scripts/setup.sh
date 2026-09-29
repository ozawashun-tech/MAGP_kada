#!/usr/bin/env bash
# MAGP_kada の共通環境セットアップ。
# PC（Docker）も Jetson（素の OS）も、このスクリプトだけを実行する。
#
# Tested: Ubuntu 22.04 / ROS 2 Humble / Python 3.10
#   PC:    torch 2.4.1 (CPU。CUDA が要るときは MAGP_TORCH_FLAVOR=cu121)
#   Jetson: JetPack 6.x 向け NVIDIA wheel (pypi.jetson-ai-lab.io/jp6/cu126)
#
# 使い方:
#   bash scripts/setup.sh
# 上書き:
#   MAGP_SKIP_ROS=1 MAGP_SKIP_TORCH=1 bash scripts/setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REQUIREMENTS="${MAGP_REQUIREMENTS:-${REPO_ROOT}/requirements.txt}"

ROS_DISTRO_NAME="humble"
PC_TORCH_VERSION="${MAGP_TORCH_VERSION:-2.4.1}"
PC_TORCH_FLAVOR="${MAGP_TORCH_FLAVOR:-cpu}"   # cpu | cu121
JETSON_TORCH_VERSION="${MAGP_JETSON_TORCH_VERSION:-2.8.0}"
JETSON_TORCH_INDEX="${MAGP_JETSON_TORCH_INDEX:-https://pypi.jetson-ai-lab.io/jp6/cu126}"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

pip_install() {
  local extra=()
  if python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    extra+=(--break-system-packages)
  fi
  python3 -m pip install "${extra[@]}" "$@"
}

is_jetson() {
  [[ -f /etc/nv_tegra_release ]] && return 0
  if [[ -f /proc/device-tree/compatible ]]; then
    grep -aq tegra /proc/device-tree/compatible 2>/dev/null && return 0
  fi
  return 1
}

echo "========================================"
echo " MAGP_kada environment setup"
echo " repo: ${REPO_ROOT}"
echo " arch: $(uname -m)"
echo "========================================"

# --- OS チェック ----------------------------------------------------------
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${VERSION_ID:-}" != "22.04" ]]; then
    echo "ERROR: Ubuntu 22.04 が必要です (detected: ${PRETTY_NAME:-unknown})" >&2
    echo "       PC は Docker を使い、実機は JetPack 6.x (Ubuntu 22.04) を使ってください。" >&2
    exit 1
  fi
else
  echo "ERROR: /etc/os-release が見つかりません。" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
export LANG="${LANG:-C.UTF-8}"

# --- ROS 2 Humble ---------------------------------------------------------
install_ros_humble() {
  echo "==> Installing ROS 2 Humble"
  $SUDO apt-get update
  $SUDO apt-get install -y locales curl gnupg lsb-release software-properties-common
  $SUDO locale-gen en_US.UTF-8
  $SUDO update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
  export LANG=en_US.UTF-8
  $SUDO add-apt-repository -y universe

  if [[ ! -f /usr/share/keyrings/ros-archive-keyring.gpg ]]; then
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
      | $SUDO tee /usr/share/keyrings/ros-archive-keyring.gpg >/dev/null
  fi
  local ubu
  ubu="$(. /etc/os-release && echo "${UBUNTU_CODENAME}")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu ${ubu} main" \
    | $SUDO tee /etc/apt/sources.list.d/ros2.list >/dev/null

  $SUDO apt-get update
  $SUDO apt-get install -y "ros-${ROS_DISTRO_NAME}-desktop"
}

if [[ "${MAGP_SKIP_ROS:-0}" != "1" ]]; then
  if [[ ! -f /opt/ros/${ROS_DISTRO_NAME}/setup.bash ]]; then
    install_ros_humble
  else
    echo "==> ROS 2 ${ROS_DISTRO_NAME} already present"
  fi
fi

# --- apt パッケージ -------------------------------------------------------
echo "==> Installing apt packages"
$SUDO apt-get update
$SUDO apt-get install -y --no-install-recommends \
  build-essential \
  cmake \
  git \
  curl \
  wget \
  pkg-config \
  python3-pip \
  python3-dev \
  python3-venv \
  python3-colcon-common-extensions \
  python3-rosdep \
  python3-argcomplete \
  i2c-tools \
  libi2c-dev \
  libgpiod-dev \
  "ros-${ROS_DISTRO_NAME}-joy" \
  "ros-${ROS_DISTRO_NAME}-diagnostic-updater" \
  "ros-${ROS_DISTRO_NAME}-laser-proc" \
  "ros-${ROS_DISTRO_NAME}-tf2-ros" \
  "ros-${ROS_DISTRO_NAME}-rosbag2" \
  "ros-${ROS_DISTRO_NAME}-rosbag2-storage-mcap" \
  "ros-${ROS_DISTRO_NAME}-cartographer-ros" \
  "ros-${ROS_DISTRO_NAME}-nav2-map-server"

if apt-cache show python3-libgpiod >/dev/null 2>&1; then
  $SUDO apt-get install -y --no-install-recommends python3-libgpiod || true
fi

# Jetson 上では I2C / シリアルを使えるグループへ入れる
if is_jetson && [[ "$(id -u)" -ne 0 ]]; then
  echo "==> Adding ${USER} to i2c,dialout,gpio (re-login required)"
  $SUDO usermod -aG i2c,dialout,gpio "${USER}" 2>/dev/null \
    || $SUDO usermod -aG i2c,dialout "${USER}" || true
fi

# --- Python (torch 以外) -------------------------------------------------
if [[ ! -f "${REQUIREMENTS}" ]]; then
  echo "ERROR: requirements.txt が見つかりません: ${REQUIREMENTS}" >&2
  exit 1
fi

echo "==> Installing Python packages from ${REQUIREMENTS}"
pip_install --upgrade pip
pip_install -r "${REQUIREMENTS}"

# --- PyTorch --------------------------------------------------------------
install_torch_pc() {
  local index
  case "${PC_TORCH_FLAVOR}" in
    cpu)   index="https://download.pytorch.org/whl/cpu" ;;
    cu121) index="https://download.pytorch.org/whl/cu121" ;;
    *)
      echo "ERROR: MAGP_TORCH_FLAVOR は cpu または cu121 です (${PC_TORCH_FLAVOR})" >&2
      exit 1
      ;;
  esac
  echo "==> Installing torch==${PC_TORCH_VERSION} (${PC_TORCH_FLAVOR}) for $(uname -m)"
  pip_install "torch==${PC_TORCH_VERSION}" --index-url "${index}"
}

install_torch_jetson() {
  echo "==> Installing Jetson torch==${JETSON_TORCH_VERSION}"
  echo "    index: ${JETSON_TORCH_INDEX}"
  echo "    公式 PyPI の torch は使わない（CPU 専用になり GPU が使えない）"
  if ! pip_install "torch==${JETSON_TORCH_VERSION}" --index-url "${JETSON_TORCH_INDEX}"; then
    echo "ERROR: Jetson 用 PyTorch のインストールに失敗しました。" >&2
    echo "       JetPack の版に合わせて次を上書きしてください。" >&2
    echo "         MAGP_JETSON_TORCH_INDEX=..." >&2
    echo "         MAGP_JETSON_TORCH_VERSION=..." >&2
    echo "       または手動インストール後に MAGP_SKIP_TORCH=1 で再実行。" >&2
    exit 1
  fi
}

if [[ "${MAGP_SKIP_TORCH:-0}" != "1" ]]; then
  if python3 -c "import torch" >/dev/null 2>&1 && [[ "${MAGP_FORCE_TORCH:-0}" != "1" ]]; then
    echo "==> torch already installed: $(python3 -c 'import torch; print(torch.__version__)')"
  else
    if is_jetson; then
      install_torch_jetson
    else
      install_torch_pc
    fi
  fi
else
  echo "==> Skipping torch (MAGP_SKIP_TORCH=1)"
fi

# --- 結果表示 -------------------------------------------------------------
echo
echo "========================================"
echo " setup finished"
echo "  OS:     ${PRETTY_NAME:-unknown}"
echo "  arch:   $(uname -m)"
echo "  jetson: $(is_jetson && echo yes || echo no)"
if [[ -f /opt/ros/${ROS_DISTRO_NAME}/setup.bash ]]; then
  echo "  ROS:    ${ROS_DISTRO_NAME}"
else
  echo "  ROS:    not found"
fi
python3 - <<'PY'
import importlib.util
print("  python:", __import__("sys").version.split()[0])
for name in ("numpy", "pandas", "rosbags", "serial", "adafruit_pca9685",
             "adafruit_bno08x", "adafruit_extended_bus"):
    spec = importlib.util.find_spec(name)
    print(f"  {name}: {'ok' if spec else 'MISSING'}")
try:
    import torch
    print(f"  torch: {torch.__version__}  cuda={torch.cuda.is_available()}")
except Exception as exc:
    print(f"  torch: MISSING ({exc})")
PY
echo "========================================"
echo "次: source /opt/ros/${ROS_DISTRO_NAME}/setup.bash && make build"
echo "    Jetson でグループ追加した場合は一度ログアウトしてください。"
