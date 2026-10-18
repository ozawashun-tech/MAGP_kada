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
| `src/sensors/bno085_imu/`（新規） | `bno085_imu_node` を追加。Game Rotation Vector・ジャイロ・加速度を 50Hz で読み、`/imu/data`（`frame_id=imu_link`）を publish する。接続失敗、10回連続の読み取りエラー、1秒間のデータ停止のいずれでも、ノードを落とさずに再接続する。 |
| `src/sensors/bno085_imu/launch/bno085_imu.launch.py` | ノードと `base_link → imu_link` の静的 TF を起動する。取り付け姿勢は `imu_x`〜`imu_yaw` の引数で指定する。 |
| `src/sensors/bno085_imu/config/bno085_imu_params.yaml` | バス番号・アドレス（`74` = 0x4A）・周期・共分散・再接続の設定。 |
| `launch/autonomous_driving.py` | IMU の launch を追加。 |
| `requirements.txt` | `adafruit-circuitpython-bno08x==1.3.3`、`adafruit-extended-bus==1.0.2` を追加。 |
| `scripts/setup.sh` | 最後の確認項目に上の2モジュールを追加。 |
| `src/tools/bag_recorder/config/bag_recorder_params.yaml` | 記録するトピックに `/imu/data` を追加。学習側の `training_src/dataset.py` はトピック名で絞り込んでいるので影響はない。 |
| `docs/hardware.md`、`README.md`、`README_ja.md` | 配線手順（ステップ 5）、部品表、パッケージ一覧を追記。 |

### 確認状況
- [x] ROS とセンサをスタブに置き換えたロジックテスト（publish の内容・共分散・再接続・データ停止の検出）が通った。
- [x] `colcon build`（Jetson 実機で確認）
- [x] 実機での基本動作（I2C での認識、配信レート、軸の向き）
- [ ] 走行中の加速度の妥当性、rosbag への記録（下の TODO）

### 実機での確認結果（2026-10-02）
- ビルド、I2C での認識、`/imu/data` の配信、`base_link → imu_link` の TF は確認できた。静止時の値も正常（`linear_acceleration.z` ≈ 9.73）。
- `rate_hz: 100` では I2C が飽和し、配信が約 10Hz（間隔 0.002〜0.3 秒）に落ちた。25Hz・50Hz 設定では安定した（50Hz 設定で最大間隔 0.071 秒）。ライブラリは 1 パケットに I2C の読み出しを 3 回使い、3 レポート × 100Hz は 100kHz のバスでは間に合わない。
- 対応: `rate_hz` の既定値を 50 に下げた。publish は「値が変わったとき」から一定周期のタイマーに変更した（3 レポートが別々に届くため、以前は設定値より多く出て、静止時は間引かれていた）。
- 静止させても、データ停止の誤検出による再接続は起きなかった。
- 100Hz が必要になったら、I2C バスを 400kHz にする（デバイスツリーの変更）か、マイコン経由の USB 方式にする。
- 修正後のノードは、単体でも `make run`（床での実走行を含む、約 200 秒）でも平均 50.0Hz で安定した。間隔は 0.002〜0.051 秒、標準偏差は約 0.010 秒で、走行による悪化はなかった。間隔のばらつきは I2C の読み出し時間によるもので、気になる場合はバスの 400kHz 化で改善する。
- 軸の向きは `base_link`（x 前・y 左・z 上）と一致した。加速度・角速度とも符号と軸が正しいので、`imu_roll` / `imu_pitch` / `imu_yaw` は 0 のままでよい。

### 取り付け位置（2026-10-02 実測）
LiDAR（UST-10LX）の回転軸中心・窓の高さ中央を基準に、IMU の位置を測った。精度は 5mm 程度。

| 軸 | 実測 | launch 引数にする場合 |
|---|---|---|
| x（前後） | LiDAR より後ろに 11cm | `imu_x:=-0.11` |
| y（左右） | 0cm | `imu_y:=0.0` |
| z（上下） | LiDAR より上に 2.4cm | `imu_z:=0.024` |

- このリポジトリでは `base_link` と LiDAR のフレームを同じ位置として扱っている（`launch/cartographer.launch.py` の静的 TF がすべて 0）ので、上の値はそのまま `base_link → imu_link` の並進として使える。
- 今は `/imu/data` を記録しているだけで TF を読む処理がないため、launch には設定していない（すべて 0 のまま）。Cartographer で `use_imu_data = true` にするとき、または加速度から旋回時の遠心成分を補正するときに設定する。

### TODO（実機）
1. 走行中に `linear_acceleration` が加減速（x）・旋回（y）に応じて動くか確認する。これまでに確認できたのは静止時の値と、手で傾けたときの反応だけ。
2. 走行中に再接続のログ（`読み取りエラーが続いたため再接続します` など）が出ていないか確認する。多ければマイコン経由の USB 方式を検討する。
3. 走行を rosbag に記録し、`ros2 bag info` で `/imu/data` が約 50Hz 分入っているか確認する。
4. PCA9685 がピン3/5 のバスにつながっていることを実際の配線で確認する。ドキュメントはコードの `board.SCL/SDA` から推測して書いている。
5. IMU を固定する 3D プリントマウントを `hardware/base_plate/` に追加する。
6. IMU の使い道（Cartographer で使う／学習の入力に加える／記録のみ）を決める。
