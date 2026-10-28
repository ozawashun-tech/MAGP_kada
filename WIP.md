# WIP

進行中の作業と、実機での確認待ちの項目を記録する。

---

## 2026-10-19 — L2 補正区間を学習で重く扱う（branch: `l2-intervention-weighting`）

### 目的
自動走行中に L2 で補正したデータで学習し直し、モデルが苦手な場面を直す（HG-DAgger に近いやり方）。今までの bag には L2 やモードの情報が無く、自動で L2 を押していない区間（教師がモデル自身の出力）も同じ重みで学習していた。

### 変更内容
- `src/mux_pwm/mux_pwm/pwm_mux_node.py`: `/mux_pwm` を出すたびに、その出どころを `/mux_pwm/source`（`std_msgs/String`）に出す。値は `manual` / `auto` / `auto_l2`（自動で L2 を押している間。スティックが中立でも含む）。既存のトピックと動作は変えていない。
- `src/tools/bag_recorder/config/bag_recorder_params.yaml`: `topic_list` に `/mux_pwm/source` を追加した。
- `training_src/dataset.py`:
  - `/pwm_input` の各行に、その時刻以前で最後の `/mux_pwm/source` を `source` 列として付ける。
  - `DataPreprocessConfig(source_weights=...)` でラベルごとの重みを決める。重み 0 のサンプルは作らない（検証と正規化統計からも外れる）。重みは教師の行で決める。
  - ラベルの無い bag は全サンプル重み 1.0 で、今までと同じ結果になる。
- `training_src/train.py`: `source_weights={"manual": 1.0, "auto": 0.0, "auto_l2": 3.0}`。学習データの重みが混ざるときは `WeightedRandomSampler` で重みに比例した頻度でサンプルを選ぶ。検証は重みなし。
- `training_src/dataset_imu.py`: IMU の欠損でサンプルを絞るときに、重みの配列も同じく絞るようにした（IMU 系の挙動は変えていない）。

### 確認状況
- [x] 既存の bag（`rosbag2_2026_10_14-10_15_03_1`）で、変更前の `dataset.py` とサンプル・教師が完全に一致した（`source_weights` の有無どちらでも）。
- [x] 合成 bag（manual → auto → auto_l2 → auto）で、`auto` の除外、`auto_l2` の重み 3.0、`RosbagIMUDataset` で重みと教師の対応が保たれること、サンプラーの選ばれる比率が期待値どおりなことを確かめた。
- [ ] 実機での `/mux_pwm/source` の確認（下の TODO）

### TODO（実機）
1. `make build` のあと `make run` で起動する（`bag_recorder_params.yaml` を反映させるため、ビルドが必要）。
2. `ros2 topic echo /mux_pwm/source` で、PS ボタンで `manual` ⇔ `auto` が、自動中に L2 を押している間は `auto_l2` が出ることを確認する。
3. `ros2 topic hz /mux_pwm/source` が `/mux_pwm` と同じくらいであることを確認する。
4. 自動走行＋L2 補正で bag を記録し、`metadata.yaml` に `/mux_pwm/source` があることを確認する。
5. 手動走行の bag と補正走行の bag を混ぜて学習する。`train.py` は `BASE_BAGS`（毎回含める bag）と Jetson の最新 bag を合わせて学習し、`model/transformer/my_model_<日時>.pth` に保存する。補正走行の bag は、使い終わったら `BASE_BAGS` に足して積み重ねる。`model/checkpoints/` は毎回上書きされる。

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
