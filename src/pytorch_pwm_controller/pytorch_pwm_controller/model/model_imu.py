import numpy as np
import torch
import torch.nn as nn

# training_src/ と pytorch_pwm_controller/model/ の 2 か所で同じ内容にする（片方を変えたらもう片方も）
try:
    from .model import DNN
except ImportError:
    from model import DNN

# LiDAR Encoder に当たるパラメータ名の接頭辞（DNN と DNNIMU で共通）
ENCODER_PREFIXES = ("scan_embedding.", "pos_encoder.", "transformer_encoder.")


class DNNIMU(DNN):
    """
    LiDAR Encoder の出力と IMU 特徴を、回帰ヘッドの直前で結合するモデル

    scan (B, L, 1081) -> Encoder -> flatten (B, L*d_model) ─┐
                                                            concat -> fc1 -> fc2 -> fc3
    imu  (B, L, imu_dim) -> flatten -> imu_mlp (B, imu_hidden) ┘

    Encoder と fc2 / fc3 のパラメータ名は DNN と同じなので、
    LiDAR のみの学習済みモデルを load_lidar_pretrained でそのまま読み込める
    """

    def __init__(
        self,
        input_dim=1081,
        output_dim=2,
        scan_history_length=20,
        prediction_steps=1,
        d_model=128,
        nhead=4,
        num_layers=2,
        dim_feedforward=512,
        dropout=0.0,
        imu_dim=1,
        imu_hidden=32,
    ):
        """
        Args:
            imu_dim: 各時刻の IMU 特徴量の数（ヨーレートのみなら 1）
            imu_hidden: IMU 用 MLP の出力次元数
            その他: DNN と同じ
        """
        super().__init__(
            input_dim=input_dim,
            output_dim=output_dim,
            scan_history_length=scan_history_length,
            prediction_steps=prediction_steps,
            d_model=d_model,
            nhead=nhead,
            num_layers=num_layers,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
        )
        self.imu_dim = imu_dim
        self.imu_hidden = imu_hidden
        self.lidar_feature_dim = d_model * scan_history_length

        # IMU の履歴 (L * imu_dim) を特徴ベクトルに変換
        self.imu_mlp = nn.Sequential(
            nn.Linear(scan_history_length * imu_dim, imu_hidden),
            nn.ReLU(),
            nn.Linear(imu_hidden, imu_hidden),
            nn.ReLU(),
        )

        # LiDAR 特徴と IMU 特徴を結合したものを受けるように fc1 を作り直す
        self.fc1 = nn.Linear(self.lidar_feature_dim + imu_hidden, 256)

    def encode(self, x):
        """
        Args:
            x: スキャンデータ (batch_size, scan_history_length, scan_dim)

        Returns:
            LiDAR 特徴 (batch_size, scan_history_length * d_model)
        """
        x = self.scan_embedding(x)
        x = self.pos_encoder(x)
        x = self.transformer_encoder(x)
        return x.flatten(1)

    def forward(self, x, imu):
        """
        Args:
            x: スキャンデータ (batch_size, scan_history_length, scan_dim)
            imu: IMU データ (batch_size, scan_history_length, imu_dim)
        """
        lidar_feature = self.encode(x)
        imu_feature = self.imu_mlp(imu.flatten(1))
        x = torch.cat([lidar_feature, imu_feature], dim=1)

        x = self.relu(self.fc1(x))
        x = self.relu(self.fc2(x))
        x = self.fc3(x)

        batch_size = x.size(0)
        x = x.view(batch_size, self.prediction_steps, -1)

        return x

    def load_lidar_pretrained(self, state_dict):
        """
        LiDAR のみの学習済み DNN の重みを読み込む

        fc1 は LiDAR 側の列に学習済みの重みを入れ、IMU 側の列を 0 にする。
        読み込み直後の出力は、学習済みの DNN と同じになる。
        """
        state_dict = dict(state_dict)
        fc1_weight = state_dict.pop("fc1.weight")
        expected_shape = (self.fc1.out_features, self.lidar_feature_dim)
        if tuple(fc1_weight.shape) != expected_shape:
            raise ValueError(
                f"fc1.weight の形が合いません: {tuple(fc1_weight.shape)} != {expected_shape}"
                "（scan_history_length や d_model が学習済みモデルと違う可能性があります）"
            )

        missing, unexpected = self.load_state_dict(state_dict, strict=False)
        allowed_missing = {"fc1.weight"} | {f"imu_mlp.{k}" for k in self.imu_mlp.state_dict()}
        if unexpected or set(missing) - allowed_missing:
            raise ValueError(
                f"学習済みモデルのキーが合いません: missing={missing}, unexpected={unexpected}"
            )

        with torch.no_grad():
            self.fc1.weight.zero_()
            self.fc1.weight[:, : self.lidar_feature_dim].copy_(fc1_weight)

    def encoder_parameters(self):
        return [p for n, p in self.named_parameters() if n.startswith(ENCODER_PREFIXES)]

    def head_parameters(self):
        return [p for n, p in self.named_parameters() if not n.startswith(ENCODER_PREFIXES)]


