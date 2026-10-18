# IMU 実験の記録

IMU を取り入れたモデル（`training_src/train_imu.py`）と、同じ手順で学習したベースライン（LiDAR のみ）の記録。
うまくいかなかった試行も消さずに残す。

## モデル構造

- LiDAR Encoder（`DNN` の `scan_embedding` / `pos_encoder` / `transformer_encoder`）の出力 (L × 128) と、IMU 用 MLP の出力 (32) を、回帰ヘッド（`fc1`〜`fc3`）の直前で結合する。
- IMU の入力はヨーレート（`angular_velocity.z`）。scan の履歴と同じ時刻の値を使う。各値は「直前の scan から今の scan までの IMU の平均」。
- Stage 1: 学習済みの LiDAR モデルで初期化し、Encoder を凍結して、回帰ヘッドと IMU 用 MLP を学習する。`fc1` の IMU 側の列は 0 から始める。
- Stage 2（任意）: 全体を小さい学習率でファインチューニングする。
- 学習中は、確率 `imu_dropout_p` で IMU 入力をゼロにする（modality dropout）。

## 記入例

### imu_YYYYMMDD_gz_len20_str10_s1
- 比較対象: lidar_YYYYMMDD_len20_str10_s1
- 初期値: `model/transformer/my_model.pth`
- 学習用 bag: rosbag2_...
- 検証用 bag: rosbag2_...
```
scan_history_length=20,
scan_history_stride=10,
imu_hidden=32,
imu_dropout_p=0.25,
epochs=100,
learning_rate=1e-4,
finetune_epochs=0,
seed=42,
```

| 指標 | ベースライン（LiDAR のみ） | IMU あり |
|---|---|---|
| 検証 loss（正規化後） | | |
| スロットルの MAE（µs） | | |
| ステアリングの MAE（µs） | | |
| IMU をゼロにしたときのステアリング MAE | — | |

所感:

---

## 2026-10-18: ヨーレート（gz）1 チャンネル、seed 3 本での比較

### 共通の条件
- 初期値: Jetson の `/home/jetson/models/my_model.pth`（10/14 に学習、launch で使っているモデル）。PC には `model/pretrained_from_jetson/my_model_jetson_20261014.pth` としてコピーした（md5 `248231e8…` で一致を確認）。10/18 の bag は学習に使われていない。
- 学習用 bag: rosbag2_2026_10_18-15_09_54_1（148 秒）、rosbag2_2026_10_18-15_13_07_2（92 秒）
- 検証用 bag: rosbag2_2026_10_18-15_14_47_3（92 秒、約 3600 サンプル）
- 3 本とも IMU は 49.8 Hz、1 秒以上の欠損なし。直前の IMU が 0.1 秒より古いフレームはどの bag も 1% 未満なので、除外しなかった。
- 学習は PC の CPU で行った（GPU が認識されなかったため）。1 回 2〜5 分。
- 実行: `cd training_src && python train_imu.py [--no-imu] [--seed N] [--run-name NAME] [--finetune-epochs 10]`。ログは各 run 名の `.log`。
```
scan_history_length=20,
scan_history_stride=10,
imu_hidden=32,
imu_dropout_p=0.25,
batch_size=32,
epochs=100,
learning_rate=1e-4,
early_stopping_patience=10,
finetune_epochs=0（Stage 1）/ 10（Stage 2）,
finetune_lr=1e-5,
seed=42, 1, 2,
```

### run 名
| | ベースライン（LiDAR のみ） | IMU あり |
|---|---|---|
| Stage 1, seed 42 | lidar_20261018_len20_str10_s1 | imu_20261018_gz_len20_str10_s1 |
| Stage 1, seed 1 | lidar_20261018_len20_str10_s1_seed1 | imu_20261018_gz_len20_str10_s1_seed1 |
| Stage 1, seed 2 | lidar_20261018_len20_str10_s1_seed2 | imu_20261018_gz_len20_str10_s1_seed2 |
| Stage 2, seed 42 | lidar_20261018_len20_str10_s2 | imu_20261018_gz_len20_str10_s2 |
| Stage 2, seed 1 | lidar_20261018_len20_str10_s2_seed1 | imu_20261018_gz_len20_str10_s2_seed1 |
| Stage 2, seed 2 | lidar_20261018_len20_str10_s2_seed2 | imu_20261018_gz_len20_str10_s2_seed2 |

### 初期値のモデルそのまま（pretrained）
検証用 bag で val loss 0.3918、スロットル MAE 55.7 µs、ステアリング MAE 143.7 µs。

