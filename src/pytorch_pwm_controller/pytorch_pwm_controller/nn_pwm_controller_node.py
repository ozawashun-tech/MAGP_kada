#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import TwistStamped
import numpy as np
from ament_index_python.packages import get_package_share_directory
import os
import time
from .model.model import Model


class NNControllerNode(Node):
    def __init__(self):
        super().__init__('nn_controller_node')
        self.start_time = time.time()
        
        # scan_rangesのシーケンスを保持するリスト
        self.scan_sequence = []
        
        # パラメータの宣言
        self.declare_parameter('model_path', '')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('pwm_topic', '/torch_pwm')
        self.declare_parameter('prediction_steps', 1)
        self.declare_parameter('scan_history_length', 20)
        self.declare_parameter('scan_history_stride', 10)
        
        # パラメータの取得
        model_path = self.get_parameter('model_path').get_parameter_value().string_value
        prediction_steps = self.get_parameter('prediction_steps').get_parameter_value().integer_value
        self.scan_history_length = self.get_parameter('scan_history_length').get_parameter_value().integer_value
        self.scan_history_stride = self.get_parameter('scan_history_stride').get_parameter_value().integer_value
        
        # モデルパスが指定されていない場合はデフォルトパスを使用
        if not model_path:
            package_share_dir = get_package_share_directory('pytorch_pwm_controller')
            model_path = os.path.join(package_share_dir, 'model', 'model_v9.pth')
        
        scan_topic = self.get_parameter('scan_topic').get_parameter_value().string_value
        pwm_topic = self.get_parameter('pwm_topic').get_parameter_value().string_value
        
        self.get_logger().info(f'Loading model from: {model_path}')
        self.get_logger().info(f'Prediction steps: {prediction_steps}')
        self.get_logger().info(f'Scan history: length={self.scan_history_length}, stride={self.scan_history_stride}')
        
        # モデルの初期化
        try:
            self.model = Model(
                model_path,
                prediction_steps=prediction_steps,
                scan_history_length=self.scan_history_length,
            )
            self.get_logger().info('Model loaded successfully')
        except Exception as e:
            self.get_logger().error(f'Failed to load model: {str(e)}')
            raise
        
        # Subscriber
        self.scan_sub = self.create_subscription(
            LaserScan,
            scan_topic,
            self.scan_callback,
            10
        )
        
        # Publisher (TwistStamped for PWM commands)
        self.pwm_pub = self.create_publisher(TwistStamped, pwm_topic, 10)
        
        self.get_logger().info(f'Subscribing to: {scan_topic}')
        self.get_logger().info(f'Publishing to: {pwm_topic}')
    
    def scan_callback(self, msg: LaserScan):
        try:
            # LaserScanデータをNumPy配列に変換
            scan_ranges = np.array(msg.ranges, dtype=np.float32)
            
            # inf値を最大値に、nan値を0に置換
            scan_ranges = np.where(np.isinf(scan_ranges), msg.range_max, scan_ranges)
            scan_ranges = np.where(np.isnan(scan_ranges), 0.0, scan_ranges)
            
            # シーケンスにscan_rangesを追加
            self.scan_sequence.append(scan_ranges)
            
            # 必要なデータ数を計算（strideを考慮）
            required_length = (self.scan_history_length - 1) * self.scan_history_stride + 1
            
            # 必要な数が溜まるまで待つ
            if len(self.scan_sequence) < required_length:
                return
            
            # 初回のみログ出力
            if len(self.scan_sequence) == required_length:
                self.get_logger().info(f'Ready for inference (scan: {required_length})')
            
            # 必要な数を超えたら古いものを削除
            while len(self.scan_sequence) > required_length:
                self.scan_sequence.pop(0)
            
            # スキャンデータをstrideを考慮して取得
            scan_indices = [i * self.scan_history_stride for i in range(self.scan_history_length)]
            inference_scan = np.stack(
                [self.scan_sequence[i] for i in scan_indices], axis=0
            )  # shape: (scan_history_length, scan_size)

            # 推論実行
            prediction = self.model.inference_next_step_only(inference_scan)

            # self.get_logger().info(f'DNN output is {prediction[0]:.4f}, {prediction[1]:.4f}')
            
            # 予測結果を取得 [throttle, angle]
            throttle = prediction[0]
            angle = prediction[1]
            
            # TwistStampedメッセージの作成
            pwm_msg = TwistStamped()
            pwm_msg.header.stamp = self.get_clock().now().to_msg()
            pwm_msg.header.frame_id = "base_link"
            
            # throttle
            pwm_msg.twist.linear.x = float(throttle)
            pwm_msg.twist.linear.y = 0.0
            pwm_msg.twist.linear.z = 0.0
            
            # angle
            pwm_msg.twist.angular.x = 0.0
            pwm_msg.twist.angular.y = 0.0
            pwm_msg.twist.angular.z = float(angle)
            
            # パブリッシュ
            self.pwm_pub.publish(pwm_msg)
            
        except Exception as e:
            self.get_logger().error(f'Error in scan_callback: {str(e)}')


def main(args=None):
    rclpy.init(args=args)
    try:
        node = NNControllerNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'Error: {str(e)}')
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()