#!/usr/bin/env python3
"""
IMU 実験用の自動走行 launch（launch/autonomous_driving.py は変更しない）

推論ノード以外は autonomous_driving.py と同じ構成。あちらを変えたら、こちらも合わせること。

  ros2 launch launch/autonomous_driving_imu.py              # IMU モデル（既定）
  ros2 launch launch/autonomous_driving_imu.py model:=lidar # 10/18 のデータで学習し直した LiDAR モデル
  ros2 launch launch/autonomous_driving_imu.py imu_model_path:=/path/to/model.pth

現行モデル（/home/jetson/models/my_model.pth）に戻すときは、これまでどおり make run を使う。
"""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node

MODEL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'model', 'transformer_imu')
)
# 比較に使うモデル（Stage 2 の 3 seed のうち、検証 loss が最も低いもの。model/transformer_imu/experiments.md）
DEFAULT_IMU_MODEL = os.path.join(MODEL_DIR, 'imu_20261018_gz_len20_str10_s2_seed2.pth')
DEFAULT_LIDAR_MODEL = os.path.join(MODEL_DIR, 'lidar_20261018_len20_str10_s2_seed1.pth')


def _model_is(name):
    return IfCondition(PythonExpression(["'", LaunchConfiguration('model'), f"' == '{name}'"]))


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('model', default_value='imu', description='imu または lidar'),
        DeclareLaunchArgument('imu_model_path', default_value=DEFAULT_IMU_MODEL),
        DeclareLaunchArgument('lidar_model_path', default_value=DEFAULT_LIDAR_MODEL),

        # joy_muxを起動（複数ジョイスティック対応、/joy0 /joy1 -> /joy に集約）
        ExecuteProcess(
            cmd=['ros2', 'launch', 'joy_mux', 'joy_mux.launch.py'],
            output='screen'
        ),

        # urg_node2を起動
        ExecuteProcess(
            cmd=['ros2', 'launch', 'urg_node2', 'urg_node2.launch.py'],
            output='screen'
        ),

        # bno085_imuを起動（base_link -> imu_link の静的TFも含む）
        ExecuteProcess(
            cmd=['ros2', 'launch', 'bno085_imu', 'bno085_imu.launch.py'],
            output='screen'
        ),

        Node(
            package='mux_pwm',
            executable='pwm_mux_node',
            name='pwm_mux_node',
            output='screen',
            parameters=[{
                'speed_sensitivity': 0.5,  # 直進感度の倍率（<1.0で鈍く、>1.0で敏感）
                'speed_expo': 1.0,  # 直進カーブ指数（1.0=線形, >1.0で低速域が細かく操作可能）
            }]
        ),

        # IMU モデル（model:=imu）
        Node(
            package='pytorch_pwm_controller',
            executable='nn_imu_pwm_controller_node',
            name='nn_imu_pwm_controller_node',
            output='screen',
            condition=_model_is('imu'),
            parameters=[{
                'model_path': LaunchConfiguration('imu_model_path'),
                # 履歴長・stride はチェックポイントから読む
            }]
        ),

        # 学習し直した LiDAR モデル（model:=lidar）。ノードは現行と同じもの
        Node(
            package='pytorch_pwm_controller',
            executable='nn_pwm_controller_node',
            name='nn_pwm_controller_node',
            output='screen',
            condition=_model_is('lidar'),
            parameters=[{
                'model_path': LaunchConfiguration('lidar_model_path'),
                'scan_history_length': 20,
                'scan_history_stride': 10,
            }]
        ),

        Node(
            package='pwm_controller',
            executable='pwm_pca9685_controller',
            name='pwm_pca9685_controller',
            output='screen'
        ),

        # bag_recorderを起動
        ExecuteProcess(
            cmd=['ros2', 'launch', 'bag_recorder', 'bag_recorder.launch.py'],
            output='screen'
        ),

        # m5stack_visualizerを起動
        ExecuteProcess(
            cmd=['ros2', 'launch', 'm5stack_visualizer', 'm5stack_visualizer.launch.py'],
            output='screen'
        ),
    ])
