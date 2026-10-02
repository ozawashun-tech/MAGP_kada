#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import serial
import json
import threading


class M5StackBridgeNode(Node):
    def __init__(self):
        super().__init__('m5stack_bridge_node')
        
        # パラメータ
        self.declare_parameter('serial_port', '/dev/ttyUSB0')
        self.declare_parameter('baud_rate', 115200)
        
        serial_port = self.get_parameter('serial_port').value
        baud_rate = self.get_parameter('baud_rate').value
        
        # シリアルポート接続
        try:
            self.serial_port = serial.Serial(
                serial_port, 
                baud_rate, 
                timeout=1
            )
            self.get_logger().info(f'Connected to {serial_port} at {baud_rate} baud')
        except Exception as e:
            self.get_logger().error(f'Failed to open serial port: {str(e)}')
            self.serial_port = None
        
        # Subscriber
        self.status_sub = self.create_subscription(
            String,
            '/bag_recorder/status',
            self.status_callback,
            10
        )
        
        self.event_sub = self.create_subscription(
            String,
            '/bag_recorder/event',
            self.event_callback,
            10
        )
        
        self.mux_sub = self.create_subscription(
            String,
            '/mux_pwm/status',
            self.mux_callback,
            10
        )
        
        self.get_logger().info('M5Stack Bridge Node started')
    
    def status_callback(self, msg):
        """Bag Recorder Statusを転送"""
        if self.serial_port and self.serial_port.is_open:
            try:
                # JSONをそのまま転送
                self.serial_port.write((msg.data + '\n').encode())
                self.get_logger().debug('Sent status to M5Stack')
            except Exception as e:
                self.get_logger().error(f'Serial write error: {str(e)}')
    
    def event_callback(self, msg):
        """Bag Recorder Eventを転送"""
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write((msg.data + '\n').encode())
                self.get_logger().debug('Sent event to M5Stack')
            except Exception as e:
                self.get_logger().error(f'Serial write error: {str(e)}')
    
    def mux_callback(self, msg):
        """Mux Input Statusを転送"""
        if self.serial_port and self.serial_port.is_open:
            try:
                # JSON形式に変換
                try:
                    # すでにJSONの場合
                    json.loads(msg.data)
                    json_str = msg.data
                except:
                    # テキストの場合、JSON化
                    json_dict = {'mode': msg.data}
                    json_str = json.dumps(json_dict)
                
                self.serial_port.write((json_str + '\n').encode())
                self.get_logger().debug('Sent mux status to M5Stack')
            except Exception as e:
                self.get_logger().error(f'Serial write error: {str(e)}')
    
    def destroy_node(self):
        """ノード終了時の処理"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.get_logger().info('Serial port closed')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = M5StackBridgeNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()