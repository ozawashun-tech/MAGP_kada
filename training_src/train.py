import torch
import torch.nn as nn
from dataset import (
    DataPreprocessConfig,
    RosbagDataset,
    custom_collate_fn,
    load_rosbags,
)
from model import DNN
from torch.utils.data import DataLoader, random_split
import os



class WeightedMSELoss(nn.Module):
    def __init__(self, weights):
        """
        Args:
            weights: 各次元の重み [weight_dim0, weight_dim1]
                    例: [1.0, 2.0] なら2次元目の損失を2倍重視
        """
        super().__init__()
        # Tensorに変換してから登録
        if not isinstance(weights, torch.Tensor):
            weights = torch.tensor(weights, dtype=torch.float32)
        self.register_buffer("weights", weights)

    def forward(self, output, target):
        # (batch, prediction_steps, 2) -> 各次元ごとにMSEを計算
        mse_per_dim = (output - target) ** 2  # (batch, prediction_steps, 2)

        # 重みを適用（weightsは自動的にoutputと同じデバイスになる）
        weighted_mse = mse_per_dim * self.weights  # broadcast: (B,P,2) * (2,)

        return weighted_mse.mean()


def compute_normalization_stats(dataset):
    """
    データセットから正規化統計量（平均と標準偏差）を計算

    Args:
        dataset: RosbagDatasetインスタンス

    Returns:
        tuple: (scan_mean, scan_std, target_mean, target_std)
    """
    print("\nComputing normalization statistics...")

    # 全データを収集
    all_scans = []
    all_targets = []

    for i in range(len(dataset)):
        scan, target = dataset[i]
        # numpy配列の場合はテンソルに変換
        if not isinstance(scan, torch.Tensor):
            scan = torch.from_numpy(scan)
        if not isinstance(target, torch.Tensor):
            target = torch.from_numpy(target)

        all_scans.append(scan)
        all_targets.append(target)

    # テンソルに変換
    all_scans = torch.stack(all_scans)  # (N, scan_history_length, scan_dim)
    all_targets = torch.stack(all_targets)  # (N, prediction_steps, 2)

    # 平均と標準偏差を計算
    # スキャンデータ: 全履歴・全次元で統計量を計算
    scan_mean = all_scans.mean()
    scan_std = all_scans.std()

    # ターゲットデータ: 全ステップ・全次元で統計量を計算
    target_mean = all_targets.mean()
    target_std = all_targets.std()

    print(f"  Scan - mean: {scan_mean:.4f}, std: {scan_std:.4f}")
    print(f"  Target - mean: {target_mean:.4f}, std: {target_std:.4f}")

    return scan_mean, scan_std, target_mean, target_std


