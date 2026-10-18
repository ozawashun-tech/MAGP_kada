#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu, LaserScan
from geometry_msgs.msg import TwistStamped
import numpy as np
from .model.model_imu import ModelIMU


class NNIMUControllerNode(Node):
    """
    LiDAR + IMU（ヨーレート）のモデルで /torch_pwm を出すノード

    IMU の特徴量は学習時（training_src/dataset_imu.py の attach_imu_features）と同じく、
    「直前の scan から今の scan までに届いた IMU の平均（届いていなければ直前の値）」を scan ごとに作る。

    IMU が stale_timeout_sec 以上届かないときは、推論をやめて中立の PWM を出す。
    pwm_mux_node は最後に受け取った /torch_pwm を出し続けるため、publish を止めるだけでは車が止まらない。
    """

    def __init__(self):
        super().__init__('nn_imu_controller_node')

        self.declare_parameter('model_path', '')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('imu_topic', '/imu/data')
        self.declare_parameter('pwm_topic', '/torch_pwm')
        self.declare_parameter('stale_timeout_sec', 1.0)
        # IMU が止まったときに出す値（pwm_mux_node / pwm_pca9685_controller と同じ値にする）
        self.declare_parameter('motor_neutral', 1620)
        self.declare_parameter('steering_center', 1265)

        model_path = self.get_parameter('model_path').value
        scan_topic = self.get_parameter('scan_topic').value
        imu_topic = self.get_parameter('imu_topic').value
        pwm_topic = self.get_parameter('pwm_topic').value
        self.stale_timeout_ns = int(self.get_parameter('stale_timeout_sec').value * 1e9)
        self.motor_neutral = float(self.get_parameter('motor_neutral').value)
        self.steering_center = float(self.get_parameter('steering_center').value)

        if not model_path:
            raise ValueError('model_path を指定してください（IMU モデルに同梱の既定値はありません）')

        self.get_logger().info(f'Loading IMU model from: {model_path}')
        try:
            self.model = ModelIMU(model_path)
        except Exception as e:
            self.get_logger().error(f'Failed to load model: {str(e)}')
            raise

        # 履歴長と特徴量はチェックポイントから読む（launch との設定の取り違えを防ぐ）
        self.scan_history_length = self.model.scan_history_length
        self.scan_history_stride = self.model.scan_history_stride
        self.imu_features = self.model.imu_features
        self.required_length = (self.scan_history_length - 1) * self.scan_history_stride + 1
        self.get_logger().info(
            f'Scan history: length={self.scan_history_length}, stride={self.scan_history_stride}, '
            f'IMU features: {self.imu_features}'
        )

        # scan ごとの履歴（scan, IMU 特徴量, IMU が有効か）
        self.scan_sequence = []
        self.imu_sequence = []
        self.imu_valid_sequence = []

        # 前の scan 以降に届いた IMU の値
        self.pending_imu = []
        self.last_imu_value = None
        self.last_imu_time_ns = None
        self.imu_ok = None  # 状態が変わったときだけログを出すため

        self.scan_sub = self.create_subscription(LaserScan, scan_topic, self.scan_callback, 10)
        self.imu_sub = self.create_subscription(Imu, imu_topic, self.imu_callback, 50)
        self.pwm_pub = self.create_publisher(TwistStamped, pwm_topic, 10)

        self.get_logger().info(f'Subscribing to: {scan_topic}, {imu_topic}')
        self.get_logger().info(f'Publishing to: {pwm_topic}')

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

    def _set_imu_state(self, ok, reason=''):
        if ok == self.imu_ok:
            return
        self.imu_ok = ok
        if ok:
            self.get_logger().info('IMU OK: 推論を再開します')
        else:
            self.get_logger().warn(f'IMU が使えないため、中立の PWM を出します（{reason}）')

    def _publish(self, throttle, angle):
        pwm_msg = TwistStamped()
        pwm_msg.header.stamp = self.get_clock().now().to_msg()
        pwm_msg.header.frame_id = 'base_link'
        pwm_msg.twist.linear.x = float(throttle)
        pwm_msg.twist.angular.z = float(angle)
        self.pwm_pub.publish(pwm_msg)

    def scan_callback(self, msg: LaserScan):
        try:
            now_ns = self.get_clock().now().nanoseconds

            # inf値を最大値に、nan値を0に置換（nn_pwm_controller_node と同じ）
            scan_ranges = np.array(msg.ranges, dtype=np.float32)
            scan_ranges = np.where(np.isinf(scan_ranges), msg.range_max, scan_ranges)
            scan_ranges = np.where(np.isnan(scan_ranges), 0.0, scan_ranges)

            imu_feature, imu_valid = self._imu_feature_for_scan(now_ns)

            self.scan_sequence.append(scan_ranges)
            self.imu_sequence.append(imu_feature)
            self.imu_valid_sequence.append(imu_valid)
            while len(self.scan_sequence) > self.required_length:
                self.scan_sequence.pop(0)
                self.imu_sequence.pop(0)
                self.imu_valid_sequence.pop(0)

            if len(self.scan_sequence) < self.required_length:
                return
            if len(self.scan_sequence) == self.required_length and self.imu_ok is None:
                self.get_logger().info(f'Ready for inference (scan: {self.required_length})')

            indices = [i * self.scan_history_stride for i in range(self.scan_history_length)]

            # 使う履歴に IMU が止まっていたフレームが 1 つでもあれば、推論しない
            if not imu_valid:
                self._set_imu_state(False, f'{self.stale_timeout_ns / 1e9:.1f} 秒以上届いていない')
                self._publish(self.motor_neutral, self.steering_center)
                return
            if not all(self.imu_valid_sequence[i] for i in indices):
                self._set_imu_state(False, '履歴に IMU が止まっていた区間が残っている')
                self._publish(self.motor_neutral, self.steering_center)
                return
            self._set_imu_state(True)

            inference_scan = np.stack([self.scan_sequence[i] for i in indices], axis=0)
            inference_imu = np.stack([self.imu_sequence[i] for i in indices], axis=0)
            throttle, angle = self.model.inference_next_step_only(inference_scan, inference_imu)
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
