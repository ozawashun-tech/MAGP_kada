from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    # Launch引数
    serial_port_arg = DeclareLaunchArgument(
        'serial_port',
        default_value='/dev/ttyUSB0',
        description='Serial port for M5Stack'
    )
    
    baud_rate_arg = DeclareLaunchArgument(
        'baud_rate',
        default_value='115200',
        description='Baud rate for serial communication'
    )
    
    # M5Stack Bridgeノード
    m5stack_bridge_node = Node(
        package='m5stack_visualizer',
        executable='m5stack_bridge_node',
        name='m5stack_bridge_node',
        output='screen',
        parameters=[{
            'serial_port': LaunchConfiguration('serial_port'),
            'baud_rate': LaunchConfiguration('baud_rate')
        }],
    )
    
    return LaunchDescription([
        serial_port_arg,
        baud_rate_arg,
        m5stack_bridge_node,
    ])