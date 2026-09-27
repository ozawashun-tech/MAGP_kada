[🇯🇵 日本語](README_ja.md) | 🇺🇸 English

> 🚧 **Work in progress** — This project is under active development. Documentation and hardware build guide are incomplete.

# MAGP — E2E RC-CAR Project for Minicar Autonomous Grand Prix

> End-to-end imitation learning RC car with 2D LiDAR and PyTorch Transformer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![ROS2](https://img.shields.io/badge/ROS2-Humble-blue)](https://docs.ros.org/en/humble/)

## Overview

MAGP is an open-source autonomous miniature car platform that learns to drive by imitating human demonstrations. A 2D LiDAR captures the environment, and a PyTorch Transformer model trained on recorded driving data outputs throttle and steering commands in real time.

Key features:
- **End-to-End Imitation Learning** — raw LiDAR scan → throttle + steering angle
- **Transformer-based model** — sequence of scan history as input, multi-step prediction
- **Seamless manual/auto switching** — PS3 joystick blending via PWM multiplexer
- **Shared control** — blend autonomous output with manual correction for richer data collection
- **Easy data collection** — joystick-triggered rosbag recorder with safety checks
- **M5Stack display** — real-time status visualization on hardware

## System Architecture

```
[Hokuyo LiDAR]
      │ /scan
      ▼
[nn_pwm_controller]  ←── PyTorch Transformer (E2E imitation learning)
      │ /torch_pwm
      ▼
[mux_pwm] ←─────────────── [PS3 Joystick]
      │ /mux_pwm               (manual override / mode switch)
      ▼
[pwm_pca9685_controller]
      │ I2C
      ▼
 Motor / Servo

Side channels:
  [mux_pwm] ──► [bag_recorder]       (data collection)
  [mux_pwm] ──► [m5stack_visualizer] (status display via serial)
```

## Hardware Requirements

> See [docs/hardware.md](docs/hardware.md) for the full build guide (wiring and assembly coming soon).
> 3D-printable parts (STL/STEP/Fusion 360 source) are in [hardware/](hardware/).

| Component | Model |
|-----------|-------|
| RC car chassis | CARTEN M210 |
| Motor | Tamiya TBLM-02S Brushless Motor 02 (sensored) 21.5T, item no. 54895 |
| ESC | Tamiya TBLE-02S Brushless ESC 02 (sensored), item no. 45057 |
| 2D LiDAR | Hokuyo UST-10LX (Ethernet, cable included) |
| SBC | NVIDIA Jetson Orin NX |
| SBC case | Any compatible case (e.g. [Amazon](https://amzn.asia/d/0bSVL3AZ)) |
| PWM driver | PCA9685 breakout board (I2C) |
| LiPo battery | [7.4V 2S](https://amzn.asia/d/3W8HEnf) |
| DC-DC boost converter | [7.4V → 12V](https://amzn.asia/d/dPUERnJ) |
| Tamiya plug | [connector](https://amzn.asia/d/3aU40sA) |
| DC barrel jack pigtail cable | check Jetson carrier board jack size |
| Joystick | PS3 / PS4 |
| Display | M5Stack Core2 (optional) |

The Jetson Orin NX + UST-10LX combination provides research-grade compute and sensing in a 1/10-scale chassis, making this platform suitable for real machine learning experiments beyond hobbyist prototyping.

## Software Requirements

- ROS2 Humble
- Python 3.10+
- PyTorch 2.x
- `adafruit-circuitpython-pca9685`
- `pyserial` (for M5Stack visualizer)

## Quick Start

### 1. Build

```bash
cd MAGP_kada
make build
# or: colcon build --symlink-install
```

### 2. Collect Training Data

Connect the PS3 joystick, launch the full system, then drive manually while recording:

```bash
make run
```

Bag recorder joystick controls:

| Button | Action |
|--------|--------|
| △ × 3 | Unlock recorder |
| ■ | Start recording |
| ● | Stop & save |
| ✕ | Discard recording |

### 3. Train the Model

Install the additional Python dependency for reading rosbag files:

```bash
pip install rosbags
```

Place collected rosbag data under `dataset/train/` (e.g., `dataset/train/YYYYMMDD/`), then run:

```bash
cd training_src
python train.py
```

Key parameters at the bottom of `training_src/train.py` (edit as needed):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `bag_paths` | `../dataset/train/20260401` | Path to rosbag directory |
| `model_save_path` | `../model/transformer/model.pth` | Output model path |
| `scan_history_length` | `20` | Number of scan frames in input sequence |
| `scan_history_stride` | `10` | Stride between frames |
| `epochs` | `128` | Max training epochs |
| `learning_rate` | `0.0001` | Learning rate |

The best checkpoint (lowest validation loss) is saved to `model/checkpoints/best_model.pth`.
Periodic checkpoints are saved every `checkpoint_interval` epochs, and early stopping triggers after 10 epochs without improvement.

Set `model_path` in the launch file to point to the saved `.pth` file.

### 4. Run Autonomous Mode

```bash
make run
```

Press the **PS button** on the joystick to toggle between **manual** and **autonomous** mode.
In autonomous mode, hold **L2** to blend manual input with the model output.

## Package Overview

| Package | Node | Description |
|---------|------|-------------|
| [pytorch_pwm_controller](src/pytorch_pwm_controller/) | `nn_pwm_controller_node` | PyTorch Transformer inference, publishes PWM commands |
| [mux_pwm](src/mux_pwm/) | `pwm_mux_node` | Manual/auto PWM multiplexer with joystick blending |
| [pwm_controller](src/controller/pwm_controller/) | `pwm_pca9685_controller` | PCA9685 I2C hardware PWM driver |
| [bag_recorder](src/tools/bag_recorder/) | `bag_recorder_node` | Joystick-controlled rosbag recorder |
| [m5stack_visualizer](src/tools/m5stack_visualizer/) | `m5stack_bridge_node` | Serial bridge to M5Stack Core2 display |
| [urg_node2](src/urg_node2/) | `urg_node2` | Hokuyo 2D LiDAR driver (by Hokuyo / eSOL) |

## License

This project (excluding `urg_node2`) is licensed under the [MIT License](LICENSE).
Copyright (c) 2025 K.Aiki, K.Teruyuki

`urg_node2` is licensed under the Apache License 2.0. See [src/urg_node2/LICENSE](src/urg_node2/LICENSE).

---

Documentation supported by [Claude](https://claude.ai) (Anthropic).