import numpy as np
import pandas as pd
import torch
from pathlib import Path
from rosbags.rosbag2 import Reader
from rosbags.typesys import Stores, get_typestore


class DataPreprocessConfig:
    """データ前処理の設定クラス"""

    def __init__(
        self,
        trim_front=0.0,
        trim_back=0.0,
        stop_threshold=None,
        stop_samples=None,
        stop_margin_before=0,
        stop_margin_after=0,
    ):
        """
        Args:
            trim_front: 前半から削除する割合 (0.0-1.0)
            trim_back: 後半から削除する割合 (0.0-1.0)
            stop_threshold: 連続停止判定のスロットル閾値
                - float: この値以下（絶対値）を停止とみなす
                - tuple: (min_threshold, max_threshold) の範囲内を停止とみなす
            stop_samples: 連続停止サンプル数の閾値
                両方指定された場合のみ連続停止区間を削除
            stop_margin_before: 停止区間の前に削除するサンプル数
            stop_margin_after: 停止区間の後に削除するサンプル数
        """
        if trim_front < 0 or trim_back < 0 or trim_front + trim_back >= 1.0:
            raise ValueError("trim_front and trim_back must be in [0, 1)")

        self.trim_front = trim_front
        self.trim_back = trim_back
        self.stop_threshold = stop_threshold
        self.stop_samples = stop_samples
        self.stop_margin_before = stop_margin_before
        self.stop_margin_after = stop_margin_after


def load_rosbag_data(bag_path):
    """単一のROSバッグファイルからデータを読み込む"""
    typestore = get_typestore(Stores.LATEST)
    joy_data = []
    scan_data = []

    with Reader(bag_path) as reader:
        for connection, timestamp, rawdata in reader.messages():
            msg = typestore.deserialize_cdr(rawdata, connection.msgtype)
                        
            if connection.topic == "/pwm_input":
                data = {}
                data["twist.linear.x"] = msg.twist.linear.x 
                data["twist.angular.z"] = msg.twist.angular.z
                data["timestamp"] = timestamp
                joy_data.append(data)
            elif connection.topic == "/scan":
                data = vars(msg)
                data["timestamp"] = timestamp
                scan_data.append(data)

    return pd.DataFrame(scan_data), pd.DataFrame(joy_data)


def synchronize_data(scan_df, joy_df):
    """scanとjoyデータをタイムスタンプで同期"""
    synchronized_joy_data = []
    
    for scan_timestamp in scan_df["timestamp"]:
        time_diffs = np.abs(joy_df["timestamp"] - scan_timestamp)
        closest_index = time_diffs.idxmin()
        synchronized_joy_data.append(joy_df.loc[closest_index])

    synchronized_joy_df = pd.DataFrame(synchronized_joy_data).reset_index(drop=True)
    scan_df = scan_df.reset_index(drop=True)

    return scan_df, synchronized_joy_df


def trim_data(scan_df, joy_df, trim_front=0.0, trim_back=0.0):
    """データの前半・後半をトリミング"""
    if trim_front == 0 and trim_back == 0:
        return scan_df, joy_df

    total_len = len(scan_df)
    start_idx = int(total_len * trim_front)
    end_idx = int(total_len * (1 - trim_back))

    scan_df = scan_df.iloc[start_idx:end_idx].reset_index(drop=True)
    joy_df = joy_df.iloc[start_idx:end_idx].reset_index(drop=True)

    print(
        f"  Trimmed: {total_len} -> {len(scan_df)} samples "
        f"(front: {trim_front:.1%}, back: {trim_back:.1%})"
    )

    return scan_df, joy_df


