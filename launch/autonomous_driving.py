#!/usr/bin/env python3
from launch import LaunchDescription
from launch.actions import ExecuteProcess
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        # joy_nodeを起動
        # Node(
        #     package='joy',
        #     executable='joy_node',
        #     name='joy_node',
        #     parameters=[{
        #         'dev': '/dev/input/js0'
        #     }]),
        
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
        
        # joy_mux_nodeを起動
        Node(
            package='mux_pwm',
            executable='pwm_mux_node',
            name='pwm_mux_node',
            output='screen'
        ),
        
        # pwm_controller_nodeを起動
        Node(
            package='pytorch_pwm_controller',
            executable='nn_pwm_controller_node',
            name='nn_pwm_controller_node',
            output='screen'
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