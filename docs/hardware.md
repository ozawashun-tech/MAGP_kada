[🇯🇵 日本語](#日本語) | [🇺🇸 English](#english)

---

## English

# Hardware Build Guide

> 🚧 **Work in progress** — Assembly instructions, wiring diagrams, and STL files are being prepared. Content marked "Coming soon" will be added by the authors.

## Concept

MAGP uses a high-spec configuration relative to its mini car form factor — **NVIDIA Jetson Orin NX** for onboard AI compute and **Hokuyo UST-10LX** for 2D LiDAR sensing. This makes the platform capable of genuine research workloads (real-time model inference, SLAM, data collection) on a 1/10-scale RC car chassis.

![MAGP completed build](images/magp_overview.jpg)

## Bill of Materials

| # | Component | Model / Link | Notes |
|---|-----------|-------------|-------|
| 1 | RC car chassis | CARTEN M210 | 1/10-scale touring car chassis |
| 2 | 2D LiDAR | Hokuyo UST-10LX | Ethernet, 270°, 40 m range. Ethernet cable included. |
| 3 | SBC | NVIDIA Jetson Orin NX 16GB | 157 TOPS, 8-core Cortex-A78AE, 16GB LPDDR5, GbE × 1 |
| 4 | SBC case | [Amazon](https://amzn.asia/d/0bSVL3AZ) | Any case that fits the chassis |
| 5 | PWM driver | PCA9685 breakout board | I2C, 16-ch, 50 Hz |
| 6 | LiPo battery | [7.4V 2S LiPo](https://amzn.asia/d/3W8HEnf) | Main power source |
| 6a | LiPo balance charger | Any 2S–3S LiPo balance charger | For charging the 7.4V 2S battery |
| 7 | DC-DC boost converter | [7.4V → 12V](https://amzn.asia/d/dPUERnJ) | Powers LiDAR and Jetson |
| 8 | Tamiya plug | [Tamiya connector](https://amzn.asia/d/3aU40sA) | Battery connector |
| 9 | DC barrel jack pigtail cable | DC 5.5mm × 2.5mm (verify your carrier board) | One end: DC plug for Jetson power jack. Other end: bare wire to DC-DC converter output. Accepts 9–20V. |
| 10 | Joystick | PS3 / PS4 | USB or Bluetooth |
| 11 | Display | M5Stack Core2 | Optional, USB serial to Jetson |
| 12 | IMU | BNO085 breakout board | I2C, on a separate bus from the PCA9685 |

> **Mounting:** All parts except the PCA9685 are mounted using 3D-printed brackets.
> CAD data (STL / STEP / Fusion 360 source) are available in [hardware/base_plate/](../hardware/base_plate/).

---

## Step 0 — Prepare Parts

Gather all components listed in the BOM above. Confirm that:
- The LiPo battery is charged.
- The CARTEN M210 chassis parts are on hand.
- The PCA9685 is soldered (header pins and terminal block).

---

## Step 1 — Assemble the RC Car

Assemble the CARTEN M210 chassis following the manufacturer's manual.

Complete the standard assembly up through mounting the ESC and servo. Do **not** mount a receiver — the PCA9685 will take its place.

---

## Step 2 — Power System Wiring

The power topology is as follows:

```
[7.4V LiPo] (Tamiya plug)
      │
      ├──────────────────────────► ESC (RC car main power)
      │
      └──► [DC-DC Boost Converter]  7.4V → 12V
                  │
                  ├──────────────► Hokuyo UST-10LX  (12V)
                  │
                  └──────────────► Jetson Orin NX   (12V, via DC barrel jack pigtail)
```

1. Connect the LiPo battery (Tamiya plug) to a splitter cable.
2. Route one branch to the ESC's battery input (Tamiya connector).
3. Route the other branch to the DC-DC boost converter input. Set the output to **12V**.
4. From the boost converter output, split again:
   - One branch to the LiDAR power input.
   - One branch to the Jetson via the DC barrel jack pigtail cable.

> Verify polarity at every connection before powering on.

---

## Step 3 — Mount and Wire Jetson and LiDAR

1. Mount the Jetson Orin NX (in its case) onto the chassis using the 3D-printed bracket.
2. Mount the UST-10LX on the front of the chassis using the 3D-printed bracket.
3. Connect the LiDAR and Jetson with the **Ethernet cable** (included with UST-10LX).
   - Configure the Jetson's Ethernet interface with a static IP on the same subnet as the UST-10LX (default: `192.168.0.10`).

---

## Step 4 — Install and Wire the PCA9685

Mount the PCA9685 board on the chassis.

**I2C connection (Jetson → PCA9685):**

| PCA9685 pin | Jetson GPIO header pin |
|-------------|----------------------|
| VCC | 3.3V |
| GND | GND |
| SDA | SDA (I2C) |
| SCL | SCL (I2C) |

![Jetson I2C wiring](images/IMG_7478.JPG)

**PWM output (PCA9685 → RC car):**

| PCA9685 channel | Connected to |
|-----------------|-------------|
| CH0 | ESC signal input (throttle) |
| CH1 | Steering servo signal input |

The ESC provides a 5V BEC output through the servo signal connector, which powers the servo rail (V+) of the PCA9685.

![PCA9685 overview](images/IMG_7468.JPG)
![PCA9685 mounted in chassis](images/IMG_7479.JPG)

---

## Step 5 — Install and Wire the BNO085 IMU

Use a **separate I2C bus** from the PCA9685. If the BNO085 (which uses clock stretching) hangs its bus, throttle/steering output keeps working.

**I2C connection (Jetson → BNO085):**

| BNO085 pin | Jetson 40-pin header |
|------------|----------------------|
| VIN | Pin 1 (3.3V) |
| GND | Pin 6 or 9 |
| SDA | Pin 27 (I2C0 SDA, usually `/dev/i2c-1`) |
| SCL | Pin 28 (I2C0 SCL) |
| P0 / P1 | Leave open (I2C mode) |
| DI | Open → 0x4A (default), High → 0x4B |

The bus number depends on the carrier board. Check with `i2cdetect -l` and `i2cdetect -y -r 1` (0x4A should appear), then set `i2c_bus` in `src/sensors/bno085_imu/config/bno085_imu_params.yaml`.

**Mounting:**
- Near the chassis center of rotation, as low as possible, away from the motor and ESC.
- Fix with foam double-sided tape to reduce vibration.
- Align axes with `base_link` (REP-103: x forward, y left, z up). Set the offset/rotation via the `imu_*` launch arguments of `bno085_imu.launch.py`.

---

## Full Wiring Overview

```
[7.4V LiPo]
    ├── ESC ──► Motor
    └── DC-DC Boost (12V)
          ├── UST-10LX ──(Ethernet)──► Jetson Orin NX
          └── Jetson Orin NX (DC barrel jack)
                ├── (I2C, pin 3/5) ──► PCA9685
                │                ├── CH0 ──► ESC signal (throttle)
                │                └── CH1 ──► Servo signal (steering)
                ├── (I2C, pin 27/28) ──► BNO085 IMU
                ├── (USB/BT) ──► PS3/PS4 Joystick
                └── (USB serial) ──► M5Stack Core2 (optional)
```

---
---

## 日本語

# ハードウェアビルドガイド

> 🚧 **作成中** — 組み立て手順・配線図・STLファイルは準備中です。

## コンセプト

MAGPは、1/10スケールのミニカーサイズながら研究用途として十分なスペックを持つことを特徴としています。**NVIDIA Jetson Orin NX** による高い計算能力と **北陽電機 UST-10LX** の高精度LiDARにより、リアルタイム推論・SLAM・データ収集を実機上でこなせる構成です。

![MAGP 完成図](images/magp_overview.jpg)

## 部品リスト

| # | 部品 | 型番 / リンク | 備考 |
|---|------|-------------|------|
| 1 | RCカーシャーシ | CARTEN M210 | 1/10スケールのツーリングカーシャーシ |
| 2 | 2D LiDAR | 北陽電機 UST-10LX | Ethernet接続、270°、測定距離40 m。Ethernetケーブル付属。 |
| 3 | SBC | NVIDIA Jetson Orin NX 16GB | 157 TOPS、8コア Cortex-A78AE、16GB LPDDR5、GbE × 1 |
| 4 | SBCケース | [Amazon](https://amzn.asia/d/0bSVL3AZ) | シャーシに収まるものであれば何でもよい |
| 5 | PWMドライバー | PCA9685ブレークアウトボード | I2C、16ch、50 Hz |
| 6 | リポバッテリー | [7.4V 2S LiPo](https://amzn.asia/d/3W8HEnf) | メイン電源 |
| 6a | リポバッテリー充電器 | 2S〜3S対応リポバランス充電器 | 7.4V 2S バッテリーの充電用 |
| 7 | DC-DC昇圧コンバータ | [7.4V → 12V](https://amzn.asia/d/dPUERnJ) | LiDARとJetsonに給電 |
| 8 | タミヤプラグ | [タミヤコネクタ](https://amzn.asia/d/3aU40sA) | バッテリーコネクタ |
| 9 | DCプラグ付きピグテールケーブル | DC 5.5mm × 2.5mm（キャリアボードで要確認） | 片側：JetsonのDC電源ジャック用プラグ（9〜20V 入力）。もう片側：DC-DCコンバータ出力端子に接続する裸線。 |
| 10 | ジョイスティック | PS3 / PS4 | USBまたはBluetooth |
| 11 | ディスプレイ | M5Stack Core2 | オプション、JetsonにUSBシリアル接続 |
| 12 | IMU | BNO085ブレークアウトボード | I2C、PCA9685とは別バスに接続 |

> **固定方法：** PCA9685以外の各部品は3Dプリンターで印刷したブラケットで固定します。
> CADデータ（STL / STEP / Fusion 360 ソース）は [hardware/base_plate/](../hardware/base_plate/) にあります。

---

## ステップ 0 — 部品の準備

上記の部品リストを揃えます。以下を確認してください：
- リポバッテリーが充電されていること
- CARTEN M210のシャーシパーツが揃っていること
- PCA9685のハンダ付けが完了していること（ピンヘッダとターミナルブロック）

---

## ステップ 1 — RCカーの組み立て

CARTEN M210をメーカーのマニュアルに従って組み立てます。

ESCとサーボを取り付けるところまで標準組み立てを完了させます。**受信機は取り付けない**でください（代わりにPCA9685を使用します）。

---

## ステップ 2 — 電源系統の固定と配線

電源系統のトポロジーは以下の通りです：

```
[7.4Vリポバッテリー]（タミヤプラグ）
      │
      ├──────────────────────────► ESC（RCカー本体電源）
      │
      └──► [DC-DC昇圧コンバータ]  7.4V → 12V
                  │
                  ├──────────────► 北陽電機 UST-10LX  (12V)
                  │
                  └──────────────► Jetson Orin NX     (12V、DCプラグ経由)
```

1. リポバッテリー（タミヤプラグ）を分岐ケーブルに接続します。
2. 一方をESCのバッテリー入力（タミヤコネクタ）に接続します。
3. もう一方をDC-DC昇圧コンバータの入力に接続し、出力を **12V** に設定します。
4. 昇圧コンバータの出力をさらに分岐します：
   - 一方をLiDARの電源入力に接続します。
   - もう一方をDCプラグ付きピグテールケーブルを介してJetsonの電源ジャックに接続します。

> 各接続の極性を必ず確認してから通電してください。

---

## ステップ 3 — JetsonとLiDARの固定と配線

1. Jetson Orin NX（ケース入り）を3Dプリント製ブラケットでシャーシに固定します。
2. UST-10LXを3Dプリント製ブラケットでシャーシ前部に固定します。
3. LiDARとJetsonをUST-10LXに**付属のEthernetケーブル**で直結します。
   - JetsonのEthernetインターフェイスをUST-10LXと同一サブネットの静的IPに設定してください（UST-10LXデフォルト: `192.168.0.10`）。

---

## ステップ 4 — モータードライバーの取り付けと配線

PCA9685ボードをシャーシに取り付けます。

**I2C接続（Jetson → PCA9685）：**

| PCA9685ピン | Jetson GPIOヘッダピン |
|-------------|----------------------|
| VCC | 3.3V |
| GND | GND |
| SDA | SDA (I2C) |
| SCL | SCL (I2C) |

![JetsonのI2C配線](images/IMG_7478.JPG)

**PWM出力（PCA9685 → RCカー）：**

| PCA9685チャンネル | 接続先 |
|------------------|--------|
| CH0 | ESC信号入力（スロットル） |
| CH1 | ステアリングサーボ信号入力 |

ESCのBEC出力（サーボコネクタ経由の5V）がPCA9685のサーボレール（V+）に給電します。

![PCA9685全体](images/IMG_7468.JPG)
![PCA9685シャーシへの取り付け](images/IMG_7479.JPG)

---

## ステップ 5 — IMU（BNO085）の取り付けと配線

PCA9685 とは**別の I2C バス**に接続します。BNO085（クロックストレッチを使う）がバスを固めても、スロットル・ステアリング出力が止まらないようにするためです。

**I2C接続（Jetson → BNO085）：**

| BNO085ピン | Jetson 40ピンヘッダ |
|------------|--------------------|
| VIN | 1番ピン（3.3V） |
| GND | 6番または9番ピン |
| SDA | 27番ピン（I2C0 SDA、通常 `/dev/i2c-1`） |
| SCL | 28番ピン（I2C0 SCL） |
| P0 / P1 | オープン（I2Cモード） |
| DI | オープン → 0x4A（デフォルト）、High → 0x4B |

バス番号はキャリアボードで変わります。`i2cdetect -l` と `i2cdetect -y -r 1` で 0x4A が見えることを確認し、`src/sensors/bno085_imu/config/bno085_imu_params.yaml` の `i2c_bus` を合わせてください。

**取り付け：**
- シャーシの回転中心付近、できるだけ低い位置で、モーター・ESCから離す。
- 振動対策に両面スポンジテープで固定する。
- 軸を `base_link`（REP-103：x 前、y 左、z 上）にそろえる。取り付け位置・回転は `bno085_imu.launch.py` の `imu_*` 引数で設定する。

---

## 配線全体図

```
[7.4Vリポバッテリー]
    ├── ESC ──► モーター
    └── DC-DC昇圧（12V）
          ├── UST-10LX ──(Ethernet)──► Jetson Orin NX
          └── Jetson Orin NX（DCプラグ）
                ├── (I2C、3/5番ピン) ──► PCA9685
                │                ├── CH0 ──► ESC信号（スロットル）
                │                └── CH1 ──► サーボ信号（ステアリング）
                ├── (I2C、27/28番ピン) ──► BNO085 IMU
                ├── (USB/BT) ──► PS3/PS4ジョイスティック
                └── (USBシリアル) ──► M5Stack Core2（オプション）
```
