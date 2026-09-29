# WIP

進行中の作業と、実機での確認待ちの項目を記録する。

---

## 2026-09-29 — IMU（BNO085）の接続（branch: `imu`）

### 目的
BNO085 を Jetson Orin NX に接続して `sensor_msgs/Imu` を出す。学習データの記録や、Cartographer の `use_imu_data` で使うことを想定している。

### 接続方式（方式A: I2C 直結、PCA9685 とは別バス）
- BNO085 は **40ピンの27/28番（I2C0、通常 `/dev/i2c-1`）** につなぐ。PCA9685 はピン3/5 のまま。
- バスを分ける理由: BNO085 はクロックストレッチを使うため、バスを固めることがある。同じバスに相乗りすると、スロットル・ステアリングの出力も止まってしまう。
- 検討したほかの方式:
  - UART-RVC: 角速度が取れないので見送り。
  - マイコン経由の USB: 方式Aが不安定だった場合の代替案として残す。

### 変更内容
| ファイル | 内容 |
|---|---|
| `src/sensors/bno085_imu/`（新規） | `bno085_imu_node` を追加。Game Rotation Vector・ジャイロ・加速度を 100Hz で読み、`/imu/data`（`frame_id=imu_link`）を publish する。接続失敗、10回連続の読み取りエラー、1秒間のデータ停止のいずれでも、ノードを落とさずに再接続する。 |
| `src/sensors/bno085_imu/launch/bno085_imu.launch.py` | ノードと `base_link → imu_link` の静的 TF を起動する。取り付け姿勢は `imu_x`〜`imu_yaw` の引数で指定する。 |
| `src/sensors/bno085_imu/config/bno085_imu_params.yaml` | バス番号・アドレス（`74` = 0x4A）・周期・共分散・再接続の設定。 |
| `launch/autonomous_driving.py` | IMU の launch を追加。 |
| `requirements.txt` | `adafruit-circuitpython-bno08x==1.3.3`、`adafruit-extended-bus==1.0.2` を追加。 |
| `scripts/setup.sh` | 最後の確認項目に上の2モジュールを追加。 |
| `src/tools/bag_recorder/config/bag_recorder_params.yaml` | 記録するトピックに `/imu/data` を追加。学習側の `training_src/dataset.py` はトピック名で絞り込んでいるので影響はない。 |
| `docs/hardware.md`、`README.md`、`README_ja.md` | 配線手順（ステップ 5）、部品表、パッケージ一覧を追記。 |

### 確認状況
- [x] ROS とセンサをスタブに置き換えたロジックテスト（publish の内容・共分散・再接続・データ停止の検出）が通った。
- [ ] `colcon build`（開発用の WSL に ROS / Docker がないため未実施）
- [ ] 実機での動作確認（下の TODO）

### TODO（実機）
1. `bash scripts/setup.sh` → `make build` が通るか確認する。
2. `i2cdetect -l` と `i2cdetect -y -r 1` で 0x4A が見えるか確認する。違うバスなら params の `i2c_bus` を直す。
3. `ros2 launch bno085_imu bno085_imu.launch.py` のあと、`ros2 topic hz /imu/data` で約 100Hz 出ているか確認する。
4. 車体を左に回したとき `angular_velocity.z` が正になるか確認する。ずれていれば `imu_roll` / `imu_pitch` / `imu_yaw` で補正する。
5. 走行中に I2C エラーのログが多ければ、マイコン経由の USB 方式を検討する。
6. PCA9685 がピン3/5 のバスにつながっていることを実際の配線で確認する。ドキュメントはコードの `board.SCL/SDA` から推測して書いている。
7. IMU を固定する 3D プリントマウントを `hardware/base_plate/` に追加する。
