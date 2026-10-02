from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
import os
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('joy_mux')
    default_params_file = os.path.join(pkg_dir, 'config', 'joy_mux_params.yaml')

    params_file_arg = DeclareLaunchArgument(
        'params_file',
        default_value=default_params_file,
        description='Path to the parameter file'
    )

    # js0 向け joy_node
    joy_node_0 = Node(
        package='joy',
        executable='joy_node',
        name='joy_node_0',
        parameters=[{'device_id': 0}],
        remappings=[('joy', '/joy0')],
    )

    # js1 向け joy_node
    joy_node_1 = Node(
        package='joy',
        executable='joy_node',
        name='joy_node_1',
        parameters=[{'device_id': 1}],
        remappings=[('joy', '/joy1')],
    )

    joy_mux_node = Node(
        package='joy_mux',
        executable='joy_mux_node',
        name='joy_mux_node',
        output='screen',
        parameters=[LaunchConfiguration('params_file')],
    )

    return LaunchDescription([
        params_file_arg,
        joy_node_0,
        joy_node_1,
        joy_mux_node,
    ])
