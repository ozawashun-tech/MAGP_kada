# bag_recorder

[🇯🇵 日本語](#日本語) | [🇺🇸 English](#english)

---

## English

Joystick-controlled ROS2 bag recorder with safety features.
Uses a PS3/PS4 controller to start, save, and discard rosbag recordings.

### Node: `bag_recorder_node`

#### Subscribed Topics

| Topic | Type | Description |
|-------|------|-------------|
| `/joy` | `sensor_msgs/Joy` | PS3/PS4 joystick input |

#### Published Topics

| Topic | Type | Description |
|-------|------|-------------|
| `/bag_recorder/status` | `std_msgs/String` | JSON status at 1 Hz |
| `/bag_recorder/event` | `std_msgs/String` | JSON event messages |

#### Joystick Controls

| Button | Action |
|--------|--------|
| △ × 3 | Unlock recorder (required on startup) |
| ■ (Square) | Start recording |
| ● (Circle) | Stop & save |
| ✕ (Cross) | Discard current recording |

#### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `record_all_topics` | `true` | Record all active topics |
| `topic_list` | `[]` | Explicit topic list (when `record_all_topics` is false) |
| `save_directory` | `~/rosbag/` | Directory to save bag files |
| `disk_space_warning_threshold_gb` | `10.0` | Warn below this free space (GB) |
| `disk_space_critical_threshold_gb` | `3.0` | Block recording below this free space (GB) |

#### Status JSON

```json
{
  "state": "RECORDING",
  "system_unlocked": true,
  "current_bag": "rosbag2_2025_10_29-15_30_45_1",
  "recording_start_time": "2025-10-29T15:30:45.123456",
  "bag_counter": 1
}
```

#### Event Types

`SYSTEM_LOCKED` | `UNLOCK_PROGRESS` | `SYSTEM_UNLOCKED` | `RECORDING_STARTED` | `RECORDING_SAVED` | `RECORDING_DISCARDED` | `DISK_SPACE_WARNING` | `DISK_SPACE_CRITICAL` | `ERROR`

#### Usage

```bash
ros2 launch bag_recorder bag_recorder.launch.py
# With custom params:
ros2 launch bag_recorder bag_recorder.launch.py params_file:=/path/to/params.yaml
```

---

## 日本語

PS3/PS4コントローラーでrosbag記録を操作するノードです。安全のためロック解除が必要です。

### ノード: `bag_recorder_node`

#### サブスクライブトピック

| トピック | 型 | 説明 |
|--------|------|------|
| `/joy` | `sensor_msgs/Joy` | PS3/PS4 ジョイスティック入力 |

#### パブリッシュトピック

| トピック | 型 | 説明 |
|--------|------|------|
| `/bag_recorder/status` | `std_msgs/String` | JSON形式のステータス（1Hz） |
| `/bag_recorder/event` | `std_msgs/String` | JSON形式のイベント |

#### ジョイスティック操作

| ボタン | 操作 |
|--------|------|
| △ × 3回 | レコーダーのロック解除（起動時必須） |
| ■（Square） | 記録開始 |
| ●（Circle） | 停止 & 保存 |
| ✕（Cross） | 記録を破棄 |

#### パラメータ

| パラメータ | デフォルト | 説明 |
|-----------|---------|------|
| `record_all_topics` | `true` | 全トピックを記録 |
| `topic_list` | `[]` | 記録するトピックのリスト |
| `save_directory` | `~/rosbag/` | 保存先ディレクトリ |
| `disk_space_warning_threshold_gb` | `10.0` | 警告閾値（GB） |
| `disk_space_critical_threshold_gb` | `3.0` | 記録不可閾値（GB） |

#### 使用方法

```bash
ros2 launch bag_recorder bag_recorder.launch.py
```

#### トラブルシューティング

ボタンが反応しない場合は `ros2 topic echo /joy` でインデックスを確認してください。
記録が開始されない場合はディスク容量（3GB以上必要）と `/bag_recorder/event` を確認してください。
