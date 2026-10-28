import numpy as np
import pandas as pd
import torch
from pathlib import Path
from rosbags.rosbag2 import Reader
from rosbags.typesys import Stores, get_typestore

from dataset import RosbagDataset, load_rosbag_data, preprocess_data

IMU_TOPIC = "/imu/data"
REQUIRED_TOPICS = ("/scan", "/pwm_input", IMU_TOPIC)

# sensor_msgs/Imu の中で入力に使うフィールド（今はヨーレートのみ）
IMU_FEATURES = ["angular_velocity.z"]

# 欠損として報告する IMU の途切れ（推論ノードの stale 判定と同じ 1 秒）
IMU_GAP_THRESHOLD_NS = 1_000_000_000
# 直前の IMU サンプルがこれより古い scan は imu_valid=False にする（50Hz で 5 サンプル分）
IMU_MAX_AGE_NS = 100_000_000


def imu_column(feature):
    return f"imu.{feature}"


def _bag_dir(bag_path):
    """Reader は metadata.yaml のある bag ディレクトリを要求する"""
    bag_path = Path(bag_path)
    return bag_path.parent if bag_path.is_file() else bag_path


def check_topics(bag_path):
    """
    bag に REQUIRED_TOPICS がすべて入っているかを調べる

    Returns:
        list of str: 足りない（またはメッセージが 0 件の）トピック
    """
    with Reader(_bag_dir(bag_path)) as reader:
        counts = {}
        for connection in reader.connections:
            counts[connection.topic] = counts.get(connection.topic, 0) + connection.msgcount
    return [topic for topic in REQUIRED_TOPICS if counts.get(topic, 0) == 0]


def select_imu_bags(bag_paths):
    """REQUIRED_TOPICS がそろった bag だけを返す（足りない bag は理由を表示して除外）"""
    selected = []
    for bag_path in bag_paths:
        missing = check_topics(bag_path)
        if missing:
            print(f"Skip: {_bag_dir(bag_path).name}（{', '.join(missing)} がありません）")
        else:
            selected.append(bag_path)
    return selected


def load_imu(bag_path, features=IMU_FEATURES):
    """bag から /imu/data だけを読み込む（timestamp は bag の受信時刻で、/scan と同じ基準）"""
    typestore = get_typestore(Stores.LATEST)
    imu_data = []

    with Reader(_bag_dir(bag_path)) as reader:
        connections = [c for c in reader.connections if c.topic == IMU_TOPIC]
        for connection, timestamp, rawdata in reader.messages(connections=connections):
            msg = typestore.deserialize_cdr(rawdata, connection.msgtype)
            data = {"timestamp": timestamp}
            for feature in features:
                value = msg
                for attr in feature.split("."):
                    value = getattr(value, attr)
                data[imu_column(feature)] = value
            imu_data.append(data)

    return pd.DataFrame(imu_data, columns=["timestamp"] + [imu_column(f) for f in features])


def attach_imu_features(scan_df, imu_df, features=IMU_FEATURES):
    """
    scan の各行に IMU の特徴量の列と imu_valid 列を付け足す（前処理の前に呼ぶ）

    値は「直前の scan から今の scan までに届いた IMU サンプルの平均」。
    その区間にサンプルがなければ、直前に届いた値を使う。
    直前の IMU サンプルが IMU_MAX_AGE_NS より古い行は imu_valid=False にする。
    推論ノードでも同じ計算をすること。
    """
    scan_df = scan_df.sort_values("timestamp").reset_index(drop=True)
    imu_df = imu_df.sort_values("timestamp").reset_index(drop=True)

    scan_t = scan_df["timestamp"].to_numpy(np.int64)
    imu_t = imu_df["timestamp"].to_numpy(np.int64)
    columns = [imu_column(f) for f in features]
    imu_values = imu_df[columns].to_numpy(np.float64)  # (M, F)

    # 各 scan の時刻までに届いた IMU サンプル数（区間 (前の scan, 今の scan] の終端）
    hi = np.searchsorted(imu_t, scan_t, side="right")
    # 先頭の scan は、scan の典型的な周期ぶん前からを区間とする
    scan_period = int(np.median(np.diff(scan_t))) if len(scan_t) > 1 else 0
    lo = np.empty_like(hi)
    lo[0] = np.searchsorted(imu_t, scan_t[0] - scan_period, side="right")
    lo[1:] = hi[:-1]

    cumsum = np.vstack([np.zeros((1, len(columns))), np.cumsum(imu_values, axis=0)])
    count = (hi - lo)[:, None]
    window_mean = (cumsum[hi] - cumsum[lo]) / np.maximum(count, 1)
    last_value = imu_values[np.maximum(hi - 1, 0)] if len(imu_t) else np.zeros((len(scan_t), len(columns)))
    features_per_scan = np.where(count > 0, window_mean, last_value)

    has_previous = hi > 0
    last_imu_t = imu_t[np.maximum(hi - 1, 0)] if len(imu_t) else np.zeros_like(scan_t)
    imu_valid = has_previous & (scan_t - last_imu_t <= IMU_MAX_AGE_NS)
    features_per_scan[~imu_valid] = 0.0

    scan_df = scan_df.copy()
    for i, column in enumerate(columns):
        scan_df[column] = features_per_scan[:, i].astype(np.float32)
    scan_df["imu_valid"] = imu_valid

    # 欠損の報告（scan の時間範囲内で IMU が 1 秒以上途切れた区間）
    in_range = imu_t[(imu_t >= scan_t[0]) & (imu_t <= scan_t[-1])] if len(scan_t) else imu_t
    edges = np.concatenate([[scan_t[0]], in_range, [scan_t[-1]]]) if len(scan_t) else in_range
    gaps = np.diff(edges)
    gaps = gaps[gaps >= IMU_GAP_THRESHOLD_NS]
    duration = (scan_t[-1] - scan_t[0]) / 1e9 if len(scan_t) > 1 else 0.0
    rate = len(in_range) / duration if duration > 0 else 0.0
    print(
        f"  IMU: {len(imu_t)} samples ({rate:.1f} Hz), "
        f"gaps >= {IMU_GAP_THRESHOLD_NS / 1e9:.0f}s: {len(gaps)} ({gaps.sum() / 1e9:.1f}s), "
        f"invalid scans: {(~imu_valid).sum()}/{len(imu_valid)}"
    )

    return scan_df


