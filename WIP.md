# WIP

進行中の作業と、実機での確認待ちの項目を記録する。

---

## 2026-10-28 — Auto に切り替えたら推論の履歴を埋め直す（branch: `reset-history-on-auto-v2`、main にマージ済み）

### 目的
スタート地点から走らせるとうまく走るが、途中で止めて少し手前から再開させるとうまく走れない。推論ノードは手動中も `/scan` を溜め続けるため、手動中に車を手で運んでから Auto にすると、運んでいる最中のスキャンが約 4.9 秒間（191 フレーム）入力に残っていた。

オフラインで確かめたこと（`my_model.pth`、bag `14_57_36_2`）:
- 各地点で「その地点のスキャン 1 枚を繰り返した履歴」（＝その場で止めて再開した直後）を入れると、ステアリングの誤差が 23 µs → 142 µs、記録された操作との相関が 0.97 → 0.37 に悪化した。
- 学習で静止した履歴が出てくるのは bag の先頭（スタート地点）だけなので、コース途中の静止履歴は学習データにない入力になる。

この変更（履歴のリセット）は、運んでいる最中のスキャンを捨てて、推論の入力を「bag の先頭と同じ形」にそろえるもの。これだけでは「コース途中での静止履歴」は学習データにないままなので、学習側で再開を模した履歴を混ぜる拡張（オフラインで再開直後の誤差が 103 → 71 µs に改善）と組み合わせる予定。

### 変更内容
- `src/pytorch_pwm_controller/pytorch_pwm_controller/nn_pwm_controller_node.py`:
  - `/mux_pwm/status`（パラメータ `mode_topic`）を購読し、Manual → Auto に変わったら、次に届いたスキャン 1 枚で履歴 191 フレームをすべて埋める。学習で bag の先頭の足りない履歴を最初のフレームで埋めているのと同じ形。
  - Auto → Manual では何もしない。Auto のまま status が届いてもリセットしない。
  - `reset_history_on_auto`（既定 `True`）を `False` にすると従来の動作に戻る。
  - 起動直後で履歴が溜まる前に Auto にした場合も、その時点から推論を始める（従来は約 4.9 秒待っていた）。
  - `Ready for inference` のログは最初の 1 回だけ出す（リセットのたびに出ていたため）。
- `nn_imu_pwm_controller_node.py`（IMU 版）は変更していない。
- 10/28 に一度 main へマージして打ち消した `reset-history-on-auto`（b0f80c4）と同じ内容。

### 確認状況
- [x] rclpy をスタブにした動作確認: Manual → Auto でリセット、Auto → Auto ではリセットしない、Auto → Manual → Auto で再リセット、`reset_history_on_auto: False` で従来どおり、起動直後の Auto でもすぐ推論、不正な status は警告のみ。
- [x] `colcon build --packages-select pytorch_pwm_controller` が通った。
- [x] PC で bag（`14_57_36_2`）の `/scan` を再生し、`ros2 topic pub` で `/mux_pwm/status` を送って確認した（車は動かしていない、`ROS_DOMAIN_ID=77`）。Auto を送るとリセットのログが出て、Auto を続けて送ったときは出ず、Manual → Auto で再び出た。
- [x] 実機での確認: うまく行った（2026-10-28）

### TODO（実機）
1. `make build` → `make run` で起動し、ログに `Reset history on Auto: True (/mux_pwm/status)` が出ることを確認する。
2. PS ボタンで Auto にするたびに `Switched to Auto: scan history reset to the latest scan` が出ることを確認する。
3. 途中で止め、少し手前に車を手で運んで置き、すぐ Auto にして、再開直後の挙動を見る。比べるなら `reset_history_on_auto: False` でも試す。
4. 切り替えた瞬間の 1〜2 フレーム（数十 ms）は、`pwm_mux_node` が切り替え前の `/torch_pwm` を使う。問題になるようなら `pwm_mux_node` 側で Auto 切り替え時に `latest_torch_pwm` を捨てる。

---

## 2026-10-18 — IMU（ヨーレート）モデルの実機走行

### 目的
オフラインで比較した IMU ありモデルと、同じデータで学習し直した LiDAR モデルを、実機で切り替えて走らせて比べる。オフラインでは差が誤差の範囲で、IMU をゼロにしても誤差がほぼ変わらなかった（[experiments.md](model/transformer_imu/experiments.md)）。なので、走りの差は小さいと予想している。

### 変更内容
- `src/pytorch_pwm_controller/pytorch_pwm_controller/nn_imu_pwm_controller_node.py`（新規）: `/scan` と `/imu/data` を購読し、`/torch_pwm` を出す。
  - IMU の値は学習時と同じく「直前の scan から今の scan までの平均」で作る。
  - 履歴長・stride・特徴量は、チェックポイントから読む。
  - IMU が 1 秒以上届かないときや、使う履歴に IMU が止まっていた区間が残っているときは、中立（1620 / 1265）を出す。`pwm_mux_node` は最後の `/torch_pwm` を出し続けるので、publish を止めるだけでは車が止まらないため。
  - IMU が戻っても、止まっていた区間が履歴から抜けるまで（約 5 秒）は中立のまま。
- `.../model/model_imu.py`（新規）: `training_src/model_imu.py` と同じ内容。推論用の `ModelIMU` を含む。
- `src/pytorch_pwm_controller/setup.py`: entry point `nn_imu_pwm_controller_node` を追加した。
- `launch/autonomous_driving_imu.py`（新規）: `model:=imu`（既定）と `model:=lidar` で推論ノードを切り替える。推論ノード以外は `autonomous_driving.py` と同じ構成。
  - `imu`: `imu_20261018_gz_len20_str10_s2_seed2.pth`
  - `lidar`: `lidar_20261018_len20_str10_s2_seed1.pth`（Stage 2 の 3 seed のうち、検証 loss が最も低いもの）
- 既存の `nn_pwm_controller_node.py`・`launch/autonomous_driving.py` は変更していない。`make run` で現行モデルに戻せる。

### 確認状況
- [x] `colcon build --packages-select pytorch_pwm_controller` が通った。
- [x] 検証用の bag（`15_14_47_3`）の `/scan`・`/imu/data` を再生してノードに入れた（車は動かしていない、`ROS_DOMAIN_ID=77`）。出力はオフライン推論と一致した（ステアリングの差は中央値 0.00 µs）。記録された操作との誤差は、ステアリング 114.8 µs・スロットル 56.2 µs で、学習時の値と同じくらい。
- [x] `/scan` だけを再生したとき（IMU なし）は、883 件すべて中立を出し、警告ログが出た。
- [x] `lidar_20261018_len20_str10_s2_seed1.pth` を既存の `nn_pwm_controller_node` で読み込めた。
- [ ] 実機での走行（下の TODO）

### TODO（実機）
1. `make build` のあと、`source install/setup.bash && ros2 launch launch/autonomous_driving_imu.py` で起動する。最初は `torch_gain` を下げ、PS ボタンですぐ手動に戻せる状態で走らせる。
2. 起動ログに `Ready for inference` と `IMU OK: 推論を再開します` が出ることを確認する。
3. 走行中に `IMU が使えないため、中立の PWM を出します` が出ないかを確認する。出たら、bno085 側の再接続ログも確認する。
4. 同じコースで `model:=lidar` に切り替えて走らせ、ラップタイム・コースアウトの回数・ステアリングの振れを比べる。結果は `experiments.md` に追記する。
5. 走行中に IMU のケーブルを抜く（または bno085 のノードを止める）と、1 秒ほどで止まることを確認する。必ず車を浮かせた状態で行う。

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
