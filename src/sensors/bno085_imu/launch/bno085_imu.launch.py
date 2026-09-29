from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
import os
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    # パッケージのディレクトリを取得
    pkg_dir = get_package_share_directory('bno085_imu')

    # デフォルトのパラメータファイルパス
    default_params_file = os.path.join(pkg_dir, 'config', 'bno085_imu_params.yaml')

    # Launch引数の定義（base_link から見た IMU の取り付け位置・姿勢）
    launch_args = [
        DeclareLaunchArgument(
            'params_file',
            default_value=default_params_file,
            description='Path to the parameter file'
        ),
        DeclareLaunchArgument('imu_x', default_value='0.0', description='[m]'),
        DeclareLaunchArgument('imu_y', default_value='0.0', description='[m]'),
        DeclareLaunchArgument('imu_z', default_value='0.0', description='[m]'),
        DeclareLaunchArgument('imu_roll', default_value='0.0', description='[rad]'),
        DeclareLaunchArgument('imu_pitch', default_value='0.0', description='[rad]'),
        DeclareLaunchArgument('imu_yaw', default_value='0.0', description='[rad]'),
    ]

    # ノードの定義
    bno085_imu_node = Node(
        package='bno085_imu',
        executable='bno085_imu_node',
        name='bno085_imu_node',
        output='screen',
        parameters=[LaunchConfiguration('params_file')],
        emulate_tty=True,
    )

    # base_link -> imu_link の静的TF
    imu_tf_node = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='imu_static_tf',
        arguments=[
            '--x', LaunchConfiguration('imu_x'),
            '--y', LaunchConfiguration('imu_y'),
            '--z', LaunchConfiguration('imu_z'),
            '--roll', LaunchConfiguration('imu_roll'),
            '--pitch', LaunchConfiguration('imu_pitch'),
            '--yaw', LaunchConfiguration('imu_yaw'),
            '--frame-id', 'base_link',
            '--child-frame-id', 'imu_link',
        ],
    )

    return LaunchDescription(launch_args + [
        bno085_imu_node,
        imu_tf_node,
    ])
