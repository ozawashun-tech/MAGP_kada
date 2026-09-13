🇯🇵 日本語 | [🇺🇸 English](README.md)

> 🚧 **作成中** — このプロジェクトは現在開発中です。ドキュメントおよびハードウェアビルドガイドは未完成です。

# MAGP — E2E RC-CAR Project for Minicar Autonomous Grand Prix

> 2D LiDARとPyTorch Transformerを用いたE2E模倣学習RCカープラットフォーム

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![ROS2](https://img.shields.io/badge/ROS2-Humble-blue)](https://docs.ros.org/en/humble/)

## 概要

このプロジェクトは、人間のデモンストレーションを模倣して自律走行を学習するオープンソースのミニカープラットフォームです。2D LiDARで環境を計測し、記録された走行データで学習したPyTorch Transformerモデルがスロットルとステアリング角度をリアルタイムに出力します。

主な特徴：
- **End-to-End模倣学習** — 生のLiDARスキャン → スロットル + ステアリング角
- **Transformerベースモデル** — スキャン履歴のシーケンスを入力、マルチステップ予測
- **シームレスな手動/自動切替** — PS3ジョイスティックによるPWMマルチプレクサ
- **簡単なデータ収集** — ジョイスティック操作のrosbagレコーダー
- **シェアードコントロール** - シェアードコントロールにより自動運転に微修正をかけたデータ収集が可能
- **M5Stackによる内部状態の可視化** — ハードウェア上でのリアルタイムステータス表示

## システムアーキテクチャ

```
[北陽電機 LiDAR]
      │ /scan
      ▼
[nn_pwm_controller]  ←── PyTorch Transformer（E2E模倣学習）
      │ /torch_pwm
      ▼
[mux_pwm] ←─────────────── [PS3ジョイスティック]
      │ /mux_pwm               （手動オーバーライド / モード切替）
      ▼
[pwm_pca9685_controller]
      │ I2C
      ▼
 モーター / サーボ

サイドチャネル：
  [mux_pwm] ──► [bag_recorder]       （データ収集）
  [mux_pwm] ──► [m5stack_visualizer] （シリアル経由のステータス表示）
```

## ハードウェア要件

> 配線・組み立て手順は [docs/hardware.md](docs/hardware.md) を参照してください（準備中）。
> 3Dプリント用パーツ（STL / STEP / Fusion 360 ソース）は [hardware/](hardware/) にあります。

| 部品 | 型番 |
|------|------|
| RCカーシャーシ | [Tamiya TT-02](https://www.tamiya.com/cms/english/rc/rcmanual/tt02.pdf) |
| 2D LiDAR | 北陽電機 UST-10LX（Ethernet接続、ケーブル付属） |
| シングルボードコンピュータ | NVIDIA Jetson Orin NX |
| SBCケース | 対応ケース（例：[Amazon](https://amzn.asia/d/0bSVL3AZ)、他でも可） |
| PWMドライバー | PCA9685ブレークアウトボード（I2C） |
| リポバッテリー | [7.4V 2S](https://amzn.asia/d/3W8HEnf) |
| DC-DC昇圧コンバータ | [7.4V → 12V](https://amzn.asia/d/dPUERnJ) |
| タミヤプラグ | [コネクタ](https://amzn.asia/d/3aU40sA) |
| DCプラグ付きピグテールケーブル | Jetsonキャリアボードのジャック径を要確認 |
| ジョイスティック | PS3 / PS4 |
| ディスプレイ | M5Stack Core2（オプション） |

Jetson Orin NX + UST-10LXという組み合わせにより、1/10スケールのミニカーサイズながら本格的な機械学習実験が可能な研究用スペックを実現しています。

## ソフトウェア要件

- ROS2 Humble
- Python 3.10+
- PyTorch 2.x
- `adafruit-circuitpython-pca9685`
- `pyserial`（M5Stackビジュアライザー使用時）

## クイックスタート

### 1. ビルド

```bash
cd MAGP_kada
make build
# または: colcon build --symlink-install
```

### 2. 学習データの収集

PS3ジョイスティックを接続してシステムを起動し、手動走行しながら記録します：

```bash
make run
```

bagレコーダーのジョイスティック操作：

| ボタン | 操作 |
|--------|------|
| △ × 3 | レコーダーのロック解除 |
| ■ | 記録開始 |
| ● | 停止 & 保存 |
| ✕ | 記録を破棄 |

### 3. モデルの学習

rosbagファイルを読み込むためのPython依存ライブラリをインストールします：

```bash
pip install rosbags
```

収集したrosbagデータを `dataset/train/` 以下に配置し（例：`dataset/train/YYYYMMDD/`）、学習スクリプトを実行します：

```bash
cd training_src
python train.py
```

`training_src/train.py` 末尾の主なパラメータ（必要に応じて編集）：

| パラメータ | デフォルト値 | 説明 |
|-----------|------------|------|
| `bag_paths` | `../dataset/train/20260401` | rosbagディレクトリのパス |
| `model_save_path` | `../model/transformer/model.pth` | モデルの保存先パス |
| `scan_history_length` | `20` | 入力シーケンスのスキャンフレーム数 |
| `scan_history_stride` | `10` | フレーム間のストライド |
| `epochs` | `128` | 最大エポック数 |
| `learning_rate` | `0.0001` | 学習率 |

検証lossが最小のベストチェックポイントは `model/checkpoints/best_model.pth` に保存されます。
`checkpoint_interval` エポックごとに定期チェックポイントも保存され、10エポック改善がない場合はアーリーストッピングが発動します。

保存された `.pth` ファイルのパスをlaunchファイルの `model_path` パラメータに設定してください。

### 4. 自律走行モードの実行

```bash
make run
```

ジョイスティックの **PSボタン** を押して **手動モード** と **自律走行モード** を切り替えます。
自律走行モード中に **L2** を押し続けると、手動入力とモデル出力をブレンドできます。

## パッケージ一覧

| パッケージ | ノード | 概要 |
|---------|------|-------------|
| [pytorch_pwm_controller](src/pytorch_pwm_controller/) | `nn_pwm_controller_node` | PyTorch Transformer推論、PWMコマンドのパブリッシュ |
| [mux_pwm](src/mux_pwm/) | `pwm_mux_node` | ジョイスティックブレンディング付き手動/自動PWMマルチプレクサ |
| [pwm_controller](src/controller/pwm_controller/) | `pwm_pca9685_controller` | PCA9685 I2C ハードウェアPWMドライバー |
| [bag_recorder](src/tools/bag_recorder/) | `bag_recorder_node` | ジョイスティック操作のrosbagレコーダー |
| [m5stack_visualizer](src/tools/m5stack_visualizer/) | `m5stack_bridge_node` | M5Stack Core2ディスプレイへのシリアルブリッジ |
| [urg_node2](src/urg_node2/) | `urg_node2` | 北陽電機 2D LiDARドライバー（Hokuyo / eSOL製） |

## ライセンス

このプロジェクト（`urg_node2` を除く）は [MITライセンス](LICENSE) の下で公開されています。
Copyright (c) 2025 K.Aiki, K.Teruyuki

`urg_node2` は Apache License 2.0 の下でライセンスされています。[src/urg_node2/LICENSE](src/urg_node2/LICENSE) を参照してください。

---

ドキュメント作成においては [Claude](https://claude.ai)（Anthropic）を使用しております。