### Stage 1（Encoder 凍結）
| 指標 | ベースライン（seed 42 / 1 / 2） | 平均 ± 標準偏差 | IMU あり（seed 42 / 1 / 2） | 平均 ± 標準偏差 |
|---|---|---|---|---|
| 検証 loss（正規化後） | 0.2605 / 0.2756 / 0.2729 | 0.2697 ± 0.0081 | 0.2655 / 0.2566 / 0.2657 | 0.2626 ± 0.0052 |
| スロットルの MAE（µs） | 50.3 / 69.7 / 67.6 | 62.5 ± 10.6 | 59.9 / 51.6 / 64.6 | 58.7 ± 6.6 |
| ステアリングの MAE（µs） | 119.7 / 114.5 / 118.5 | 117.5 ± 2.7 | 119.7 / 115.2 / 112.3 | 115.7 ± 3.7 |
| IMU をゼロにしたときのステアリング MAE | — | — | 120.7 / 116.0 / 112.2 | 116.3 ± 4.3 |

### Stage 2（全体を lr=1e-5 で 10 エポック）
| 指標 | ベースライン（seed 42 / 1 / 2） | 平均 ± 標準偏差 | IMU あり（seed 42 / 1 / 2） | 平均 ± 標準偏差 |
|---|---|---|---|---|
| 検証 loss（正規化後） | 0.2605 / 0.2589 / 0.2704 | 0.2633 ± 0.0062 | 0.2633 / 0.2566 / 0.2548 | 0.2582 ± 0.0045 |
| スロットルの MAE（µs） | 50.3 / 62.6 / 64.2 | 59.0 ± 7.6 | 60.4 / 51.6 / 61.0 | 57.7 ± 5.3 |
| ステアリングの MAE（µs） | 119.7 / 116.5 / 116.7 | 117.6 ± 1.8 | 117.6 / 115.2 / 113.2 | 115.4 ± 2.2 |
| IMU をゼロにしたときのステアリング MAE | — | — | 117.9 / 116.0 / 112.7 | 115.5 ± 2.6 |

- Stage 2 で val loss が下がらなかった run（ベースライン seed 42、IMU あり seed 1）は、Stage 1 のベストがそのまま残っている。
- 標準偏差は 3 本から求めた標本標準偏差。

### 所感
- 10/18 のデータで学習し直すと、どちらも初期値より大きく良くなる（val loss 0.39 → 0.26 前後、ステアリング MAE 144 → 116 µs 前後）。これは新しい日のデータに合わせた効果で、IMU とは関係ない。
- IMU ありとベースラインの差は、val loss で 0.005〜0.007、ステアリング MAE で 2 µs 前後。seed によるばらつき（val loss 0.005〜0.008、ステアリング 2〜4 µs）と同じくらいなので、**改善ではなく誤差の範囲**。
- **IMU をゼロにしてもステアリング MAE はほぼ変わらない**（平均で Stage 1 は +0.6 µs、Stage 2 は +0.2 µs。seed 2 ではゼロにした方がわずかに良い）。モデルはヨーレートをほとんど使えていない。IMU ありの方がわずかに良く見えるのも、IMU の情報ではなく、パラメータが増えたことや乱数の違いによるものと考えるのが自然。
- エポックごとの val loss の上下（0.02〜0.03）が、モデル間の差より大きい。しかも、早期終了でベストを選ぶのに使う bag と、成績を報告する bag が同じなので、どちらの数値も少し楽観的になっている（条件は両者で同じ）。
- 次に試すこと（案）: 学習用のデータを増やす（現状 4 分）。ヨーレートは scan の履歴（壁の見え方の回転）からほぼ推定できてしまうので、効かないのかもしれない（未検証の仮説）。scan からは読み取りにくい特徴量（加速度 `linear_acceleration.x/y` など）を 1 つずつ足して比べる。

### 注意: 10/18 に train.py で my_model.pth を学習し直した
- `model/transformer/my_model.pth` と `model/checkpoints/` は、10/18 に `train.py` をそのまま実行して、rosbag2_2026_10_18-15_14_47_3 の 1 本だけからゼロから学習し直したもの（最良の val loss 0.0840。bag 内の random_split なので、上の表の値とは比べられない）。
- この bag は上の比較の検証用なので、このモデルを IMU 比較の初期値に使わない。初期値には `model/pretrained_from_jetson/my_model_jetson_20261014.pth` を使う（学習し直す前の `my_model.pth`、および Jetson の `/home/jetson/models/my_model.pth` と md5 が同じ）。
