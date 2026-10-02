#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy
from std_msgs.msg import String
import subprocess
import os
import shutil
import json
from datetime import datetime
from pathlib import Path
from enum import Enum


class RecordState(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"


class BagRecorderNode(Node):
    def __init__(self):
        super().__init__('bag_recorder_node')
        
        # パラメータの宣言
        self.declare_parameter('record_all_topics', True)
        self.declare_parameter('topic_list', [''])
        self.declare_parameter('save_directory', '~/rosbag/')
        self.declare_parameter('disk_space_warning_threshold_gb', 10.0)
        self.declare_parameter('disk_space_critical_threshold_gb', 3.0)
        self.declare_parameter('button_square', 0)
        self.declare_parameter('button_cross', 1)
        self.declare_parameter('button_circle', 2)
        self.declare_parameter('button_triangle', 3)
        
        # パラメータの取得
        self.record_all_topics = self.get_parameter('record_all_topics').value
        self.topic_list = self.get_parameter('topic_list').value
        self.save_directory = os.path.expanduser(
            self.get_parameter('save_directory').value)
        self.disk_warning_threshold = self.get_parameter(
            'disk_space_warning_threshold_gb').value
        self.disk_critical_threshold = self.get_parameter(
            'disk_space_critical_threshold_gb').value
        
        # ボタンインデックス
        self.btn_square = self.get_parameter('button_square').value
        self.btn_cross = self.get_parameter('button_cross').value
        self.btn_circle = self.get_parameter('button_circle').value
        self.btn_triangle = self.get_parameter('button_triangle').value
        
        # 保存ディレクトリの作成
        os.makedirs(self.save_directory, exist_ok=True)
        
        # 状態管理
        self.state = RecordState.IDLE
        self.recording_process = None
        self.current_bag_path = None
        self.bag_counter = 0
        self.recording_start_time = None
        
        # 起動ロック（triangleを3回押すまでシステムが動作しない）
        self.system_unlocked = False
        self.triangle_press_count = 0
        
        # ボタン状態（チャタリング防止）
        self.prev_buttons = []
        
        # Publisher
        self.status_pub = self.create_publisher(
            String, '/bag_recorder/status', 10)
        self.event_pub = self.create_publisher(
            String, '/bag_recorder/event', 10)
        
        # Subscriber
        self.joy_sub = self.create_subscription(
            Joy, '/joy', self.joy_callback, 10)
        
        # ステータスの定期publish（1Hz）
        self.status_timer = self.create_timer(1.0, self.publish_status)
        
        self.get_logger().info('Bag Recorder Node started')
        self.get_logger().info('Press Triangle button 3 times to unlock the system')
        self.publish_event('SYSTEM_LOCKED', 
                          'Press Triangle 3 times to unlock')
    
    def joy_callback(self, msg):
        # ボタン配列のサイズチェック
        if len(msg.buttons) <= max(self.btn_square, self.btn_cross, 
                                    self.btn_circle, self.btn_triangle):
            return
        
        # 前回のボタン状態が初期化されていない場合
        if len(self.prev_buttons) != len(msg.buttons):
            self.prev_buttons = [0] * len(msg.buttons)
            return
        
        # Triangleボタンでシステムアンロック（3回押す必要がある）
        if not self.system_unlocked:
            if self.is_button_pressed(msg, self.btn_triangle):
                self.triangle_press_count += 1
                self.get_logger().info(
                    f'Triangle pressed: {self.triangle_press_count}/3')
                self.publish_event('UNLOCK_PROGRESS', 
                                  f'{self.triangle_press_count}/3')
                
                if self.triangle_press_count >= 3:
                    self.system_unlocked = True
                    self.get_logger().info('System unlocked!')
                    self.publish_event('SYSTEM_UNLOCKED', 
                                      'Bag recorder is now active')
            
            self.prev_buttons = list(msg.buttons)
            return
        
        # Square: 記録開始
        if self.is_button_pressed(msg, self.btn_square):
            if self.state == RecordState.IDLE:
                self.start_recording()
        
        # Circle: 記録停止して保存
        elif self.is_button_pressed(msg, self.btn_circle):
            if self.state == RecordState.RECORDING:
                self.stop_and_save_recording()
        
        # Cross: 記録破棄
        elif self.is_button_pressed(msg, self.btn_cross):
            if self.state == RecordState.RECORDING:
                self.discard_recording()
        
        # 前回のボタン状態を更新
        self.prev_buttons = list(msg.buttons)
    
    def is_button_pressed(self, msg, button_index):
        """ボタンの立ち上がりエッジを検出（0→1）"""
        return (msg.buttons[button_index] == 1 and 
                self.prev_buttons[button_index] == 0)
    
    def check_disk_space(self):
        """ディスク空き容量をチェック（GB単位で返す）"""
        stat = shutil.disk_usage(self.save_directory)
        free_gb = stat.free / (1024**3)
        return free_gb
    
    def start_recording(self):
        """記録を開始"""
        # ディスク容量チェック
        free_space = self.check_disk_space()
        
        if free_space < self.disk_critical_threshold:
            self.get_logger().error(
                f'Insufficient disk space: {free_space:.2f} GB available')
            self.publish_event('DISK_SPACE_CRITICAL', 
                              f'Cannot start recording: {free_space:.2f} GB available')
            return
        
        if free_space < self.disk_warning_threshold:
            self.get_logger().warn(
                f'Low disk space warning: {free_space:.2f} GB available')
            self.publish_event('DISK_SPACE_WARNING', 
                              f'{free_space:.2f} GB remaining')
        
        # カウンターをインクリメント
        self.bag_counter += 1
        
        # bagファイル名の生成
        timestamp = datetime.now().strftime('%Y_%m_%d-%H_%M_%S')
        bag_name = f'rosbag2_{timestamp}_{self.bag_counter}'
        self.current_bag_path = os.path.join(self.save_directory, bag_name)
        
        # ros2 bag recordコマンドの構築
        cmd = ['ros2', 'bag', 'record', '-o', self.current_bag_path]
        
        if self.record_all_topics:
            cmd.append('-a')
        else:
            if not self.topic_list:
                self.get_logger().error('No topics specified for recording')
                self.publish_event('ERROR', 'No topics specified')
                return
            cmd.extend(self.topic_list)
        
        # 記録プロセスの開始
        try:
            self.recording_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.state = RecordState.RECORDING
            self.recording_start_time = datetime.now()
            
            self.get_logger().info(f'Recording started: {bag_name}')
            self.publish_event('RECORDING_STARTED', bag_name)
            
        except Exception as e:
            self.get_logger().error(f'Failed to start recording: {str(e)}')
            self.publish_event('ERROR', f'Failed to start recording: {str(e)}')
    
    def stop_and_save_recording(self):
        """記録を停止して保存"""
        if self.recording_process is None:
            return
        
        try:
            # プロセスを終了
            self.recording_process.terminate()
            self.recording_process.wait(timeout=5.0)
            
            self.get_logger().info(f'Recording saved: {self.current_bag_path}')
            self.publish_event('RECORDING_SAVED', 
                              os.path.basename(self.current_bag_path))
            
        except subprocess.TimeoutExpired:
            self.get_logger().warn('Recording process did not terminate, killing...')
            self.recording_process.kill()
            self.recording_process.wait()
        
        except Exception as e:
            self.get_logger().error(f'Error stopping recording: {str(e)}')
        
        finally:
            self.recording_process = None
            self.current_bag_path = None
            self.recording_start_time = None
            self.state = RecordState.IDLE
    
    def discard_recording(self):
        """記録を破棄"""
        if self.recording_process is None:
            return
        
        bag_path_to_delete = self.current_bag_path
        
        try:
            # プロセスを終了
            self.recording_process.terminate()
            self.recording_process.wait(timeout=5.0)
            
        except subprocess.TimeoutExpired:
            self.recording_process.kill()
            self.recording_process.wait()
        
        except Exception as e:
            self.get_logger().error(f'Error stopping recording: {str(e)}')
        
        finally:
            self.recording_process = None
            self.state = RecordState.IDLE
            self.recording_start_time = None
        
        # bagファイルを削除
        if bag_path_to_delete and os.path.exists(bag_path_to_delete):
            try:
                shutil.rmtree(bag_path_to_delete)
                self.get_logger().info(f'Recording discarded: {bag_path_to_delete}')
                self.publish_event('RECORDING_DISCARDED', 
                                  os.path.basename(bag_path_to_delete))
            except Exception as e:
                self.get_logger().error(f'Error deleting bag: {str(e)}')
        
        self.current_bag_path = None
    
    def publish_status(self):
        """ステータスを定期的にpublish"""
        status_dict = {
            'state': self.state.value,
            'system_unlocked': self.system_unlocked,
            'current_bag': os.path.basename(self.current_bag_path) 
                          if self.current_bag_path else None,
            'recording_start_time': self.recording_start_time.isoformat() 
                                   if self.recording_start_time else None,
            'bag_counter': self.bag_counter
        }
        
        msg = String()
        msg.data = json.dumps(status_dict)
        self.status_pub.publish(msg)
    
    def publish_event(self, event_type, message):
        """イベントをpublish"""
        event_dict = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'message': message
        }
        
        msg = String()
        msg.data = json.dumps(event_dict)
        self.event_pub.publish(msg)
    
    def destroy_node(self):
        """ノード終了時の処理"""
        if self.state == RecordState.RECORDING:
            self.get_logger().info('Node shutting down, saving current recording...')
            self.stop_and_save_recording()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = BagRecorderNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()