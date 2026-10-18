---
name: imu-imitation-learning
description: 模倣学習モデルに IMU（BNO085, /imu/data）の情報を取り入れる実験の進め方。IMU 入りのデータセット・モデル・学習スクリプト・推論ノードを作る・直すとき、IMU モデルの学習結果を評価・比較するとき、IMU モデルを実機で試すときに使う。
---

# IMU を取り入れた模倣学習の実験

LiDAR のみの現行モデルを残したまま、IMU 入りのモデルを別系統として作り、比較しながら改良する。
1 回で完成させるのではなく、「データ確認 → 実装 → 学習 → 比較 → 記録」を 1 サイクルとして小さく回す。

## 絶対に守ること（現行モデルの保護）

- 次のものを削除・上書き・挙動変更しない:
  - 既存の `.pth` すべて（`model/transformer/`、`model/checkpoints/`、`src/pytorch_pwm_controller/pytorch_pwm_controller/model/`、`/home/jetson/models/`）
  - `training_src/model.py`、`dataset.py`、`train.py`
  - `nn_pwm_controller_node.py`、`launch/autonomous_driving.py`
- IMU 用のコードは新しいファイルに書き、既存モジュールは **import して再利用** する（コピーして改変しない）。既存ファイルの変更がどうしても必要なときは、理由を説明してユーザーの確認を取る。その場合も、デフォルトの挙動は変えない。
- `train.py` は実行するたびに `model/checkpoints/best_model.pth`・`epoch_*.pth` と `model_save_path` を上書きする。比較用のベースラインを学習するときも `train.py` をそのまま走らせず、IMU 側の学習スクリプトから「IMU なし」の設定で学習して、IMU 側の保存先に出す。
- 保存先は IMU 実験専用にする:
  - 完成モデル: `model/transformer_imu/<run名>.pth`
  - チェックポイント: `model/checkpoints_imu/<run名>/`
  - run 名の例: `imu_20261020_gz-ax-ay_len20_str10`（日付 + IMU の特徴量 + 履歴設定）。同じ名前のファイルがあれば上書きせず、止めて確認する。

## 前提となる事実

- `/imu/data`（`sensor_msgs/Imu`, `frame_id=imu_link`）は約 50Hz で配信される。`/scan` は UST-10LX で約 40Hz。
- `bag_recorder` の記録対象には `/imu/data` が入っている（`src/tools/bag_recorder/config/bag_recorder_params.yaml`）。ただし、2026-10-18 時点で `dataset/train/` と `~/rosbag/` にある bag には **`/imu/data` が 1 本も入っていない**。学習の前に IMU 入りのデータを集める必要がある。
- `/pwm_input` しか入っていない bag もある。学習に使う bag は、必ずトピックを確認してから選ぶ。
- 姿勢は Game Rotation Vector（地磁気なし）なので、**ヨー角の絶対値はドリフトする** うえにコース上の位置と結びつかない。入力にはしない。

## 手順

### 1. データの確認（毎回最初にやる）

```bash
grep -oE 'name: /[a-z_/]+' dataset/train/*/metadata.yaml ~/rosbag/*/metadata.yaml
```

- `/scan`・`/pwm_input`・`/imu/data` の 3 つがそろった bag だけを使う。そろった bag がなければ、ユーザーにデータ収集を依頼して止まる（`make run` → ジョイスティックで記録）。
- 収集の前に、実機で `ros2 topic hz /imu/data` が約 50Hz であることを確認してもらう。IMU が再接続を繰り返しているとデータが欠ける。
- 各 bag の IMU の欠損（1 秒以上途切れた区間）を数えて報告する。欠損が多い bag は除外を提案する。

### 2. データセット（`training_src/dataset_imu.py`）

- `dataset.load_rosbag_data` 相当の読み込みで `/imu/data` も読む。読み込み関数は新しく書いてよいが、`synchronize_data` / `trim_data` / `filter_stops` / `preprocess_data` は `dataset.py` から import して使う。
- IMU の特徴量は、前処理の **前に** scan のタイムスタンプに合わせて `scan_df` の列として付け足す。こうすると、既存の同期・トリミング・停止区間の除去で scan と同じ行が残り、ずれない。
  - 合わせ方は「直前の scan から今の scan までの IMU の平均」を基本にする。推論ノードでもまったく同じ計算をすること。
