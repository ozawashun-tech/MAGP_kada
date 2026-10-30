# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

2D LiDAR（北陽 UST-10LX）のスキャン履歴から、PyTorch Transformer がスロットルとステアリングを直接出力する、E2E 模倣学習の RC カー（MAGP 向け）。実機は Jetson Orin NX（Ubuntu 22.04 / ROS 2 Humble / Python 3.10）。ユーザーへの返答とドキュメントは日本語で書く。

## コマンド

```bash
bash scripts/setup.sh          # 環境構築（apt・pip・torch）。pip を直接叩かず必ずこれを使う
                               # MAGP_SKIP_ROS=1 / MAGP_SKIP_TORCH=1 / MAGP_TORCH_FLAVOR=cu121 で上書きできる
make build                     # colcon build --symlink-install
make run                       # source install/setup.bash && ros2 launch launch/autonomous_driving.py
make clean                     # build / install / log を削除
colcon build --symlink-install --packages-select <pkg>   # 1 パッケージだけビルド

cd training_src && python3 train.py  # 学習（パラメータは train.py 末尾の train_model(...) を直接編集する）

# L2 補正の繰り返し（PC で実行。SSH で Jetson に接続する）
make new-session               # コースが変わったとき。今 Jetson にある bag は学習に使わず、モデルの枠も空にする
make dagger c                  # 新しい bag を c（時計回り）としてセッションに足し、c の bag で学習して origin_clockwise の枠に送る
make dagger cc                 # 同じく cc（反時計回り）。origin_counter_clockwise の枠に送る
make mirror c                  # c の bag を左右反転して学習し、mirror_counter_clockwise の枠に送る（新しい bag は足さない）
make mirror cc                 # cc の bag から mirror_clockwise の枠に送る
make rollback c                # origin_clockwise の枠を 1 つ前のモデルに戻す（もう一度で元に戻る。cc も同じ）
```

- `requirements.txt` には torch を入れない。PC と Jetson で wheel が違うため、`setup.sh` が環境を判別して入れる（Jetson は jetson-ai-lab の index を使う。公式 PyPI 版では GPU が使えない）。
- 自前パッケージにテストはない（`src/urg_node2/test` はサードパーティのもの）。動作確認は実機で `ros2 topic hz` / `ros2 topic echo` を使う。
- `script/` は単体起動用のシェル（手動走行、Cartographer でのマッピング、マップ保存）。`scripts/` はセットアップ用。

## アーキテクチャ

ROS 2 の ament_python パッケージ群（`src/` 以下）。`urg_node2` だけは C++ のサードパーティ製（Apache 2.0）なので、基本的に手を入れない。

### 制御のデータフロー

```
/scan ─► nn_pwm_controller_node ─► /torch_pwm ─┐
/joy0, /joy1 ─► joy_mux ─► /joy ───────────────┼─► pwm_mux_node ─► /mux_pwm ─► pwm_pca9685_controller ─► PCA9685(I2C)
                                               │                                     └─► /pwm_input（実際に出した値）
```

- PWM 系のトピックはすべて `geometry_msgs/TwistStamped` で、**`twist.linear.x` = モーター、`twist.angular.z` = ステアリング**。値は正規化値ではなく **PWM パルス幅（µs）** そのもの（例: モーター中立 1620、ステアリング中央 1265）。
- `pwm_mux_node`（[src/mux_pwm/](src/mux_pwm/)）: PS ボタンで手動/自動を切り替える。自動中に L2 を押している間は、モデル出力に手動入力を加算する（シェアードコントロール）。R2 を押しながら十字キー左右で `torch_gain`（モーターのみに掛かる）、L2 を押しながらで `manual_gain` を調整する。
- `pwm_pca9685_controller`（[src/controller/pwm_controller/](src/controller/pwm_controller/)）: `/mux_pwm` を受けて、速度リミットでクランプしてからハードウェアに出す。十字キー上下で速度リミットを変える。L1+R1 は非常停止。クランプ後の値を `/pwm_input` に出す。
- PWM の中立・範囲・ボタン番号は `pwm_mux_node` と `pwm_pca9685_controller` の両方で `declare_parameter` されている。キャリブレーション値を変えるときは両方そろえること。
- `bag_recorder`（ジョイスティックで記録開始・停止）と `m5stack_visualizer`（シリアル経由で M5Stack に状態表示、ファームは `platformio/`）は脇から購読するだけ。M5Stack のブリッジは 1 行 1 JSON で送り、ファームはキー（`"state"` / `"event_type"` / `"model"` / `"mode"`）で振り分ける。ファームは Core2 用（`platformio/core2/`）と Basic 用（`platformio/src/`）の 2 つあり、変えるときは両方そろえる。
- `bno085_imu` は PCA9685 とは別の I2C バスにつなぐ（クロックストレッチでバスが固まり、PWM 出力が止まるのを避けるため）。`rate_hz` は 50 が上限の目安。経緯は [WIP.md](WIP.md) を参照。

### 学習と推論の対応

