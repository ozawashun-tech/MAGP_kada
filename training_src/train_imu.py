import copy
import datetime
import os
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from dataset import DataPreprocessConfig
from dataset_imu import (
    IMU_FEATURES,
    RosbagIMUDataset,
    imu_collate_fn,
    load_bags_with_imu,
    select_imu_bags,
)
from jetson_sync import fetch_latest_bags
from model import DNN
from model_imu import ENCODER_PREFIXES, DNNIMU
from train import WeightedMSELoss, compute_normalization_stats

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO_ROOT / "model" / "transformer_imu"
CHECKPOINT_ROOT = REPO_ROOT / "model" / "checkpoints_imu"

# IMU の特徴量の略称（run 名に使う）
FEATURE_ABBREV = {"angular_velocity.z": "gz"}


class _ScanTargetView(torch.utils.data.Dataset):
    """compute_normalization_stats に渡すため、(scan, target) だけを返すビュー"""

    def __init__(self, dataset):
        self.dataset = dataset

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, idx):
        scan, _, target = self.dataset[idx]
        return scan, target


def split_bags(bag_paths, val_bags=None):
    """
    bag 単位で学習用と検証用に分ける

    Args:
        bag_paths: 使う bag のリスト
        val_bags: 検証に使う bag の名前またはパスのリスト。None なら最新の 1 本

    Returns:
        (train_bags, val_bags)
    """
    bag_paths = sorted((Path(p) for p in bag_paths), key=lambda p: p.name)
    if len(bag_paths) < 2:
        raise ValueError(
            f"IMU 入りの bag が {len(bag_paths)} 本しかありません。学習用と検証用に最低 2 本必要です"
        )
    if val_bags is None:
        return bag_paths[:-1], bag_paths[-1:]

    val_names = {Path(v).name for v in val_bags}
    val = [p for p in bag_paths if p.name in val_names]
    train = [p for p in bag_paths if p.name not in val_names]
    if len(val) != len(val_names):
        raise ValueError(f"val_bags が見つかりません: {val_names - {p.name for p in val}}")
    if not train:
        raise ValueError("学習用の bag が残っていません")
    return train, val


def make_run_name(use_imu, scan_history_length, scan_history_stride, stages, features=IMU_FEATURES):
    date = datetime.date.today().strftime("%Y%m%d")
    if use_imu:
        abbrev = "-".join(FEATURE_ABBREV.get(f, f.replace(".", "")) for f in features)
        prefix = f"imu_{date}_{abbrev}"
    else:
        prefix = f"lidar_{date}"
    return f"{prefix}_len{scan_history_length}_str{scan_history_stride}_s{stages}"


def set_encoder_frozen(model, frozen):
    """Encoder のパラメータを凍結（または解除）する"""
    for name, param in model.named_parameters():
        if name.startswith(ENCODER_PREFIXES):
            param.requires_grad = not frozen


def set_train_mode(model, encoder_frozen):
    """学習モードにする。Encoder を凍結しているときは Encoder だけ eval のままにする"""
    model.train()
    if encoder_frozen:
        model.scan_embedding.eval()
        model.pos_encoder.eval()
        model.transformer_encoder.eval()


