from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
import os
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    # パッケージのディレクトリを取得
    pkg_dir = get_package_share_directory('bag_recorder')
    
    # デフォルトのパラメータファイルパス
    default_params_file = os.path.join(pkg_dir, 'config', 'bag_recorder_params.yaml')
    
    # Launch引数の定義
    params_file_arg = DeclareLaunchArgument(
        'params_file',
        default_value=default_params_file,
        description='Path to the parameter file'
    )
    
    # ノードの定義
    bag_recorder_node = Node(
        package='bag_recorder',
        executable='bag_recorder_node',
        name='bag_recorder_node',
        output='screen',
        parameters=[LaunchConfiguration('params_file')],
        emulate_tty=True,
    )
    
    return LaunchDescription([
        params_file_arg,
        bag_recorder_node,
    ])