- 学習データ: rosbag の `/scan`（入力）と `/pwm_input`（教師）を時刻で同期させる（[training_src/dataset.py](training_src/dataset.py)）。教師は「ジョイスティックの値」ではなく「実際にハードウェアに出した PWM」になる。
- `pwm_mux_node` は `/mux_pwm` を出すたびに出どころ（`manual` / `auto` / `auto_l2`）を `/mux_pwm/source` に出し、bag に記録される。`dataset.py` はこれを `/pwm_input` の各行に付け、`DataPreprocessConfig(source_weights=...)` でラベルごとの重みを決める（0 は除外、ラベルの無い bag は全部 1.0）。`train.py` は `{"manual": 1.0, "auto": 0.0, "auto_l2": 3.0}` を使い、重みが混ざると `WeightedRandomSampler` を使う。
- `train.py` は引数なしなら `jetson_sync.fetch_latest_bags()` を使い、SSH（`MAGP_JETSON_HOST`、既定 `jetson`）で Jetson の `~/rosbag` から最新の bag を `dataset/train/` に取ってくる。接続できなければローカルの最新 bag を使う。
- `train.py --session --direction c|cc` は `dataset/train/session.txt`（git 管理外）に並んだ bag のうち、向きが一致するものだけで学習する。session.txt の各行は `rosbag2_<日時> c` のように bag 名と向き（c: 時計回り / cc: 反時計回り）を書く。Jetson にあって一覧に無い bag は、指定した向きとして自動で書き足す（そのため、向きを変える前に `make dagger` を実行しておく）。向きの無い行と、行頭が `#` の bag は使わない。`--bags <名前…>` は bag を直接指定する（Jetson に接続しない）。
- モデルの枠: Jetson の `/home/jetson/models/` に `origin_clockwise` / `origin_counter_clockwise` / `mirror_clockwise` / `mirror_counter_clockwise` の 4 つの枠（`<枠>.pth`、実体へのシンボリックリンク）を置く。`--session` で学習したモデルは `<枠>_<日時>.pth` に保存する。`--deploy` はそれを Jetson に送り、`<枠>.pth` と `current.pth`（→ `<枠>.pth`）を付け替える。origin の枠は前の行き先を `<枠>.prev.pth` に残し、`make rollback c|cc` で入れ替える。`make new-session` は `current.pth` を実体に張り替えてから枠のリンクを消す（ファイルは消さない）。枠の名前と向きの対応は `jetson_sync.py` の `SLOTS` / `slot_name()`。
- `train.py --mirror` は、スキャンとステアリングを左右反転して学習する。`--session` なら反対の向きの mirror の枠（c の bag → `mirror_counter_clockwise`）、そうでなければ `my_model_mirror_<日時>.pth` に保存する（左右対称なもう一方のコース用）。自車の写り込みの角度は反転しない。ステアリングは `mirror_steering_center`（1273、ジャイロから求めた直進の PWM）で折り返す。根拠と評価は [WIP.md](WIP.md) を参照。
- 保存される `.pth` は dict で、`model_state_dict` に加えて正規化統計（`scan_mean` / `scan_std` / `target_mean` / `target_std`）を含む。推論側はこれを使って正規化と逆正規化をする。
- **モデル定義は 2 か所にある**: [training_src/model.py](training_src/model.py) と [src/pytorch_pwm_controller/pytorch_pwm_controller/model/model.py](src/pytorch_pwm_controller/pytorch_pwm_controller/model/model.py)。中身は同じ（後者には末尾にコメントアウトされたテストがあるだけ）。片方を変えたら、もう片方も必ず合わせる。
- `scan_history_length` / `scan_history_stride` は、学習時の値と [launch/autonomous_driving.py](launch/autonomous_driving.py) の推論ノードのパラメータで一致させる。推論ノードは `(length-1)*stride+1` フレームをバッファし、stride ごとに取り出してモデルに入れる。バッファは手動中も更新され続けるが、`/mux_pwm/status` で Manual → Auto に変わると、次のスキャン 1 枚で埋め直す（`reset_history_on_auto`）。
- 推論で使うモデルは launch の `model_path`（現状 `/home/jetson/models/current.pth`）で指定する。そのファイルが無ければ `fallback_model_path`（`my_model.pth`）、`model_path` が空ならパッケージ同梱の `model_v9.pth` を使う。推論ノードはモデルファイルの差し替え（リンクの付け替え・更新時刻）を 1 秒ごとに見て、Manual 中に読み込み直す（Auto 中は Manual に戻るまで待つ。`reload_model` で無効にできる）。起動時と `current.pth` が付け替わったとき（deploy・rollback）は、`current.pth` が指す枠を使う。Manual 中に R1 を離すと、リンクがある次の枠（`slot_names` の順）に切り替える（押している間に L1 も押したら、非常停止なので切り替えない）。読み込んでいるモデルのファイル名（`.pth` を除く）を `/nn_pwm_controller/model`（`std_msgs/String`、transient_local、1 秒ごと）に出し、bag にも記録する。過去モデルの設定と評価は [model.md](src/pytorch_pwm_controller/pytorch_pwm_controller/model/model.md) に記録している。

### 現行モデルの保護と IMU 実験（進行中）

- 既存の `.pth`（`model/`、パッケージ内の `model/`、`/home/jetson/models/`）は削除・上書きしない。
- `train.py` は実行するたびに `model/checkpoints/best_model.pth`・`epoch_*.pth` を上書きする。学習したモデルは `model/transformer/my_model_<日時>.pth` に保存する。
- IMU を取り入れた模倣学習を、現行の LiDAR のみの系統とは別に開発している。関連する作業では `imu-imitation-learning` スキル（[.claude/skills/imu-imitation-learning/SKILL.md](.claude/skills/imu-imitation-learning/SKILL.md)）に従う。
- 2026-10-18 時点の既存の bag には `/imu/data` が入っていない。`/pwm_input` しかない bag もあるので、学習に使う前に `metadata.yaml` でトピックを確認する。

### その他

- `launch/autonomous_driving.py` は各パッケージの launch を `ExecuteProcess` で呼び出し、主要ノードは `Node` で直接起動する。チューニング用のパラメータ（`speed_sensitivity` など）はここで上書きする。
- マッピング: `launch/cartographer.launch.py` と `config/magp_2d.lua`。保存したマップは `data/map/` に置く。
- 実機で確認が必要な作業は、[WIP.md](WIP.md) に日付付きで「変更内容 / 確認状況 / TODO」の形で残す運用になっている。