- 最初の特徴量は `angular_velocity.z`（ヨーレート）と `linear_acceleration.x/y` にする。増やすのは、比較で効果が見えてから 1 つずつにする。
- `RosbagDataset` を継承し、同じ `history_indices` で IMU の履歴も取り出す。`__getitem__` は `(scan_history, imu_history, targets)` を返す。scan へのノイズ付加はそのまま使い、IMU には別の小さいノイズを足すかどうかを設定で選べるようにする。

### 3. モデル（`training_src/model_imu.py`）

- `model.DNN` を継承した `DNNIMU` を作る。IMU を `Linear(imu_dim, d_model)` で埋め込み、各時刻の scan 埋め込みに足してから Transformer に入れる方式から始める。
- 正規化は scan とは別に、IMU の **チャンネルごと** に mean/std を取る（scan は全体で 1 つのスカラーなので流用できない）。
- チェックポイントは、推論側が設定を取り違えないように、中身だけで設定が分かる形にする。既存のキーに加えて、次を入れる:
  `model_type: "transformer_imu"`, `imu_features`, `imu_mean`, `imu_std`, `scan_history_length`, `scan_history_stride`, `prediction_steps`

### 4. 学習（`training_src/train_imu.py`）

- `train.py` の `WeightedMSELoss` と `compute_normalization_stats` は import して使う。
- `use_imu=False` で、同じデータ・同じ分割・同じハイパーパラメータのまま LiDAR のみのモデルを学習できるようにする。これをベースラインにする。
- **検証データは bag 単位で分ける**（例: 最新の 1 本を検証用にする）。既存の `random_split` は履歴が重なったサンプルが学習と検証に分かれて入るので、val loss が楽観的になり、比較に向かない。
- 実行例: `cd training_src && python train_imu.py`（設定はファイル末尾で指定する。`train.py` に合わせる）

### 5. 評価と比較（1 サイクルの最後に必ずやる）

学習の後に、次をユーザーに表で報告する。

| 指標 | ベースライン（LiDAR のみ） | IMU あり |
|---|---|---|
| 検証 loss（正規化後） | | |
| スロットルの MAE（µs） | | |
| ステアリングの MAE（µs） | | |
| IMU をゼロにしたときのステアリング MAE | — | |

- 最後の行は、モデルが IMU を実際に使っているかの確認に使う。IMU をゼロ（正規化後の平均値）にしても誤差がほとんど変わらなければ、IMU は効いていない。
- 差が小さいときは、改善とは書かず「誤差の範囲」と書く。可能なら seed を変えて 2〜3 回学習し、ばらつきも示す。
- 結果は `model/transformer_imu/experiments.md` に追記する。形式は `model.md` に合わせる（run 名・使った bag・特徴量・パラメータ・指標・所感）。うまくいかなかった試行も消さずに残す。

### 6. 実機で試すとき

- オフラインの比較で IMU ありがベースライン以上になってから進む。
- 推論は新しいノード `nn_imu_pwm_controller_node`（`pytorch_pwm_controller` パッケージに追加し、`setup.py` に entry point を足す）と、新しい launch `launch/autonomous_driving_imu.py` で行う。既存のノードと launch には触らないので、`make run` でいつでも現行モデルに戻せる。
- 推論ノードの `model_imu.py` は `training_src/model_imu.py` と同じ内容にする（既存の model.py と同じく 2 か所で管理する）。
- 推論ノードでは、IMU が `stale` になった（1 秒以上届かない）ときの挙動を決めておく。学習で IMU の欠損を扱っていないなら、推論を止めて警告を出す。ゼロで埋めて走らせない。
- 実機での確認項目は `WIP.md` に「変更内容 / 確認状況 / TODO」の形式で書く。最初は `torch_gain` を下げて、PS ボタンですぐ手動に戻せる状態で走らせるよう、ユーザーに伝える。
