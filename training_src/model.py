import numpy as np
import torch
import torch.nn as nn
import math


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=5000):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)  # (1, max_len, d_model)
        self.register_buffer('pe', pe)

    def forward(self, x):
        """
        Args:
            x: (batch_size, seq_len, d_model)
        """
        return x + self.pe[:, :x.size(1), :]


class DNN(nn.Module):
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
    ):
        """
        Args:
            input_dim: 入力スキャンデータの次元数
            output_dim: 各ステップの出力次元（throttle, angleで2）
            scan_history_length: スキャンデータの履歴長
            prediction_steps: 予測する未来のステップ数
            d_model: Transformerの隠れ層次元数
            nhead: Multi-head Attentionのヘッド数
            num_layers: Transformer Encoderの層数
            dim_feedforward: Feedforward層の次元数
            dropout: ドロップアウト率
        """
        super().__init__()
        self.scan_history_length = scan_history_length
        self.prediction_steps = prediction_steps
        self.d_model = d_model

        # スキャンデータを埋め込み空間に射影
        self.scan_embedding = nn.Linear(input_dim, d_model)
        
        # Positional Encoding
        self.pos_encoder = PositionalEncoding(d_model, max_len=scan_history_length)
        
        # Transformer Encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True,
        )
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        # 出力層
        self.fc1 = nn.Linear(d_model * scan_history_length, 256)
        self.fc2 = nn.Linear(256, 128)
        self.fc3 = nn.Linear(128, prediction_steps * output_dim)
        
        # self.dropout = nn.Dropout(dropout)
        self.relu = nn.ReLU()

    def forward(self, x):
        """
        Args:
            x: スキャンデータ (batch_size, scan_history_length, scan_dim)
        """
        # スキャンデータを埋め込み
        x = self.scan_embedding(x)  # (batch_size, scan_history_length, d_model)
        
        # Positional Encoding を追加
        x = self.pos_encoder(x)
        
        # Transformer Encoder
        x = self.transformer_encoder(x)  # (batch_size, scan_history_length, d_model)
        
        # 系列全体を結合
        x = x.flatten(1)  # (batch_size, scan_history_length * d_model)
        
        # 全結合層で予測
        x = self.relu(self.fc1(x))
        # x = self.dropout(x)
        x = self.relu(self.fc2(x))
        # x = self.dropout(x)
        x = self.fc3(x)
        
        # (batch_size, prediction_steps * output_dim) -> (batch_size, prediction_steps, output_dim)
        batch_size = x.size(0)
        x = x.view(batch_size, self.prediction_steps, -1)
        
        return x


class Model:
    def __init__(
        self,
        path,
        prediction_steps=1,
        scan_history_length=20,
        d_model=128,
        nhead=4,
        num_layers=2,
        dim_feedforward=512,
        dropout=0.0,
    ):
        """
        Args:
            path: モデルの重みファイルのパス（.pthファイル）
            prediction_steps: 予測ステップ数（学習時と同じ値を指定）
            scan_history_length: スキャン履歴長（学習時と同じ値を指定）
            d_model: Transformerの隠れ層次元数
            nhead: Multi-head Attentionのヘッド数
            num_layers: Transformer Encoderの層数
            dim_feedforward: Feedforward層の次元数
            dropout: ドロップアウト率
        """
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.prediction_steps = prediction_steps
        self.scan_history_length = scan_history_length

        # モデルのインスタンスを作成
        self.model = DNN(
            scan_history_length=scan_history_length,
            prediction_steps=prediction_steps,
            d_model=d_model,
            nhead=nhead,
            num_layers=num_layers,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
        )

        # チェックポイントを読み込み
        checkpoint = torch.load(path, map_location=self.device, weights_only=True)
        
        # モデルの重みを読み込み
        self.model.load_state_dict(checkpoint['model_state_dict'])

        # 正規化統計量を読み込み
        self.scan_mean = checkpoint.get('scan_mean', None)
        self.scan_std = checkpoint.get('scan_std', None)
        self.target_mean = checkpoint.get('target_mean', None)
        self.target_std = checkpoint.get('target_std', None)

        # デバイスに転送
        if self.scan_mean is not None:
            self.scan_mean = self.scan_mean.to(self.device)
            self.scan_std = self.scan_std.to(self.device)
        if self.target_mean is not None:
            self.target_mean = self.target_mean.to(self.device)
            self.target_std = self.target_std.to(self.device)

        # デバイスに転送して評価モードに設定
        self.model.to(self.device)
        self.model.eval()

        print(f"Model loaded from {path}")
        print(f"Normalization stats loaded: scan={self.scan_mean is not None}, "
              f"target={self.target_mean is not None}")

    def _normalize_scan(self, scan_data):
        """スキャンデータを正規化"""
        if self.scan_mean is not None and self.scan_std is not None:
            return (scan_data - self.scan_mean) / (self.scan_std + 1e-8)
        return scan_data

    def _denormalize_target(self, target_data):
        """ターゲットデータを逆正規化"""
        if self.target_mean is not None and self.target_std is not None:
            return target_data * self.target_std + self.target_mean
        return target_data

    def inference(self, scan_ranges: np.ndarray) -> list:
        """
        推論を実行

        Args:
            scan_ranges: スキャンデータ (scan_history_length, scan_dim) の形状

        Returns:
            予測結果のリスト
            - prediction_steps=1: [throttle, angle]
            - prediction_steps>1: [[throttle1, angle1], [throttle2, angle2], ...]
        """
        # 生データをテンソルに変換
        input_data = torch.from_numpy(np.array(scan_ranges, dtype=np.float32)).unsqueeze(0)
        input_data = input_data.to(self.device)

        # 正規化
        input_data = self._normalize_scan(input_data)

        # 推論
        with torch.no_grad():
            output = self.model(input_data)
            # 逆正規化して生データに戻す
            output = self._denormalize_target(output)

        # (1, prediction_steps, 2) -> (prediction_steps, 2)
        predictions = output.cpu().numpy().squeeze(0)

        if self.prediction_steps == 1:
            # 単一ステップの場合は1次元リストを返す
            return predictions.flatten().tolist()
        else:
            # 複数ステップの場合は2次元リストを返す
            return predictions.tolist()

    def inference_next_step_only(self, scan_ranges: np.ndarray) -> list:
        """
        次のステップのみを予測（複数ステップモデルでも最初のステップだけ返す）

        Args:
            scan_ranges: スキャンデータ (scan_history_length, scan_dim) の形状

        Returns:
            次ステップの予測 [throttle, angle]
        """
        predictions = self.inference(scan_ranges)

        if self.prediction_steps == 1:
            return predictions
        else:
            return predictions[0]
