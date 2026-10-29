# WIP

進行中の作業と、実機での確認待ちの項目を記録する。

---

## 2026-10-29 — 壁に当たると自動走行が速くなる問題（branch: `L2`）

### 症状と原因
- 自動走行で壁に当たると、それを境に速くなる。`rosbag2_2026_10_29-15_18_56_1`（`my_model_20261029_160500.pth`）では、通常 1515〜1525 のモーターが、学習データに無い景色に入った 38.4 秒から下がり始めた。衝突（39.7 秒）の後、壁に押し付けられて止まっている間も 1435〜1445 のままだった。
- bag をオフラインでモデルにかけ直すと、記録された値がそのまま再現する。原因は mux・ゲイン・ESC ではなく、モデルの出力。
- 原因は 3 つ。
  1. 学習データ（手動 94.5 秒）に、停止・衝突の場面が無い。同じスキャンを並べた「止まっている」入力でも、1401〜1503 の速い値が出る。
  2. 教師の正規化が、モーターとステアリングをまとめた 1 つの平均・標準偏差（1341 / 204）だった。モーター自体の std は約 6 なので、モーターの誤差が損失にほとんど効かず、学習範囲外の入力で大きくずれる。
  3. L2 補正の bag では、壁に押し付けられている区間の教師が、モデル自身の速い出力（重み 3.0）になる。

### 変更内容
| ファイル | 内容 |
|---|---|
| `launch/autonomous_driving.py` | `pwm_pca9685_controller` の `speed_center`（前進の速度リミットの初期値）を 1120 → 1490 にする。手動・自動の両方に掛かる。十字キー上で今までどおり速くできる。 |
| `training_src/train.py` | `compute_normalization_stats(per_dim_target=True)` で、`target_mean` / `target_std` を次元ごと（形状 (2,)）にする。推論側はブロードキャストでそのまま動き、既存の `.pth` も読める。`train_imu.py` は今までどおり（引数の既定値は False）。 |
| `training_src/dataset.py` | `exclude_collisions` を追加。前方 ±15° に 0.1m 未満の行と、止まっている行（隣り合うスキャンの差の中央値が、直近 20 フレームの平均で 8mm 未満）、およびその後 40 フレームの `sample_weight` を 0 にする。履歴がつながらないよう、行は消さない。`train.py` の `DataPreprocessConfig` で有効にしている。 |

### 確認状況（PC）
- [x] 除外の判定: 衝突 bag では壁に押し付けられた L2 区間（モーター 1444）だけが外れた。手動の bag で外れたのは、走り出し前と最後の停止区間だけだった（`15_02_57` と `14_37_54` は 0 行）。
- [x] 同じ bag（`15_02_57_1`）で学習し直し、衝突 bag を再生した。38〜41 秒の出力は 1515〜1518（旧モデルは 1422〜1504）。止まった入力では 1511〜1518（旧モデルは 1401〜1524）。ステアリングは旧モデルと相関 0.90 で、大きくは変わらなかった。
- 新しいモデルのモーターは、ほぼ一定（std 0.6）になった。教師のモーター自体がほぼ一定（std 3.5）なので、平均を出しているのに近い。場面に応じて速度を変える学習は、今後の課題。

### TODO（実機）
1. `make run` で速度リミットの初期値が 1490 になっていることをログ（`初期速度リミット: 1490`）で確認する。
2. `make dagger` で学習・デプロイし（新しい bag `15_18_56_1` がセッションに追加される）、壁に当たっても速くならないかを確認する。
3. L2 で補正するときは、スロットルのスティックに触らない（触ると、その場面で加速することを重み 3.0 で学習する）。

---

## 2026-10-28 — L2 補正の bag で学習し直すサイクル（branch: `L2`）

### 目的
自動走行中に L2 で補正し、その bag で学習し直したモデルを、すぐ実機に入れて走らせるサイクルを回す（HG-DAgger に近いやり方）。本番は時間が無いので、コードの編集や長い CLI を打たずに、PC の `make dagger` 1 回と、車の PS ボタンだけで回せるようにする。

本番での 1 サイクル:
1. 車: 自動で走らせ、まずい所で L2 を押しながら補正する。記録は今までどおりジョイスティックで開始・停止する。
2. PC: `make dagger`（Jetson の新しい bag をセッションに足す → 学習 → Jetson に送って `current.pth` を付け替える）。
3. 車: Manual のまま 1 秒ほど待つと、推論ノードが新しいモデルを読み込む。PS ボタンで Auto にして走らせる。
- コースや走る向きが変わったら `make new-session`。新しいモデルの走りが悪ければ `make rollback`。

### 変更内容
- 10/28 に打ち消した `l2-intervention-weighting`（57c5dd8・d23e62f）を入れ直した。`BASE_BAGS`（逆回りの bag を混ぜて悪くなった）と、bag・`.dockerignore` を管理から外す変更は入れていない。
  - `pwm_mux_node`: `/mux_pwm/source`（`manual` / `auto` / `auto_l2`）を出す。`bag_recorder` の記録対象にも加えた。
  - `dataset.py` / `dataset_imu.py` / `train.py`: `source_weights={"manual": 1.0, "auto": 0.0, "auto_l2": 3.0}` で重み付けし、重みが混ざると `WeightedRandomSampler` を使う。ラベルの無い bag は全部 1.0。
