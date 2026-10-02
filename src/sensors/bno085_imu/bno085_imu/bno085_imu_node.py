#!/usr/bin/env python3

import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu
from adafruit_extended_bus import ExtendedI2C
from adafruit_bno08x import (
    BNO_REPORT_ACCELEROMETER,
    BNO_REPORT_GYROSCOPE,
    BNO_REPORT_GAME_ROTATION_VECTOR,
)
from adafruit_bno08x.i2c import BNO08X_I2C


class Bno085ImuNode(Node):
    """
    BNO085 を I2C で読み、sensor_msgs/Imu を publish する。

    IMU は走行に必須ではないので、接続失敗や通信エラーではノードを落とさず、
    一定間隔で再接続を試みる。PCA9685 とはバスを分けて使う前提。
    """

    def __init__(self):
        super().__init__('bno085_imu_node')

        # パラメータの宣言
        self.declare_parameter('i2c_bus', 1)  # /dev/i2c-1（40ピンの27/28番）
        self.declare_parameter('i2c_address', 0x4A)
        self.declare_parameter('frame_id', 'imu_link')
        self.declare_parameter('topic', '/imu/data')
        self.declare_parameter('rate_hz', 50.0)  # 100Hz は I2C が飽和する
        self.declare_parameter('orientation_stddev', 0.02)  # rad
        self.declare_parameter('angular_velocity_stddev', 0.01)  # rad/s
        self.declare_parameter('linear_acceleration_stddev', 0.1)  # m/s^2
        self.declare_parameter('max_consecutive_errors', 10)
        self.declare_parameter('stale_timeout_sec', 1.0)
        self.declare_parameter('reconnect_interval_sec', 2.0)

        # パラメータの取得
        self.i2c_bus = self.get_parameter('i2c_bus').value
        self.i2c_address = self.get_parameter('i2c_address').value
        self.frame_id = self.get_parameter('frame_id').value
        topic = self.get_parameter('topic').value
        self.rate_hz = self.get_parameter('rate_hz').value
        orientation_var = self.get_parameter('orientation_stddev').value ** 2
        angular_velocity_var = self.get_parameter('angular_velocity_stddev').value ** 2
        linear_acceleration_var = self.get_parameter('linear_acceleration_stddev').value ** 2
        self.max_consecutive_errors = self.get_parameter('max_consecutive_errors').value
        self.stale_timeout_sec = self.get_parameter('stale_timeout_sec').value
        self.reconnect_interval_sec = self.get_parameter('reconnect_interval_sec').value

        self.orientation_cov = self._diag_covariance(orientation_var)
        self.angular_velocity_cov = self._diag_covariance(angular_velocity_var)
        self.linear_acceleration_cov = self._diag_covariance(linear_acceleration_var)

        # State variables
        self.i2c = None
        self.bno = None
        self.consecutive_errors = 0
        self.last_sample = None
        self.last_sample_time = 0.0
        self.last_connect_attempt = 0.0

        # Publisher
        self.imu_pub = self.create_publisher(Imu, topic, 10)

        self._connect()

        # 3種類のレポートは別々のタイミングで届くので、値の変化ではなく一定周期で publish する
        self.timer = self.create_timer(1.0 / self.rate_hz, self.timer_callback)

    @staticmethod
    def _diag_covariance(variance):
        return [variance, 0.0, 0.0,
                0.0, variance, 0.0,
                0.0, 0.0, variance]

    def _connect(self):
        """I2C を開いて BNO085 を初期化し、必要なレポートを有効にする"""
        self.last_connect_attempt = time.monotonic()
        try:
            self.i2c = ExtendedI2C(self.i2c_bus)
            self.bno = BNO08X_I2C(self.i2c, address=self.i2c_address)
            report_interval_us = int(1e6 / self.rate_hz)
            for feature in (BNO_REPORT_GAME_ROTATION_VECTOR,
                            BNO_REPORT_GYROSCOPE,
                            BNO_REPORT_ACCELEROMETER):
                self.bno.enable_feature(feature, report_interval_us)
        except Exception as e:
            self.get_logger().error(
                f'BNO085 の初期化に失敗 (bus={self.i2c_bus}, '
                f'addr=0x{self.i2c_address:02X}): {e}')
            self._disconnect()
            return

        self.consecutive_errors = 0
        self.last_sample = None
        self.last_sample_time = time.monotonic()
        self.get_logger().info(
            f'BNO085 接続完了 (bus={self.i2c_bus}, addr=0x{self.i2c_address:02X}, '
            f'{self.rate_hz:.0f} Hz)')

    def _disconnect(self):
        self.bno = None
        if self.i2c is not None:
            try:
                self.i2c.deinit()
            except Exception:
                pass
        self.i2c = None

    def timer_callback(self):
        if self.bno is None:
            if time.monotonic() - self.last_connect_attempt >= self.reconnect_interval_sec:
                self._connect()
            return

        try:
            quat = self.bno.game_quaternion  # (x, y, z, w)
            gyro = self.bno.gyro
            accel = self.bno.acceleration
        except Exception as e:
            self.consecutive_errors += 1
            if self.consecutive_errors >= self.max_consecutive_errors:
                self.get_logger().error(f'BNO085 の読み取りエラーが続いたため再接続します: {e}')
                self._disconnect()
            return
        self.consecutive_errors = 0

        # ライブラリは最後の値を保持し続けるので、値がまったく変わらない状態が続いたら停止とみなす
        now = time.monotonic()
        sample = (quat, gyro, accel)
        if sample != self.last_sample:
            self.last_sample = sample
            self.last_sample_time = now
        elif now - self.last_sample_time > self.stale_timeout_sec:
            self.get_logger().warn('BNO085 のデータが更新されないため再接続します')
            self._disconnect()
            return

        msg = Imu()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w = quat
        msg.orientation_covariance = self.orientation_cov
        msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z = gyro
        msg.angular_velocity_covariance = self.angular_velocity_cov
        msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z = accel
        msg.linear_acceleration_covariance = self.linear_acceleration_cov
        self.imu_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)

    node = None
    try:
        node = Bno085ImuNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node:
            node._disconnect()
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
