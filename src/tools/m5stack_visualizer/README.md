# m5stack_visualizer

[🇯🇵 日本語](#日本語) | [🇺🇸 English](#english)

---

## English

Serial bridge node that relays ROS2 status topics to an M5Stack Core2 display.
Includes PlatformIO firmware for the M5Stack side.

### Node: `m5stack_bridge_node`

#### Subscribed Topics

| Topic | Type | Description |
|-------|------|-------------|
| `/bag_recorder/status` | `std_msgs/String` | JSON bag recorder status |
| `/mux_pwm/status` | `std_msgs/String` | JSON mux mode status |
| `/nn_pwm_controller/model` | `std_msgs/String` | Name of the model the inference node is using (transient_local) |

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `serial_port` | `/dev/ttyUSB0` | Serial port of M5Stack |
| `baud_rate` | `115200` | Baud rate |

### M5Stack Firmware

The `platformio/` directory contains the M5Stack firmware.

```bash
cd src/tools/m5stack_visualizer/platformio
pio run -t upload
```

#### Display Modes

| Mode | Content |
|------|---------|
| 0 (default) | Mux mode large display (Auto=red / Manual=blue) and model name |
| 1 | Bag recorder + mux mode split view (with model name) |
| 2 | Bag recorder large display (RECORDING=red / IDLE=green) |

M5Stack Core2 button controls: **BtnA** = previous mode, **BtnB** = refresh, **BtnC** = next mode.

The model name is split into the name and its timestamp (`_YYYYMMDD_HHMMSS`) on two lines. It is also shown in one small line on the DRIVE standby screen.

### Setup

```bash
# Install pyserial
pip3 install pyserial

# Allow serial port access (re-login required)
sudo usermod -a -G dialout $USER

# Launch
ros2 launch m5stack_visualizer m5stack_visualizer.launch.py serial_port:=/dev/ttyUSB0
```

---

## 日本語

bag recorderとmuxのステータスをM5Stack Core2に表示するシリアルブリッジノードです。

### ノード: `m5stack_bridge_node`

#### サブスクライブトピック

| トピック | 型 | 説明 |
|--------|------|------|
| `/bag_recorder/status` | `std_msgs/String` | JSON形式のbag recorderステータス |
| `/mux_pwm/status` | `std_msgs/String` | JSON形式のmuxモードステータス |
| `/nn_pwm_controller/model` | `std_msgs/String` | 推論ノードが使っているモデル名（transient_local） |

#### パラメータ

| パラメータ | デフォルト | 説明 |
|-----------|---------|------|
| `serial_port` | `/dev/ttyUSB0` | M5StackのシリアルポートPath |
| `baud_rate` | `115200` | ボーレート |

### M5Stackファームウェア

`platformio/` ディレクトリにM5Stack用ファームウェアがあります。

```bash
cd src/tools/m5stack_visualizer/platformio
pio run -t upload
```

#### 表示モード

| モード | 内容 |
|------|------|
| 0（デフォルト） | Muxモード大表示（Auto=赤 / Manual=青）とモデル名 |
| 1 | bag recorder + mux 分割表示（モデル名つき） |
| 2 | bag recorder大表示（RECORDING=赤 / IDLE=緑） |

M5Stack Core2ボタン: **BtnA**=前のモード、**BtnB**=画面リフレッシュ、**BtnC**=次のモード

モデル名は、末尾の日時（`_YYYYMMDD_HHMMSS`）を分けて 2 行で出す（例: `origin_counter_clockwise` と `10/31 14:30`）。DRIVE の待機表示にも小さく 1 行で出す。

### セットアップ

```bash
pip3 install pyserial
sudo usermod -a -G dialout $USER
ros2 launch m5stack_visualizer m5stack_visualizer.launch.py serial_port:=/dev/ttyUSB0
```

#### トラブルシューティング

シリアルポートが開けない場合: `sudo chmod 666 /dev/ttyUSB0`（一時的）
表示されない場合: `ros2 topic echo /bag_recorder/status` でトピックを確認してください。