def load_bag_with_imu(bag_path, config=None, features=IMU_FEATURES):
    """1 本の bag を読み、IMU 特徴量を付け足してから前処理する"""
    scan_df, joy_df = load_rosbag_data(bag_path)
    imu_df = load_imu(bag_path, features)
    scan_df = attach_imu_features(scan_df, imu_df, features)
    # 同期・トリミング・停止区間の除去は、IMU の列ごと scan と同じ行が残る
    return preprocess_data(scan_df, joy_df, config)


def load_bags_with_imu(bag_paths, config=None, features=IMU_FEATURES):
    """
    複数の bag を読み込んで結合する（dataset.load_rosbags の IMU 版）

    Returns:
        scan_df, joy_df, file_boundaries
    """
    if isinstance(bag_paths, (str, Path)):
        bag_paths = [bag_paths]
    if not bag_paths:
        raise ValueError("No bags given")

    all_scan_dfs = []
    all_joy_dfs = []
    file_boundaries = [0]
    for bag_path in bag_paths:
        missing = check_topics(bag_path)
        if missing:
            raise ValueError(f"{_bag_dir(bag_path)} に {', '.join(missing)} がありません")
        print(f"Loading: {bag_path}")
        scan_df, joy_df = load_bag_with_imu(bag_path, config, features)
        all_scan_dfs.append(scan_df)
        all_joy_dfs.append(joy_df)
        file_boundaries.append(file_boundaries[-1] + len(scan_df))
        print(f"  Loaded {len(scan_df)} samples")

    combined_scan_df = pd.concat(all_scan_dfs, ignore_index=True)
    combined_joy_df = pd.concat(all_joy_dfs, ignore_index=True)
    print(f"\nTotal samples: {len(combined_scan_df)}")
    print(f"File boundaries: {file_boundaries}")

    return combined_scan_df, combined_joy_df, file_boundaries


class RosbagIMUDataset(RosbagDataset):
    """RosbagDataset と同じ履歴インデックスで、IMU の履歴も返すデータセット"""

    def __init__(
        self,
        scan_df,
        joy_df,
        file_boundaries=None,
        scan_history_length=4,
        scan_history_stride=1,
        prediction_steps=1,
        noise_std=[0.0, 0.05, 0.1, 0.15],
        imu_noise_std=0.0,
        features=IMU_FEATURES,
    ):
        """
        Args:
            imu_noise_std: IMU に足すガウスノイズの標準偏差（生の値の単位。0 なら足さない）
            features: 使う IMU の特徴量（attach_imu_features と同じもの）
            その他: RosbagDataset と同じ
        """
        super().__init__(
            scan_df,
            joy_df,
            file_boundaries,
            scan_history_length=scan_history_length,
            scan_history_stride=scan_history_stride,
            prediction_steps=prediction_steps,
            noise_std=noise_std,
        )
        self.imu_noise_std = imu_noise_std
        self.all_imu = scan_df[[imu_column(f) for f in features]].to_numpy(np.float32)  # (N, F)

        # 履歴に IMU の欠損を含むサンプルは除外する
        if len(self.history_indices) > 0:
            imu_valid = scan_df["imu_valid"].to_numpy(bool)
            keep = imu_valid[self.history_indices].all(axis=1)
            if not keep.all():
                print(f"  Removed {(~keep).sum()} samples with IMU gaps in history")
            self.history_indices = self.history_indices[keep]
            self.targets = self.targets[keep]
            self.sample_weights = self.sample_weights[keep]
        print(f"  IMU samples: {len(self.history_indices)}")

    def __getitem__(self, idx):
        scan_history, targets = super().__getitem__(idx)
        imu_history = self.all_imu[self.history_indices[idx]]
        if self.imu_noise_std > 0:
            imu_history = imu_history + np.random.normal(0, self.imu_noise_std, imu_history.shape)
        return scan_history, imu_history, targets


def imu_collate_fn(batch):
    """(scan_history, imu_history, targets) のリストをテンソルのバッチにまとめる"""
    scans = torch.from_numpy(np.stack([b[0] for b in batch]).astype(np.float32))
    imus = torch.from_numpy(np.stack([b[1] for b in batch]).astype(np.float32))
    targets = torch.from_numpy(np.stack([b[2] for b in batch]).astype(np.float32))
    return scans, imus, targets