def train_model(
    bag_paths,
    model_save_path,
    scan_history_length=4,
    scan_history_stride=1,
    prediction_steps=1,
    batch_size=32,
    epochs=50,
    learning_rate=0.001,
    pretrained_model_path=None,
    freeze_conv_layers=False,
    validation_split=0.2,
    early_stopping_patience=10,
    early_stopping_min_delta=1e-6,
    use_normalization=True,
    checkpoint_interval=10,  # 追加: チェックポイント保存間隔
):
    """
    モデルの学習を実行（正規化機能付き）

    Args:
        bag_paths: ROSバッグファイルのパスまたはパスのリスト
        model_save_path: モデルの保存先パス
        scan_history_length: スキャン入力履歴の長さ
        scan_history_stride: スキャン履歴の間引き間隔
        prediction_steps: 予測ステップ数
        batch_size: バッチサイズ
        epochs: エポック数
        learning_rate: 学習率
        pretrained_model_path: 事前学習済みモデルのパス（Noneの場合は新規学習）
        freeze_conv_layers: 畳み込み層を凍結するかどうか
        validation_split: 検証データの割合（0.0-1.0）
        early_stopping_patience: 検証lossが改善しないエポック数の閾値
        early_stopping_min_delta: 改善と判定する最小のloss減少量
        use_normalization: データ正規化を使用するかどうか
        checkpoint_interval: チェックポイント保存間隔（エポック数）
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # チェックポイント保存用のディレクトリを作成
    checkpoint_dir = "../model/checkpoints"
    os.makedirs(checkpoint_dir, exist_ok=True)
    print(f"Checkpoints will be saved to: {checkpoint_dir}")

    # データの読み込み
    print("Loading data...")
    config = DataPreprocessConfig(
        trim_front=0.01,
        trim_back=0.01,
        stop_threshold=[1620, 2300],
        stop_samples=10000,
        stop_margin_before=20,
        stop_margin_after=20,
    )
    scan_df, joy_df, file_boundaries = load_rosbags(bag_paths, preprocess_configs=config)

    # データセットの作成
    print("\nCreating dataset with:")
    print(f"  scan_history_length: {scan_history_length}")
    print(f"  scan_history_stride: {scan_history_stride}")
    print(f"  prediction_steps: {prediction_steps}")
    print(f"  use_normalization: {use_normalization}")

    dataset = RosbagDataset(
        scan_df,
        joy_df,
        file_boundaries,
        scan_history_length=scan_history_length,
        scan_history_stride=scan_history_stride,
        prediction_steps=prediction_steps,
        noise_std=[0.0, 0.05],
    )

    # 正規化統計量の計算
    if use_normalization:
        scan_mean, scan_std, target_mean, target_std = compute_normalization_stats(
            dataset
        )

        # テンソルをデバイスに転送
        scan_mean = scan_mean.to(device)
        scan_std = scan_std.to(device)
        target_mean = target_mean.to(device)
        target_std = target_std.to(device)
    else:
        scan_mean = scan_std = None
        target_mean = target_std = None

    # データセットを訓練用と検証用に分割
    total_size = len(dataset)
    val_size = int(total_size * validation_split)
    train_size = total_size - val_size

    train_dataset, val_dataset = random_split(
        dataset,
        [train_size, val_size],
        generator=torch.Generator().manual_seed(42),  # 再現性のため
    )

    print("\nDataset split:")
    print(f"  Training samples: {train_size}")
    print(f"  Validation samples: {val_size}")

    # DataLoaderの作成
    train_loader = DataLoader(
        train_dataset, batch_size=batch_size, shuffle=True, collate_fn=custom_collate_fn
    )
    val_loader = DataLoader(
        val_dataset, batch_size=batch_size, shuffle=False, collate_fn=custom_collate_fn
    )

    # モデルの作成
    model = DNN(
        scan_history_length=scan_history_length,
        prediction_steps=prediction_steps,
    )

    # 事前学習済みモデルのロード
    if pretrained_model_path is not None:
        print(f"\nLoading pretrained model from {pretrained_model_path}")
        try:
            checkpoint = torch.load(
                pretrained_model_path, map_location=device, weights_only=True
            )
            
            # モデルの重みをロード
            if 'model_state_dict' in checkpoint:
                pretrained_dict = checkpoint['model_state_dict']
            else:
                pretrained_dict = checkpoint
                
            model_dict = model.state_dict()
            pretrained_dict = {
                k: v
                for k, v in pretrained_dict.items()
                if k in model_dict and v.shape == model_dict[k].shape
            }
            model_dict.update(pretrained_dict)
            model.load_state_dict(model_dict)
            
            # ★ 正規化統計量もロード（重要！）
            if use_normalization and 'scan_mean' in checkpoint:
                scan_mean = checkpoint['scan_mean'].to(device)
                scan_std = checkpoint['scan_std'].to(device)
                target_mean = checkpoint['target_mean'].to(device)
                target_std = checkpoint['target_std'].to(device)
                print("  Loaded normalization statistics from pretrained model")
            else:
                # 統計量がない場合は新規計算
                scan_mean, scan_std, target_mean, target_std = \
                    compute_normalization_stats(dataset)
                scan_mean = scan_mean.to(device)
                scan_std = scan_std.to(device)
                target_mean = target_mean.to(device)
                target_std = target_std.to(device)
                
        except Exception as e:
            print(f"  Warning: Could not load pretrained model: {e}")

    model.to(device)

    # 畳み込み層の凍結
    if freeze_conv_layers:
        print("\nFreezing convolutional layers...")
        for name, param in model.named_parameters():
            if name.startswith("conv"):
                param.requires_grad = False
                print(f"  Frozen: {name}")

        # 訓練可能なパラメータ数を表示
        trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        total_params = sum(p.numel() for p in model.parameters())
        print(f"  Trainable parameters: {trainable_params:,} / {total_params:,}")

    # 損失関数と最適化手法
    # criterion = nn.MSELoss()
    criterion = WeightedMSELoss(weights=[1.0, 1.0])  # 2次元目(angular)を2倍重視
    criterion.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    # アーリーストッピングの初期化
    best_val_loss = float("inf")
    patience_counter = 0
    best_model_state = None

    # 正規化関数の定義
    def normalize_batch(scans, targets):
        if use_normalization:
            scans = (scans - scan_mean) / (scan_std + 1e-8)
            targets = (targets - target_mean) / (target_std + 1e-8)
        return scans, targets

    # チェックポイント保存関数
    def save_checkpoint(epoch, is_best=False):
        checkpoint = {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "train_loss": avg_train_loss,
            "val_loss": avg_val_loss,
            "scan_mean": scan_mean.cpu() if scan_mean is not None else None,
            "scan_std": scan_std.cpu() if scan_std is not None else None,
            "target_mean": target_mean.cpu() if target_mean is not None else None,
            "target_std": target_std.cpu() if target_std is not None else None,
        }
        
        if is_best:
            checkpoint_path = os.path.join(checkpoint_dir, "best_model.pth")
            torch.save(checkpoint, checkpoint_path)
            print(f"  → Best model saved: {checkpoint_path}")
        else:
            checkpoint_path = os.path.join(checkpoint_dir, f"epoch_{epoch:03d}.pth")
            torch.save(checkpoint, checkpoint_path)
            print(f"  → Checkpoint saved: {checkpoint_path}")

    # 学習ループ
    print(f"\nStarting training for {epochs} epochs...")
    print(f"Checkpoint interval: every {checkpoint_interval} epochs")
    print(
        f"Early stopping: patience={early_stopping_patience}, min_delta={early_stopping_min_delta}"
    )

    for epoch in range(epochs):
        # 訓練フェーズ
        model.train()
        total_train_loss = 0.0
        num_train_batches = 0

        for batch_data in train_loader:
            scans, targets = batch_data
            scans = scans.to(device)
            targets = targets.to(device)

            # 正規化
            scans, targets = normalize_batch(scans, targets)

            # Forward pass
            outputs = model(scans)
            loss = criterion(outputs, targets)
    
            # Backward pass
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_train_loss += loss.item()
            num_train_batches += 1

        avg_train_loss = total_train_loss / num_train_batches

        # 検証フェーズ
        model.eval()
        total_val_loss = 0.0
        num_val_batches = 0

        with torch.no_grad():
            for batch_data in val_loader:
                scans, targets = batch_data
                scans = scans.to(device)
                targets = targets.to(device)

                # 正規化
                scans, targets = normalize_batch(scans, targets)

                # Forward pass
                outputs = model(scans)
                loss = criterion(outputs, targets)

                total_val_loss += loss.item()
                num_val_batches += 1

        # 出力サンプル（逆正規化して表示）
        if use_normalization:
            output_denorm = outputs[0][0] * target_std + target_mean
            print(f"output sample (denormalized): {output_denorm.cpu().numpy()}")
        else:
            print(f"output sample: {outputs[0][0].cpu().numpy()}")

        avg_val_loss = total_val_loss / num_val_batches

        # 結果の表示
        print(
            f"Epoch [{epoch + 1}/{epochs}], "
            f"Train Loss: {avg_train_loss:.6f}, "
            f"Val Loss: {avg_val_loss:.6f}"
        )

        # 定期的なチェックポイント保存
        if (epoch + 1) % checkpoint_interval == 0:
            save_checkpoint(epoch + 1, is_best=False)

        # アーリーストッピングのチェック
        if avg_val_loss < best_val_loss - early_stopping_min_delta:
            # 検証lossが改善した
            best_val_loss = avg_val_loss
            patience_counter = 0
            best_model_state = model.state_dict().copy()
            # ベストモデルも保存
            save_checkpoint(epoch + 1, is_best=True)
        else:
            # 検証lossが改善しなかった
            patience_counter += 1
            if patience_counter >= early_stopping_patience:
                print(f"\nEarly stopping triggered at epoch {epoch + 1}")
                print(f"Best validation loss: {best_val_loss:.6f}")
                break

    # 最終的なベストモデルを保存
    if best_model_state is not None:
        checkpoint = {
            "model_state_dict": best_model_state,
            "scan_mean": scan_mean.cpu() if scan_mean is not None else None,
            "scan_std": scan_std.cpu() if scan_std is not None else None,
            "target_mean": target_mean.cpu() if target_mean is not None else None,
            "target_std": target_std.cpu() if target_std is not None else None,
        }
        torch.save(checkpoint, model_save_path)
        print(f"\nBest model saved to {model_save_path}")
        print(f"Best validation loss: {best_val_loss:.6f}")
    else:
        # アーリーストッピングが発動しなかった場合は最後のモデルを保存
        checkpoint = {
            "model_state_dict": model.state_dict(),
            "scan_mean": scan_mean.cpu() if scan_mean is not None else None,
            "scan_std": scan_std.cpu() if scan_std is not None else None,
            "target_mean": target_mean.cpu() if target_mean is not None else None,
            "target_std": target_std.cpu() if target_std is not None else None,
        }
        torch.save(checkpoint, model_save_path)
        print(f"\nFinal model saved to {model_save_path}")


if __name__ == "__main__":
    train_model(
        bag_paths="../dataset/train/20260401",
        model_save_path="../model/transformer/model.pth",
        scan_history_length=20,
        scan_history_stride=10,
        prediction_steps=1,
        batch_size=32,
        epochs=128,
        learning_rate=0.0001,
        validation_split=0.2,
        early_stopping_patience=10,
        use_normalization=True,
        checkpoint_interval=10,
    )