- `training_src/jetson_sync.py`:
  - `new_session()`: `dataset/train/session.txt`（git 管理外）を作り直す。今 Jetson にある bag は `#` 付き（使わない）で書く。前の session.txt は `session_<日時>.txt` に残す。
  - `sync_session()`: Jetson にあって session.txt に無い bag を書き足し、`#` の無い bag をすべて rsync して返す。記録中の bag（`metadata.yaml` が無い）は次回に回る。Jetson に接続できなければエラーで止まる。
  - `deploy_model()`: モデルを `/home/jetson/models/` に一時名で送ってから名前を付け、`current.pth` を付け替える。前の行き先は `previous.pth` に残す（初回は `my_model.pth`）。既存のファイルは上書きしない。
  - `rollback_model()`: `current.pth` と `previous.pth` の行き先を入れ替える。
- `training_src/train.py`: `--session`（session.txt の bag で学習）と `--deploy` を追加した。保存先は `model/transformer/my_model_<日時>.pth`（今までは `my_model.pth` を上書きしていた）。引数なしなら従来どおり最新の bag 1 本で学習する。
- `nn_pwm_controller_node.py`:
  - `model_path` が無ければ `fallback_model_path` を使う。
  - 1 秒ごと（`model_check_period`）に、モデルファイルの実体のパスと更新時刻を見て、変わっていたら Manual 中に読み込み直す。Auto 中は Manual に戻るまで待つ（読み込みの間は推論が止まり、`pwm_mux_node` が古い `/torch_pwm` を出し続けるため）。読み込みに失敗したら前のモデルのまま。`reload_model: False` で無効。
- `launch/autonomous_driving.py`: `model_path` を `/home/jetson/models/current.pth`、`fallback_model_path` を `my_model.pth` にした。deploy するまでは今までと同じモデルで走る。
- `Makefile`: PC 用に `make dagger` / `make new-session` / `make rollback` を追加した。

### 確認状況
- [x] ssh・rsync をローカルで動く偽物にして、`jetson_sync.py` を確認した: new-session 前の sync はエラー、new-session 後は既存の bag を使わない、新しい bag だけ書き足す、記録中の bag は飛ばす、`#` を消した bag は使う、deploy で `current.pth` / `previous.pth` が期待どおりに付け替わる、同名のモデルは上書きせず止まる、rollback を 2 回で元に戻る。
- [x] rclpy と Model をスタブにして推論ノードを確認した: current.pth が無ければ fallback、Manual 中の付け替えで読み込み直す、Auto 中は Manual まで待つ（ログは 1 回）、壊れたファイルは前のモデルのまま（エラーは 1 回）、rollback と同じパスの上書きでも読み込み直す、Auto 切り替え時の履歴のリセットは従来どおり。
- [ ] この PC には torch・ROS が無いため、学習（`train.py`）と `colcon build` は動かしていない。`dataset.py` の重み付けは 10/19 に確認したものと同じ。
- [ ] 実機での確認（下の TODO）

### TODO（実機・練習コース）
1. PC から `ssh jetson` で入れるようにする（`~/.ssh/config` か `MAGP_JETSON_HOST`、鍵認証。`BatchMode=yes` なのでパスワードは聞かれない）。本番の会場でも PC と Jetson が同じネットワークにつながるか確かめておく。
2. Jetson で `make build` → `make run`。ログに `my_model.pth` を使う旨の警告と `Reload model in Manual: True` が出ることを確認する。
3. `ros2 topic echo /mux_pwm/source` で `manual` / `auto` / `auto_l2` が切り替わることを確認する。
4. PC で `make new-session` → 手動走行を記録 → `make dagger`。ログの `Source weights` を見て、学習・転送にかかった時間を測る。
5. Manual のままで `Model reloaded: ...` が出ることを確認し、Auto で走らせる。Auto 中に `make dagger` が終わった場合は、Manual に戻した時点で読み込まれることを確認する。
6. 自動走行＋L2 補正を記録 → `make dagger` を 2〜3 回繰り返し、走りが良くなるかを見る。悪くなったら `make rollback`。1 サイクルの時間が長すぎる場合は、前回のモデルから学習を続ける（fine-tune）方式を検討する。

### 運用の注意
- `auto_l2` の教師のモーター値には `torch_gain` が掛かっている。補正走行中の `torch_gain` は学習させたい値（普通は 1.0）にしておく。
- L2 を押している間は、スティックが中立でもモデルの出力が 3 倍の重みの教師になる。補正が終わったら L2 を離す。
- 補正走行で失敗した bag は、記録を破棄するか、session.txt で行頭に `#` を付けて外す。

---

## 2026-10-28 — Auto に切り替えたら推論の履歴を埋め直す（branch: `reset-history-on-auto-v2`）

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
- [ ] 実機での確認（下の TODO）

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