class ModelIMU:
    """
    推論用のラッパー（train_imu.py が保存した model_type="transformer_imu" の .pth を読む）

    履歴長・IMU の特徴量・正規化統計は、すべてチェックポイントから読む
    """

    def __init__(self, path, d_model=128, nhead=4, num_layers=2, dim_feedforward=512):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        checkpoint = torch.load(path, map_location=self.device, weights_only=True)

        model_type = checkpoint.get("model_type")
        if model_type != "transformer_imu":
            raise ValueError(f"{path} は IMU モデルではありません（model_type={model_type}）")

        self.scan_history_length = checkpoint["scan_history_length"]
        self.scan_history_stride = checkpoint["scan_history_stride"]
        self.prediction_steps = checkpoint["prediction_steps"]
        self.imu_features = list(checkpoint["imu_features"])

        self.model = DNNIMU(
            scan_history_length=self.scan_history_length,
            prediction_steps=self.prediction_steps,
            d_model=d_model,
            nhead=nhead,
            num_layers=num_layers,
            dim_feedforward=dim_feedforward,
            imu_dim=len(self.imu_features),
            imu_hidden=checkpoint["imu_hidden"],
        )
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.to(self.device)
        self.model.eval()

        self.scan_mean = checkpoint["scan_mean"].to(self.device)
        self.scan_std = checkpoint["scan_std"].to(self.device)
        self.target_mean = checkpoint["target_mean"].to(self.device)
        self.target_std = checkpoint["target_std"].to(self.device)
        self.imu_mean = checkpoint["imu_mean"].to(self.device)
        self.imu_std = checkpoint["imu_std"].to(self.device)

        print(f"IMU model loaded from {path}")
        print(f"  history: length={self.scan_history_length}, stride={self.scan_history_stride}, "
              f"imu_features={self.imu_features}")

    def inference_next_step_only(self, scan_ranges, imu_values):
        """
        Args:
            scan_ranges: (scan_history_length, scan_dim) の生のスキャン
            imu_values: (scan_history_length, imu_dim) の生の IMU 特徴量

        Returns:
            次ステップの予測 [throttle, angle]（PWM µs）
        """
        scans = torch.from_numpy(np.asarray(scan_ranges, dtype=np.float32)).unsqueeze(0).to(self.device)
        imus = torch.from_numpy(np.asarray(imu_values, dtype=np.float32)).unsqueeze(0).to(self.device)
        scans = (scans - self.scan_mean) / (self.scan_std + 1e-8)
        imus = (imus - self.imu_mean) / (self.imu_std + 1e-8)

        with torch.no_grad():
            output = self.model(scans, imus)
            output = output * self.target_std + self.target_mean

        return output[0, 0].cpu().numpy().tolist()