def train_imu_model(
    train_bags,
    val_bags,
    pretrained_model_path,
    use_imu=True,
    run_name=None,
    scan_history_length=20,
    scan_history_stride=10,
    prediction_steps=1,
    imu_hidden=32,
    imu_dropout_p=0.25,
    imu_noise_std=0.0,
    batch_size=32,
    epochs=100,
    learning_rate=1e-4,
    finetune_epochs=0,
    finetune_lr=1e-5,
    early_stopping_patience=10,
    early_stopping_min_delta=1e-6,
    checkpoint_interval=10,
    preprocess_config=None,
    seed=42,
    model_dir=MODEL_DIR,
    checkpoint_root=CHECKPOINT_ROOT,
):
    """
    LiDAR のみの学習済みモデルを初期値にして、IMU 入りのモデル（またはベースライン）を学習する

    Stage 1: Encoder を凍結し、回帰ヘッド（と IMU 用 MLP）を学習する
    Stage 2: finetune_epochs > 0 のとき、全体を finetune_lr で学習する

    Args:
        train_bags: 学習用の bag のリスト
        val_bags: 検証用の bag のリスト（bag 単位で分ける）
        pretrained_model_path: LiDAR のみの学習済みモデル（.pth）。読み込むだけで変更しない
        use_imu: False なら IMU を使わない DNN で同じ手順を行う（比較用のベースライン）
        run_name: 保存名。None なら make_run_name で決める
        imu_hidden: IMU 用 MLP の出力次元数
        imu_dropout_p: 学習時に IMU 入力をゼロにする確率（modality dropout）
        imu_noise_std: IMU に足すノイズの標準偏差（生の値の単位）
        learning_rate: Stage 1 の学習率
        finetune_epochs: Stage 2 のエポック数（0 なら Stage 2 を行わない）
        finetune_lr: Stage 2 の学習率
        preprocess_config: DataPreprocessConfig（None なら train.py と同じ設定）
        seed: 乱数シード
        model_dir: 完成モデルの保存先ディレクトリ
        checkpoint_root: チェックポイントの保存先の親ディレクトリ

    Returns:
        str: 保存したモデルのパス
    """
    torch.manual_seed(seed)
    np.random.seed(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # 保存先（既存のファイルは上書きしない）
    stages = 2 if finetune_epochs > 0 else 1
    if run_name is None:
        run_name = make_run_name(use_imu, scan_history_length, scan_history_stride, stages)
    model_save_path = Path(model_dir) / f"{run_name}.pth"
    checkpoint_dir = Path(checkpoint_root) / run_name
    if model_save_path.exists():
        raise FileExistsError(f"{model_save_path} がすでにあります。run_name を変えてください")
    if checkpoint_dir.exists() and any(checkpoint_dir.iterdir()):
        raise FileExistsError(f"{checkpoint_dir} がすでにあります。run_name を変えてください")
    print(f"Run name: {run_name}")
    print(f"  Model: {model_save_path}")
    print(f"  Checkpoints: {checkpoint_dir}")

    # データの読み込み
    if preprocess_config is None:
        preprocess_config = DataPreprocessConfig(
            trim_front=0.01,
            trim_back=0.01,
            stop_threshold=[1620, 2300],
            stop_samples=10000,
            stop_margin_before=20,
            stop_margin_after=20,
        )
    dataset_kwargs = dict(
        scan_history_length=scan_history_length,
        scan_history_stride=scan_history_stride,
        prediction_steps=prediction_steps,
        imu_noise_std=imu_noise_std,
    )
    print("\nLoading training data...")
    train_dataset = RosbagIMUDataset(
        *load_bags_with_imu(train_bags, preprocess_config),
        noise_std=[0.0, 0.05],
        **dataset_kwargs,
    )
    print("\nLoading validation data...")
    val_dataset = RosbagIMUDataset(
        *load_bags_with_imu(val_bags, preprocess_config),
        noise_std=[0.0],
        **dataset_kwargs,
    )
    if len(train_dataset) == 0 or len(val_dataset) == 0:
        raise ValueError("学習用または検証用のサンプルが 0 件です")
    print("\nDataset split (by bag):")
    print(f"  Training: {len(train_dataset)} samples from {[Path(p).name for p in train_bags]}")
    print(f"  Validation: {len(val_dataset)} samples from {[Path(p).name for p in val_bags]}")

    train_loader = DataLoader(
        train_dataset, batch_size=batch_size, shuffle=True, collate_fn=imu_collate_fn
    )
    val_loader = DataLoader(
        val_dataset, batch_size=batch_size, shuffle=False, collate_fn=imu_collate_fn
    )

    # 学習済みモデルの読み込み
    print(f"\nLoading pretrained model from {pretrained_model_path}")
    pretrained = torch.load(pretrained_model_path, map_location="cpu", weights_only=True)
    pretrained_state = pretrained.get("model_state_dict", pretrained)

    model_kwargs = dict(
        scan_history_length=scan_history_length,
        prediction_steps=prediction_steps,
    )
    if use_imu:
        model = DNNIMU(imu_dim=len(IMU_FEATURES), imu_hidden=imu_hidden, **model_kwargs)
        model.load_lidar_pretrained(pretrained_state)
    else:
        model = DNN(**model_kwargs)
        model.load_state_dict(pretrained_state)
    model.to(device)

    # 正規化統計: scan / target は学習済みモデルの値を使う（凍結した Encoder の入力分布を変えないため）
    if pretrained.get("scan_mean") is not None:
        scan_mean, scan_std = pretrained["scan_mean"], pretrained["scan_std"]
        target_mean, target_std = pretrained["target_mean"], pretrained["target_std"]
        print("  Using normalization statistics from pretrained model")
    else:
        print("  Warning: pretrained model has no normalization statistics; computing from data")
        scan_mean, scan_std, target_mean, target_std = compute_normalization_stats(
            _ScanTargetView(train_dataset)
        )
    imu_rows = train_dataset.all_imu[np.unique(train_dataset.history_indices)]
    imu_mean = torch.tensor(imu_rows.mean(axis=0), dtype=torch.float32)
    imu_std = torch.tensor(imu_rows.std(axis=0), dtype=torch.float32)
    print(f"  IMU {IMU_FEATURES} - mean: {imu_mean.tolist()}, std: {imu_std.tolist()}")

    scan_mean, scan_std = scan_mean.to(device), scan_std.to(device)
    target_mean, target_std = target_mean.to(device), target_std.to(device)
    imu_mean, imu_std = imu_mean.to(device), imu_std.to(device)

    criterion = WeightedMSELoss(weights=[1.0, 1.0]).to(device)

    def prepare_batch(scans, imus, targets, train=False, zero_imu=False):
        scans = (scans.to(device) - scan_mean) / (scan_std + 1e-8)
        imus = (imus.to(device) - imu_mean) / (imu_std + 1e-8)
        targets = targets.to(device)
        targets_norm = (targets - target_mean) / (target_std + 1e-8)
        if zero_imu:
            imus = torch.zeros_like(imus)
        elif train and imu_dropout_p > 0:
            # modality dropout: サンプルごとに IMU 入力全体を 0（= 平均値）にする
            drop = torch.rand(imus.size(0), 1, 1, device=device) < imu_dropout_p
            imus = imus.masked_fill(drop, 0.0)
        return scans, imus, targets, targets_norm

    def forward(scans, imus):
        return model(scans, imus) if use_imu else model(scans)

    def evaluate(zero_imu=False):
        """検証 loss（正規化後、サンプル平均）と MAE（µs）を返す"""
        model.eval()
        total_loss = 0.0
        abs_err = torch.zeros(2, device=device)
        n = 0
        with torch.no_grad():
            for batch in val_loader:
                scans, imus, targets, targets_norm = prepare_batch(*batch, zero_imu=zero_imu)
                outputs = forward(scans, imus)
                total_loss += criterion(outputs, targets_norm).item() * scans.size(0)
                outputs_us = outputs * target_std + target_mean
                abs_err += (outputs_us - targets).abs().sum(dim=(0, 1))
                n += scans.size(0)
        mae = (abs_err / (n * prediction_steps)).tolist()
        return {"val_loss": total_loss / n, "throttle_mae": mae[0], "steering_mae": mae[1]}

    def evaluate_all():
        metrics = evaluate()
        if use_imu:
            metrics["steering_mae_imu_zero"] = evaluate(zero_imu=True)["steering_mae"]
        return metrics

    def build_checkpoint(stage, state_dict, **extra):
        return {
            "model_state_dict": state_dict,
            "scan_mean": scan_mean.cpu(),
            "scan_std": scan_std.cpu(),
            "target_mean": target_mean.cpu(),
            "target_std": target_std.cpu(),
            "model_type": "transformer_imu" if use_imu else "transformer",
            "imu_features": list(IMU_FEATURES) if use_imu else [],
            "imu_mean": imu_mean.cpu() if use_imu else None,
            "imu_std": imu_std.cpu() if use_imu else None,
            "imu_hidden": imu_hidden if use_imu else None,
            "imu_dropout_p": imu_dropout_p if use_imu else None,
            "scan_history_length": scan_history_length,
            "scan_history_stride": scan_history_stride,
            "prediction_steps": prediction_steps,
            "pretrained_from": str(pretrained_model_path),
            "stage": stage,
            **extra,
        }

    def save(checkpoint, path):
        if path.exists():
            raise FileExistsError(f"{path} がすでにあります（上書きしません）")
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(checkpoint, path)
        print(f"  → Saved: {path}")

    def run_stage(stage, stage_epochs, lr, encoder_frozen):
        set_encoder_frozen(model, encoder_frozen)
        params = [p for p in model.parameters() if p.requires_grad]
        trainable = sum(p.numel() for p in params)
        total = sum(p.numel() for p in model.parameters())
        print(f"\n=== Stage {stage}: encoder {'frozen' if encoder_frozen else 'trainable'}, "
              f"lr={lr}, epochs={stage_epochs} ===")
        print(f"  Trainable parameters: {trainable:,} / {total:,}")
        optimizer = torch.optim.Adam(params, lr=lr)

        best_val_loss = evaluate()["val_loss"]
        best_state = copy.deepcopy(model.state_dict())
        patience_counter = 0
        print(f"  Initial val loss: {best_val_loss:.6f}")

        for epoch in range(1, stage_epochs + 1):
            set_train_mode(model, encoder_frozen)
            total_loss = 0.0
            n = 0
            for batch in train_loader:
                scans, imus, _, targets_norm = prepare_batch(*batch, train=True)
                loss = criterion(forward(scans, imus), targets_norm)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                total_loss += loss.item() * scans.size(0)
                n += scans.size(0)
            train_loss = total_loss / n
            metrics = evaluate()
            print(
                f"Stage {stage} Epoch [{epoch}/{stage_epochs}], "
                f"Train Loss: {train_loss:.6f}, Val Loss: {metrics['val_loss']:.6f}, "
                f"MAE throttle/steering: {metrics['throttle_mae']:.1f}/{metrics['steering_mae']:.1f} µs"
            )

            if epoch % checkpoint_interval == 0:
                save(
                    build_checkpoint(
                        stage, model.state_dict(), epoch=epoch,
                        optimizer_state_dict=optimizer.state_dict(),
                        train_loss=train_loss, val_loss=metrics["val_loss"],
                    ),
                    checkpoint_dir / f"s{stage}_epoch_{epoch:03d}.pth",
                )

            if metrics["val_loss"] < best_val_loss - early_stopping_min_delta:
                best_val_loss = metrics["val_loss"]
                best_state = copy.deepcopy(model.state_dict())
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= early_stopping_patience:
                    print(f"\nEarly stopping triggered at epoch {epoch}")
                    break

        # 最後の epoch の重みのまま次のステージ・保存に進む（train.py と同じ）
        # 検証 loss が最良の重みは best_stage{n}.pth に残す
        print(f"  Best val loss: {best_val_loss:.6f}（使うのは最後の epoch: {metrics['val_loss']:.6f}）")
        save(build_checkpoint(stage, best_state, val_loss=best_val_loss),
             checkpoint_dir / f"best_stage{stage}.pth")
        return evaluate_all()

    results = {"pretrained": evaluate_all()}
    results["stage1"] = run_stage(1, epochs, learning_rate, encoder_frozen=True)
    if finetune_epochs > 0:
        results["stage2"] = run_stage(2, finetune_epochs, finetune_lr, encoder_frozen=False)

    save(
        build_checkpoint(
            stages, copy.deepcopy(model.state_dict()),
            metrics=results,
            train_bags=[Path(p).name for p in train_bags],
            val_bags=[Path(p).name for p in val_bags],
            seed=seed,
        ),
        model_save_path,
    )

    # 結果の表示
    print(f"\n=== Results: {run_name} ({'IMU' if use_imu else 'LiDAR only'}) ===")
    header = f"{'':<12}{'val loss':>10}{'throttle MAE':>14}{'steering MAE':>14}"
    if use_imu:
        header += f"{'steer MAE (IMU=0)':>19}"
    print(header)
    for name, m in results.items():
        row = f"{name:<12}{m['val_loss']:>10.4f}{m['throttle_mae']:>14.1f}{m['steering_mae']:>14.1f}"
        if use_imu:
            row += f"{m['steering_mae_imu_zero']:>19.1f}"
        print(row)
    print("（pretrained は学習済みの LiDAR モデルそのまま。IMU ありでは IMU の寄与が 0 の状態）")

    return str(model_save_path)


if __name__ == "__main__":
    # Jetson から最新の bag を取ってきて、/scan・/pwm_input・/imu/data がそろったものだけ使う
    bags = select_imu_bags(fetch_latest_bags(count=10))
    train_bags, val_bags = split_bags(bags, val_bags=None)  # 既定では最新の 1 本を検証用にする

    train_imu_model(
        train_bags=train_bags,
        val_bags=val_bags,
        pretrained_model_path=REPO_ROOT / "model" / "transformer" / "my_model.pth",
        use_imu=True,  # False でベースライン（LiDAR のみ）
        scan_history_length=20,
        scan_history_stride=10,
        prediction_steps=1,
        imu_hidden=32,
        imu_dropout_p=0.25,
        batch_size=32,
        epochs=100,
        learning_rate=1e-4,
        finetune_epochs=0,  # Stage 2 を行うときは 10〜20 程度にする
        finetune_lr=1e-5,
        early_stopping_patience=10,
        checkpoint_interval=10,
        seed=42,
    )