def filter_stops(
    scan_df, joy_df, stop_threshold, stop_samples, margin_before=0, margin_after=0
):
    """連続して停止している区間を検出して削除（前後のマージン付き、範囲指定対応）"""
    if stop_threshold is None or stop_samples is None:
        return scan_df, joy_df

    # スロットルを抽出（新しいデータ形式に対応）
    throttles = np.array(joy_df["twist.linear.x"])

    # 停止フラグの判定
    if isinstance(stop_threshold, (tuple, list)):
        # 範囲指定の場合：指定範囲内を停止とみなす
        min_threshold, max_threshold = stop_threshold
        is_stopped = (np.abs(throttles) >= min_threshold) & (np.abs(throttles) <= max_threshold)
        threshold_info = f"range [{min_threshold}, {max_threshold}]"
    else:
        # 単一値の場合：絶対値がthreshold以下を停止とみなす
        is_stopped = np.abs(throttles) <= stop_threshold
        threshold_info = f"<= {stop_threshold}"

    # 連続停止区間を検出
    mask = np.ones(len(scan_df), dtype=bool)  # 保持するデータのマスク

    i = 0
    removed_segments = 0
    total_removed = 0

    while i < len(is_stopped):
        if is_stopped[i]:
            # 停止区間の開始
            start_idx = i

            # 連続停止区間の終わりを探す
            while i < len(is_stopped) and is_stopped[i]:
                i += 1

            end_idx = i
            segment_length = end_idx - start_idx

            # 閾値以上のサンプル数の停止区間をマスク
            if segment_length >= stop_samples:
                # マージンを考慮した削除範囲を計算
                remove_start = max(0, start_idx - margin_before)
                remove_end = min(len(mask), end_idx + margin_after)

                mask[remove_start:remove_end] = False
                removed_segments += 1
                total_removed += remove_end - remove_start
        else:
            i += 1

    original_len = len(scan_df)
    scan_df = scan_df[mask].reset_index(drop=True)
    joy_df = joy_df[mask].reset_index(drop=True)

    margin_info = ""
    if margin_before > 0 or margin_after > 0:
        margin_info = f", margin: -{margin_before}/+{margin_after}"

    print(
        f"  Consecutive stop filtered (throttle {threshold_info}, samples >= {stop_samples}{margin_info}):"
    )
    print(f"    {original_len} -> {len(scan_df)} samples")
    print(f"    Removed {removed_segments} segments ({total_removed} samples)")

    return scan_df, joy_df


def preprocess_data(scan_df, joy_df, config=None):
    """
    データの前処理を実行

    Args:
        scan_df: scanデータのDataFrame
        joy_df: joyデータのDataFrame
        config: DataPreprocessConfig インスタンス

    Returns:
        前処理済みの scan_df, joy_df
    """
    if config is None:
        config = DataPreprocessConfig()

    # 同期
    scan_df, joy_df = synchronize_data(scan_df, joy_df)

    # トリミング
    scan_df, joy_df = trim_data(scan_df, joy_df, config.trim_front, config.trim_back)

    # 連続停止区間の削除
    scan_df, joy_df = filter_stops(
        scan_df,
        joy_df,
        config.stop_threshold,
        config.stop_samples,
        config.stop_margin_before,
        config.stop_margin_after,
    )

    return scan_df, joy_df


def load_rosbags(bag_paths, preprocess_configs=None):
    """
    指定されたROSバッグファイルまたはディレクトリからデータを読み込み、結合する

    Args:
        bag_paths: str, Path, or list of (str or Path)
            ROSバッグファイルのパス、ディレクトリパス、またはそれらのリスト
            - 単一のディレクトリパス: そのディレクトリ以下の全.db3ファイルを再帰的に読み込む
            - ディレクトリパスのリスト: 各ディレクトリ以下の全.db3ファイルを再帰的に読み込む
            - .db3ファイルパスのリスト: 指定されたファイルを読み込む
        preprocess_configs: DataPreprocessConfig, list of DataPreprocessConfig, or None
            前処理設定。Noneの場合はデフォルト設定を使用
            リストの場合、各bagファイルに対応する設定を指定

    Returns:
        scan_df: 結合されたscanデータ
        joy_df: 結合されたjoyデータ
        file_boundaries: 各ファイルの開始インデックスのリスト
    """
    
    # 入力を統一的に処理
    if isinstance(bag_paths, (str, Path)):
        bag_paths = [bag_paths]
    
    # 各パスから.db3ファイルを収集
    db3_files = []
    for path in bag_paths:
        path = Path(path)
        
        if path.is_file() and path.suffix == '.db3':
            # 直接.db3ファイルが指定された場合
            db3_files.append(path)
        elif path.is_dir():
            # ディレクトリの場合、その配下の.db3ファイルを再帰的に検索
            found_files = sorted(path.rglob("*.db3"))
            if found_files:
                db3_files.extend(found_files)
                print(f"Found {len(found_files)} .db3 files in {path}")
            else:
                print(f"Warning: No .db3 files found in {path}")
        else:
            print(f"Warning: Invalid path {path}")
    
    if not db3_files:
        raise ValueError(f"No .db3 files found")
    
    print(f"Total: {len(db3_files)} bag files")

    # 前処理設定の準備
    if preprocess_configs is None:
        preprocess_configs = [DataPreprocessConfig() for _ in db3_files]
    elif isinstance(preprocess_configs, DataPreprocessConfig):
        preprocess_configs = [preprocess_configs for _ in db3_files]
    elif len(preprocess_configs) != len(db3_files):
        raise ValueError("preprocess_configs length must match number of bag files")

    all_scan_dfs = []
    all_joy_dfs = []
    file_boundaries = [0]  # 各ファイルの開始インデックス
    cumulative_samples = 0

    for bag_path, config in zip(db3_files, preprocess_configs):
        print(f"Loading: {bag_path}")
        scan_df, joy_df = load_rosbag_data(bag_path)
        scan_df, joy_df = preprocess_data(scan_df, joy_df, config)

        all_scan_dfs.append(scan_df)
        all_joy_dfs.append(joy_df)
        cumulative_samples += len(scan_df)
        file_boundaries.append(cumulative_samples)
        
        print(f"  Loaded {len(scan_df)} samples")

    # 全てのデータフレームを結合
    combined_scan_df = pd.concat(all_scan_dfs, ignore_index=True)
    combined_joy_df = pd.concat(all_joy_dfs, ignore_index=True)

    print(f"\nTotal samples: {len(combined_scan_df)}")
    print(f"File boundaries: {file_boundaries}")

    return combined_scan_df, combined_joy_df, file_boundaries


