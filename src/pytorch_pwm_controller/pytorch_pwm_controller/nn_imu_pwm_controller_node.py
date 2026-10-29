#!/usr/bin/env python3
import json

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu, LaserScan
from geometry_msgs.msg import TwistStamped
from std_msgs.msg import String
import numpy as np
from .model.model_imu import ModelIMU

IMU_FALLBACKS = ('mean', 'neutral')


class NNIMUControllerNode(Node):
    """
    LiDAR + IMU（ヨーレート）のモデルで /torch_pwm を出すノード

    IMU の特徴量は学習時（training_src/dataset_imu.py の attach_imu_features）と同じく、
    「直前の scan から今の scan までに届いた IMU の平均（届いていなければ直前の値）」を scan ごとに作る。

    IMU が stale_timeout_sec 以上届かないとき、または使う履歴に IMU が止まっていたフレームが残っているときは、
    imu_fallback で決めた動作をする。
      mean:    IMU の履歴全体を平均値（正規化後の 0）にして推論を続ける。学習時の modality dropout と同じ入力。
               学習で dropout を使っていないモデル（imu_dropout_p=0）では使えず、neutral になる。
      neutral: 推論をやめて中立の PWM を出す。pwm_mux_node は最後に受け取った /torch_pwm を出し続けるため、
               publish を止めるだけでは車が止まらない。
    """

    def __init__(self):
        super().__init__('nn_imu_controller_node')

        self.declare_parameter('model_path', '')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('imu_topic', '/imu/data')
        self.declare_parameter('pwm_topic', '/torch_pwm')
        self.declare_parameter('mode_topic', '/mux_pwm/status')
        self.declare_parameter('stale_timeout_sec', 1.0)
        self.declare_parameter('imu_fallback', 'mean')
        self.declare_parameter('reset_history_on_auto', True)
        # 出力と IMU の状態をログに出す周期（0 以下で出さない）
        self.declare_parameter('status_log_period', 2.0)
        # neutral のときに出す値（pwm_mux_node / pwm_pca9685_controller と同じ値にする）
        self.declare_parameter('motor_neutral', 1620)
        self.declare_parameter('steering_center', 1265)

        model_path = self.get_parameter('model_path').value
        scan_topic = self.get_parameter('scan_topic').value
        imu_topic = self.get_parameter('imu_topic').value
        pwm_topic = self.get_parameter('pwm_topic').value
        mode_topic = self.get_parameter('mode_topic').value
        self.stale_timeout_ns = int(self.get_parameter('stale_timeout_sec').value * 1e9)
        self.imu_fallback = self.get_parameter('imu_fallback').value
        self.reset_history_on_auto = self.get_parameter('reset_history_on_auto').value
        status_log_period = float(self.get_parameter('status_log_period').value)
        self.motor_neutral = float(self.get_parameter('motor_neutral').value)
        self.steering_center = float(self.get_parameter('steering_center').value)

        if not model_path:
            raise ValueError('model_path を指定してください（IMU モデルに同梱の既定値はありません）')
        if self.imu_fallback not in IMU_FALLBACKS:
            raise ValueError(f'imu_fallback は {IMU_FALLBACKS} のどれかにしてください: {self.imu_fallback}')

        self.get_logger().info(f'Loading IMU model from: {model_path}')
        try:
            self.model = ModelIMU(model_path)
        except Exception as e:
            self.get_logger().error(f'Failed to load model: {str(e)}')
            raise

        if self.imu_fallback == 'mean' and self.model.imu_dropout_p <= 0:
            self.get_logger().warn(
                'このモデルは IMU を平均値にした入力を学習していない（imu_dropout_p=0）ため、'
                'imu_fallback を neutral にします'
            )
            self.imu_fallback = 'neutral'

        # 履歴長と特徴量はチェックポイントから読む（launch との設定の取り違えを防ぐ）
        self.scan_history_length = self.model.scan_history_length
        self.scan_history_stride = self.model.scan_history_stride
        self.imu_features = self.model.imu_features
        self.required_length = (self.scan_history_length - 1) * self.scan_history_stride + 1
        self.get_logger().info(
            f'Scan history: length={self.scan_history_length}, stride={self.scan_history_stride}, '
            f'IMU features: {self.imu_features}'
        )
        self.get_logger().info(
            f'IMU fallback: {self.imu_fallback} (imu_dropout_p={self.model.imu_dropout_p})'
        )

        # scan ごとの履歴（scan, IMU 特徴量, IMU が有効か）
        self.scan_sequence = []
        self.imu_sequence = []
        self.imu_valid_sequence = []

        # 前の scan 以降に届いた IMU の値
        self.pending_imu = []
        self.last_imu_value = None
        self.last_imu_time_ns = None
        # IMU の使い方（ok / mean / neutral）。変わったときだけログを出す
        self.imu_state = None

        # Auto に切り替わったら、次のスキャンで履歴を埋め直す
        self.auto_mode = False
        self.reset_history = False
        self.ready_logged = False

        # 状態ログ用（前回のログからの件数と、最後に出した値）
        self.scan_count = 0
        self.inference_count = 0
        self.last_output = None

        self.scan_sub = self.create_subscription(LaserScan, scan_topic, self.scan_callback, 10)
        self.imu_sub = self.create_subscription(Imu, imu_topic, self.imu_callback, 50)
        # 手動中に車を運ぶと、そのスキャンが Auto 再開後も約 5 秒間履歴に残るため、モードの切り替えを見る
        self.mode_sub = self.create_subscription(String, mode_topic, self.mode_callback, 10)
        self.pwm_pub = self.create_publisher(TwistStamped, pwm_topic, 10)

        self.status_log_period = status_log_period
        if status_log_period > 0:
            self.create_timer(status_log_period, self.log_status)

        self.get_logger().info(f'Subscribing to: {scan_topic}, {imu_topic}')
        self.get_logger().info(f'Publishing to: {pwm_topic}')
        self.get_logger().info(f'Reset history on Auto: {self.reset_history_on_auto} ({mode_topic})')

    def imu_callback(self, msg: Imu):
        values = []
        for feature in self.imu_features:
            value = msg
            for attr in feature.split('.'):
                value = getattr(value, attr)
            values.append(float(value))
        # 学習データの timestamp は bag の受信時刻なので、ここでも受信時刻を使う
        self.pending_imu.append(values)
        self.last_imu_value = values
        self.last_imu_time_ns = self.get_clock().now().nanoseconds

    def mode_callback(self, msg: String):
        """pwm_mux_node のモード（{"mode": "Auto" | "Manual", ...}）を受け取る"""
        try:
            auto_mode = json.loads(msg.data).get('mode') == 'Auto'
        except (json.JSONDecodeError, AttributeError):
            self.get_logger().warn(f'Unknown mode message: {msg.data}')
            return

        if auto_mode and not self.auto_mode and self.reset_history_on_auto:
            self.reset_history = True
        self.auto_mode = auto_mode

    def log_status(self):
        """出力と IMU の状態を定期的にログに出す（Auto にしても動かないときの切り分け用）"""
        now_ns = self.get_clock().now().nanoseconds
        if self.last_imu_time_ns is None:
            imu_age = 'IMU 未受信'
        else:
            imu_age = f'IMU {(now_ns - self.last_imu_time_ns) / 1e9:.2f}s 前'

        if self.scan_count == 0:
            progress = '/scan 未受信'
        elif len(self.scan_sequence) < self.required_length:
            progress = f'履歴 {len(self.scan_sequence)}/{self.required_length}'
        else:
            progress = f'推論 {self.inference_count / self.status_log_period:.1f} Hz'

        output = '出力なし'
        if self.last_output is not None:
            output = f'thr={self.last_output[0]:.0f} str={self.last_output[1]:.0f}'

        self.get_logger().info(
            f'[status] {"Auto" if self.auto_mode else "Manual"} | {progress} | '
            f'IMU: {self.imu_state or "-"} ({imu_age}) | {output}'
        )
        self.scan_count = 0
        self.inference_count = 0

    def _imu_feature_for_scan(self, now_ns):
        """今の scan に付ける IMU 特徴量と、それが有効かを返す"""
        if self.last_imu_time_ns is None or now_ns - self.last_imu_time_ns > self.stale_timeout_ns:
            self.pending_imu.clear()
            return np.zeros(len(self.imu_features), dtype=np.float32), False

        if self.pending_imu:
            feature = np.mean(np.array(self.pending_imu, dtype=np.float32), axis=0)
            self.pending_imu.clear()
        else:
            feature = np.array(self.last_imu_value, dtype=np.float32)
        return feature, True

    def _set_imu_state(self, state, reason=''):
        if state == self.imu_state:
            return
        self.imu_state = state
        if state == 'ok':
            self.get_logger().info('IMU OK: IMU を使って推論します')
        elif state == 'mean':
            self.get_logger().warn(
                f'IMU が使えないため、IMU を平均値にして推論します（学習時の dropout と同じ入力。{reason}）'
            )
        else:
            self.get_logger().warn(f'IMU が使えないため、中立の PWM を出します（{reason}）')

    def _publish(self, throttle, angle):
        pwm_msg = TwistStamped()
        pwm_msg.header.stamp = self.get_clock().now().to_msg()
        pwm_msg.header.frame_id = 'base_link'
        pwm_msg.twist.linear.x = float(throttle)
        pwm_msg.twist.angular.z = float(angle)
        self.pwm_pub.publish(pwm_msg)
        self.last_output = (float(throttle), float(angle))

    def scan_callback(self, msg: LaserScan):
        try:
            now_ns = self.get_clock().now().nanoseconds
            self.scan_count += 1

            # inf値を最大値に、nan値を0に置換（nn_pwm_controller_node と同じ）
            scan_ranges = np.array(msg.ranges, dtype=np.float32)
            scan_ranges = np.where(np.isinf(scan_ranges), msg.range_max, scan_ranges)
            scan_ranges = np.where(np.isnan(scan_ranges), 0.0, scan_ranges)

            imu_feature, imu_valid = self._imu_feature_for_scan(now_ns)

            # Auto に切り替わった直後: 手動中の履歴を捨て、最新の scan と IMU 1 フレームで埋める
            # （学習でも bag の先頭は、足りない履歴を最初のフレームで埋めている）
            if self.reset_history:
                self.reset_history = False
                self.scan_sequence = [scan_ranges] * self.required_length
                self.imu_sequence = [imu_feature] * self.required_length
                self.imu_valid_sequence = [imu_valid] * self.required_length
                self.get_logger().info('Switched to Auto: scan/IMU history reset to the latest frame')
            else:
                self.scan_sequence.append(scan_ranges)
                self.imu_sequence.append(imu_feature)
                self.imu_valid_sequence.append(imu_valid)
                while len(self.scan_sequence) > self.required_length:
                    self.scan_sequence.pop(0)
                    self.imu_sequence.pop(0)
                    self.imu_valid_sequence.pop(0)

            if len(self.scan_sequence) < self.required_length:
                return
            if not self.ready_logged:
                self.ready_logged = True
                self.get_logger().info(f'Ready for inference (scan: {self.required_length})')

            indices = [i * self.scan_history_stride for i in range(self.scan_history_length)]
            inference_scan = np.stack([self.scan_sequence[i] for i in indices], axis=0)

            # 使う履歴に IMU が止まっていたフレームが 1 つでもあれば、IMU を使わない
            if not imu_valid:
                reason = f'{self.stale_timeout_ns / 1e9:.1f} 秒以上届いていない'
            elif not all(self.imu_valid_sequence[i] for i in indices):
                reason = '履歴に IMU が止まっていた区間が残っている'
            else:
                reason = None

            if reason is None:
                self._set_imu_state('ok')
                inference_imu = np.stack([self.imu_sequence[i] for i in indices], axis=0)
            elif self.imu_fallback == 'mean':
                self._set_imu_state('mean', reason)
                inference_imu = None
            else:
                self._set_imu_state('neutral', reason)
                self._publish(self.motor_neutral, self.steering_center)
                return

            throttle, angle = self.model.inference_next_step_only(inference_scan, inference_imu)
            self.inference_count += 1
            self._publish(throttle, angle)

        except Exception as e:
            self.get_logger().error(f'Error in scan_callback: {str(e)}')
            self._publish(self.motor_neutral, self.steering_center)


def main(args=None):
    rclpy.init(args=args)
    try:
        node = NNIMUControllerNode()
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