class RosbagDataset(torch.utils.data.Dataset):
    def __init__(
        self,
        scan_df,
        joy_df,
        file_boundaries=None,
        scan_history_length=4,
        scan_history_stride=1,
        prediction_steps=1,
        noise_std=[0.0, 0.05, 0.1, 0.15],
    ):
        """..."""
        self.noise_std = noise_std
        
        print("Preprocessing dataset...")
        
        # スキャンデータを全てnumpy配列に変換
        all_scans = np.stack([scan_df.iloc[i]["ranges"] for i in range(len(scan_df))], axis=0)
        all_actions = np.array([
            [joy_df.iloc[i]["twist.linear.x"], joy_df.iloc[i]["twist.angular.z"]]
            for i in range(len(joy_df))
        ], dtype=np.float32)
        
        # 有効なサンプルを事前に作成
        self.scan_histories = []
        self.targets = []
        
        for idx in range(len(scan_df)):
            # このidxがどのファイルに属するか確認
            file_start = 0
            file_end = len(scan_df)  # ← 追加
            
            if file_boundaries is not None:
                for i in range(len(file_boundaries) - 1):
                    if file_boundaries[i] <= idx < file_boundaries[i + 1]:
                        file_start = file_boundaries[i]
                        file_end = file_boundaries[i + 1]  # ← 追加
                        break
                else:
                    # 最後のファイル
                    if idx >= file_boundaries[-1]:
                        file_start = file_boundaries[-1]
                        file_end = len(scan_df)  # ← 追加
            
            # ターゲット（未来のアクション）が**同じファイル内で**取得できるかチェック
            # ← 修正: ファイル境界を考慮
            if idx + prediction_steps >= file_end:
                continue
            
            # 履歴を取得
            scan_indices = []
            for i in range(scan_history_length):
                target_idx = idx - i * scan_history_stride
                
                if target_idx < file_start:
                    scan_indices.append(None)
                else:
                    scan_indices.append(target_idx)

            scan_indices = scan_indices[::-1]

            # 切れ端の最初のフレームを探す
            first_valid_idx = None
            for i in scan_indices:
                if i is not None:
                    first_valid_idx = i
                    break

            if first_valid_idx is None:
                first_valid_idx = file_start

            for i in range(len(scan_indices)):
                if scan_indices[i] is None:
                    scan_indices[i] = first_valid_idx

            scan_history = all_scans[scan_indices]
            
            # ターゲット（未来のアクション）を取得
            target_indices = [idx + i for i in range(1, prediction_steps + 1)]
            targets = all_actions[target_indices]
            
            self.scan_histories.append(scan_history)
            self.targets.append(targets)
        
        # リストからnumpy配列に変換
        self.scan_histories = np.array(self.scan_histories, dtype=np.float32)
        self.targets = np.array(self.targets, dtype=np.float32)
        
        print(f"  Created {len(self.scan_histories)} samples")
        print(f"  Scan histories shape: {self.scan_histories.shape}")
        print(f"  Targets shape: {self.targets.shape}")

    def __len__(self):
        return len(self.scan_histories)

    def __getitem__(self, idx):
        scan_history = self.scan_histories[idx].copy()
        targets = self.targets[idx].copy()
        
        # ノイズを追加
        noise_level = np.random.choice(self.noise_std)
        if noise_level > 0:
            noise = np.random.normal(0, noise_level, scan_history.shape)
            scan_history = scan_history + noise
        
        return scan_history, targets
