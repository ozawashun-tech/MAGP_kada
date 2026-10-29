#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy
from geometry_msgs.msg import TwistStamped
from std_msgs.msg import String, Float32
import json


class PwmMuxNode(Node):
    def __init__(self):
        super().__init__('pwm_mux_node')
        
        # パラメータの宣言
        self.declare_parameter('motor_neutral', 1620)
        self.declare_parameter('motor_min', 1250)
        self.declare_parameter('motor_max', 2700)
        self.declare_parameter('steering_center', 1265) #1500
        self.declare_parameter('steering_left', 950)
        self.declare_parameter('steering_right', 1740)
        self.declare_parameter('speed_deadzone', 0.05)
        self.declare_parameter('steering_deadzone', 0.05)
        self.declare_parameter('speed_sensitivity', 1.0)  # 直進感度（スティック入力の倍率）
        self.declare_parameter('speed_expo', 1.0)  # 直進カーブ指数（1.0=線形, >1で中央付近が穏やか）
        self.declare_parameter('manual_gain', 1.0)
        # L2 中にスティックを後ろに倒したとき、最後まで倒すとこの PWM まで遅くする（中立は越えない）
        # 止まらずに走れる最も遅い値にする。倒しきると止まるなら下げ、まだ速いなら上げる
        self.declare_parameter('l2_slowest_motor', 1545)
        self.declare_parameter('torch_gain', 1.0) # torch_pwm倍率のデフォルト値
        self.declare_parameter('gain_step', 0.2)
        self.declare_parameter('torch_gain_step', 0.1)
        self.declare_parameter('ps_button_index', 12) # PSボタンインデックス
        self.declare_parameter('l2_button_index', 6) # L2ボタンインデックス
        self.declare_parameter('r2_button_index', 7) # R2ボタンインデックス
        self.declare_parameter('dpad_axis_index', 6)  # 十字キーの軸インデックス
        
        # パラメータの取得
        self.MOTOR_NEUTRAL = self.get_parameter('motor_neutral').value
        self.MOTOR_MIN = self.get_parameter('motor_min').value
        self.MOTOR_MAX = self.get_parameter('motor_max').value
        self.STEERING_CENTER = self.get_parameter('steering_center').value
        self.STEERING_LEFT = self.get_parameter('steering_left').value
        self.STEERING_RIGHT = self.get_parameter('steering_right').value
        self.SPEED_DEADZONE = self.get_parameter('speed_deadzone').value
        self.STEERING_DEADZONE = self.get_parameter('steering_deadzone').value
        self.SPEED_SENSITIVITY = self.get_parameter('speed_sensitivity').value
        self.SPEED_EXPO = self.get_parameter('speed_expo').value
        self.manual_gain = self.get_parameter('manual_gain').value
        self.L2_SLOWEST_MOTOR = self.get_parameter('l2_slowest_motor').value
        self.torch_gain = self.get_parameter('torch_gain').value
        self.gain_step = self.get_parameter('gain_step').value
        self.torch_gain_step = self.get_parameter('torch_gain_step').value
        self.ps_button_index = self.get_parameter('ps_button_index').value
        self.l2_button_index = self.get_parameter('l2_button_index').value
        self.r2_button_index = self.get_parameter('r2_button_index').value
        self.dpad_axis_index = self.get_parameter('dpad_axis_index').value
        
        # Subscribers
        self.joy_sub = self.create_subscription(
            Joy,
            '/joy',
            self.joy_callback,
            10
        )
        self.torch_pwm_sub = self.create_subscription(
            TwistStamped,
            '/torch_pwm',
            self.torch_pwm_callback,
            10
        )
        
        # Publishers
        self.mux_pwm_pub = self.create_publisher(TwistStamped, '/mux_pwm', 10)
        self.status_pub = self.create_publisher(String, '/mux_pwm/status', 10)
        self.manual_gain_pub = self.create_publisher(Float32, '/mux_pwm/manual_gain', 10)
        self.torch_gain_pub = self.create_publisher(Float32, '/mux_pwm/torch_gain', 10)
        # /mux_pwm の出どころ（manual / auto / auto_l2）。学習で補正区間を見分けるために bag に記録する
        self.source_pub = self.create_publisher(String, '/mux_pwm/source', 10)

        # State variables
        self.manual_mode = True
        self.last_ps_button_state = 0
        self.latest_joy = None
        self.latest_torch_pwm = None
        self.prev_dpad_value = 0.0  # 前回の十字キー値
        
        # 初期ステータスをパブリッシュ
        self.publish_status()
        self.publish_manual_gain()
        self.publish_torch_gain()
        
        # 初期モードをINFOで出力
        self.get_logger().info(f'Initial mode: {"Manual" if self.manual_mode else "Auto"}')
        self.get_logger().info(f'Initial torch_gain: {self.torch_gain:.1f}')
    
    def convert_joy_to_motor_pwm(self, joy_value):
        """
        Joy値をモーターPWMに変換
        Args:
            joy_value: axes[1] の値 (-1.0 ~ +1.0)
        Returns:
            motor_pwm: PWM値(マイクロ秒)float型
        """
        if abs(joy_value) < self.SPEED_DEADZONE:
            return float(self.MOTOR_NEUTRAL)
        elif joy_value > 0:
            # 前進: 1620 → 1100
            adjusted = (joy_value - self.SPEED_DEADZONE) / (1.0 - self.SPEED_DEADZONE)
            adjusted = self.apply_speed_sensitivity(adjusted)
            return float(self.MOTOR_NEUTRAL - (self.MOTOR_NEUTRAL - self.MOTOR_MIN) * adjusted)
        else:
            # 後退: 1620 → 2300
            adjusted = (joy_value + self.SPEED_DEADZONE) / (1.0 - self.SPEED_DEADZONE)
            adjusted = self.apply_speed_sensitivity(abs(adjusted))
            return float(self.MOTOR_NEUTRAL + (self.MOTOR_MAX - self.MOTOR_NEUTRAL) * abs(adjusted))
    
    def apply_speed_sensitivity(self, adjusted):
        """
        直進入力に感度カーブと倍率を適用
        Args:
            adjusted: デッドゾーン補正後の入力 (0.0 ~ 1.0)
        Returns:
            感度適用後の入力 (0.0 ~ 1.0)
        """
        curved = abs(adjusted) ** self.SPEED_EXPO
        return min(1.0, curved * self.SPEED_SENSITIVITY)
    
    def convert_joy_to_steering_pwm(self, joy_value):
        """
        Joy値をステアリングPWMに変換
        Args:
            joy_value: axes[2] の値 (-1.0 ~ +1.0)
        Returns:
            steering_pwm: PWM値(マイクロ秒)float型
        """
        if abs(joy_value) < self.STEERING_DEADZONE:
            return float(self.STEERING_CENTER)
        elif joy_value > 0:
            # 右: 1500 → 1800
            return float(self.STEERING_CENTER + (self.STEERING_LEFT - self.STEERING_CENTER) * joy_value)
        else:
            # 左: 1500 → 1200
            return float(self.STEERING_CENTER + (self.STEERING_CENTER - self.STEERING_RIGHT) * joy_value)
    
    def apply_torch_gain(self, torch_pwm_msg):
        """
        torch_pwmのモーターにのみ倍率を適用（ステアリングはそのまま）
        
        Args:
            torch_pwm_msg: TwistStamped(AI出力)
        Returns:
            TwistStamped: 倍率適用後のメッセージ
        """
        output_msg = TwistStamped()
        output_msg.header.stamp = self.get_clock().now().to_msg()
        output_msg.header.frame_id = "base_link"
        
        # Torch PWM値を取得
        torch_motor = torch_pwm_msg.twist.linear.x
        torch_steering = torch_pwm_msg.twist.angular.z
        
        # モーターのみゲインを適用: neutral + gain * offset
        motor_offset = torch_motor - self.MOTOR_NEUTRAL
        gained_motor = self.MOTOR_NEUTRAL + self.torch_gain * motor_offset
        
        # クリッピング
        output_msg.twist.linear.x = float(max(self.MOTOR_MIN, min(self.MOTOR_MAX, gained_motor)))
        # ステアリングはそのまま
        output_msg.twist.angular.z = float(torch_steering)
        
        return output_msg
    
    def apply_l2_deceleration(self, torch_motor, joy_value):
        """
        L2 中にスティックを後ろに倒したときのモーター PWM
        モデル出力から l2_slowest_motor までを、倒した量に比例して遅くする

        後退側の変換（中立 → motor_max の 1080µs）をそのまま足すと、少し倒しただけで
        中立を越えて ESC がブレーキに入り、すぐ止まるため
        Args:
            torch_motor: torch_gain 適用済みのモデル出力
            joy_value: axes[1] の値（-1.0 ~ -speed_deadzone）
        Returns:
            motor_pwm: PWM値(マイクロ秒)float型
        """
        # speed_sensitivity は掛けない（倒しきったときに l2_slowest_motor まで届くように）
        adjusted = (abs(joy_value) - self.SPEED_DEADZONE) / (1.0 - self.SPEED_DEADZONE)
        adjusted = min(1.0, adjusted ** self.SPEED_EXPO)
        # モデル出力がすでに l2_slowest_motor より遅ければ、そのまま
        slowest = max(torch_motor, self.L2_SLOWEST_MOTOR)
        return float(torch_motor + self.manual_gain * adjusted * (slowest - torch_motor))
    
    def apply_manual_addition(self, torch_pwm_msg, joy_msg):
        """
        手動加算: mux = manual_gain * (manual - neutral) + torch
        
        Args:
            torch_pwm_msg: TwistStamped(AI出力)
            joy_msg: Joy(手動入力)
        Returns:
            TwistStamped: 加算後のメッセージ
        """
        output_msg = TwistStamped()
        output_msg.header.stamp = self.get_clock().now().to_msg()
        output_msg.header.frame_id = "base_link"
        
        # Manual PWM値を計算
        manual_motor = self.convert_joy_to_motor_pwm(joy_msg.axes[1])
        manual_steering = self.convert_joy_to_steering_pwm(joy_msg.axes[2])
        
        # Torch PWM値を取得（torch_gain適用済み）
        torch_gained_msg = self.apply_torch_gain(torch_pwm_msg)
        torch_motor = torch_gained_msg.twist.linear.x
        torch_steering = torch_gained_msg.twist.angular.z
        
        # ニュートラルからの差分を計算
        manual_motor_offset = manual_motor - self.MOTOR_NEUTRAL
        manual_steering_offset = manual_steering - self.STEERING_CENTER
        
        # 手動加算: mux = torch + gain * manual_offset
        # 後ろに倒したときは足し算ではなく、l2_slowest_motor に向けて少しずつ減速する
        if joy_msg.axes[1] <= -self.SPEED_DEADZONE:
            mixed_motor = self.apply_l2_deceleration(torch_motor, joy_msg.axes[1])
        else:
            mixed_motor = torch_motor + self.manual_gain * manual_motor_offset
        mixed_steering = torch_steering + 3 * manual_steering_offset
        
        # クリッピング
        output_msg.twist.linear.x = float(max(self.MOTOR_MIN, min(self.MOTOR_MAX, mixed_motor)))
        output_msg.twist.angular.z = float(max(self.STEERING_LEFT, min(self.STEERING_RIGHT, mixed_steering)))
        
        return output_msg
    
    def joy_callback(self, msg):
        """Joyメッセージのコールバック"""
        self.latest_joy = msg

        if len(msg.axes) >= 3:
            # ステアリング: 左スティック横 (axis 2; right horizontal stick)
            # -1.0 = 左, +1.0 = 右
            msg.axes[2] = -msg.axes[2]  # 反転
            
            # 速度: 右スティック縦 (axis 1; left vertical stick)
            # -1.0 = 後退, +1.0 = 前進
            msg.axes[1] = msg.axes[1]
        
        # PSボタンでのモード切り替え
        if len(msg.buttons) > self.ps_button_index:
            current_ps_state = msg.buttons[self.ps_button_index]
            if current_ps_state == 1 and self.last_ps_button_state == 0:
                self.manual_mode = not self.manual_mode
                # モード切り替え時にINFO出力
                mode_str = "Manual" if self.manual_mode else "Auto"
                self.get_logger().info(f'Mode switched to: {mode_str}')
                self.publish_status()
            self.last_ps_button_state = current_ps_state
        
        # R2ボタンの状態を取得
        r2_pressed = False
        if len(msg.buttons) > self.r2_button_index:
            r2_pressed = msg.buttons[self.r2_button_index] == 1
        
        # L2ボタンの状態を取得
        l2_pressed = False
        if len(msg.buttons) > self.l2_button_index:
            l2_pressed = msg.buttons[self.l2_button_index] == 1
        
        # 十字キーでのゲイン調整（axes[6]から取得）
        if len(msg.axes) > self.dpad_axis_index:
            current_dpad_value = msg.axes[self.dpad_axis_index]
            
            # 連続検出防止: 前回が0.0で今回が非0.0のときのみ処理
            if self.prev_dpad_value == 0.0 and current_dpad_value != 0.0:
                if r2_pressed:
                    # R2押下時: torch_gain調整
                    if current_dpad_value < 0:  # 右 (-1)
                        self.torch_gain = round(self.torch_gain + self.torch_gain_step, 1)
                        self.get_logger().info(f'Torch gain increased to: {self.torch_gain:.1f}')
                        self.publish_torch_gain()
                    elif current_dpad_value > 0:  # 左 (1)
                        self.torch_gain = max(0.0, round(self.torch_gain - self.torch_gain_step, 1))
                        self.get_logger().info(f'Torch gain decreased to: {self.torch_gain:.1f}')
                        self.publish_torch_gain()
                
                elif l2_pressed:
                    # L2押下時: manual_gain調整
                    if current_dpad_value < 0:  # 右 (-1)
                        self.manual_gain = min(1.0, round(self.manual_gain + self.gain_step, 1))
                        self.get_logger().info(f'Manual gain increased to: {self.manual_gain:.1f}')
                        self.publish_manual_gain()
                    elif current_dpad_value > 0:  # 左 (1)
                        self.manual_gain = max(0.0, round(self.manual_gain - self.gain_step, 1))
                        self.get_logger().info(f'Manual gain decreased to: {self.manual_gain:.1f}')
                        self.publish_manual_gain()
            
            # 前回の値を更新
            self.prev_dpad_value = current_dpad_value
        
        # メッセージを発行
        self.publish_mux_pwm()
    
    def torch_pwm_callback(self, msg):
        """torch_pwmのコールバック"""
        self.latest_torch_pwm = msg
        self.publish_mux_pwm()
    
    def publish_mux_pwm(self):
        """現在のモードに応じてPWMデータを発行"""
        if self.manual_mode:
            # 手動モード
            if self.latest_joy is not None:
                output_msg = TwistStamped()
                output_msg.header.stamp = self.get_clock().now().to_msg()
                output_msg.header.frame_id = "base_link"
                output_msg.twist.linear.x = self.convert_joy_to_motor_pwm(self.latest_joy.axes[1])
                output_msg.twist.angular.z = self.convert_joy_to_steering_pwm(self.latest_joy.axes[2])
                self.mux_pwm_pub.publish(output_msg)
                self.publish_source('manual')
        else:
            # 自動モード
            if self.latest_torch_pwm is not None:
                # L2ボタンの状態をチェック
                l2_pressed = False
                if self.latest_joy is not None and len(self.latest_joy.buttons) > self.l2_button_index:
                    l2_pressed = self.latest_joy.buttons[self.l2_button_index] == 1
                
                if l2_pressed and self.latest_joy is not None:
                    # L2押下中: 手動入力を加算（torch_gain適用済み）
                    output_msg = self.apply_manual_addition(self.latest_torch_pwm, self.latest_joy)
                else:
                    # L2未押下: torch_pwm にtorch_gainを適用
                    output_msg = self.apply_torch_gain(self.latest_torch_pwm)
                
                self.mux_pwm_pub.publish(output_msg)
                self.publish_source('auto_l2' if l2_pressed else 'auto')

        # ステータスをパブリッシュ
        # self.publish_status()
    
    def publish_status(self):
        """現在のモード状態をJSON形式でパブリッシュ"""
        status_msg = String()
        
        # L2の状態を取得
        l2_pressed = False
        if self.latest_joy is not None and len(self.latest_joy.buttons) > self.l2_button_index:
            l2_pressed = self.latest_joy.buttons[self.l2_button_index] == 1
        
        status_dict = {
            'mode': 'Manual' if self.manual_mode else 'Auto',
            'source': 'joy' if self.manual_mode else 'torch_joy',
        }
        status_msg.data = json.dumps(status_dict)
        self.status_pub.publish(status_msg)
    
    def publish_source(self, source):
        """直前に出した /mux_pwm の出どころをパブリッシュ"""
        source_msg = String()
        source_msg.data = source
        self.source_pub.publish(source_msg)

    def publish_manual_gain(self):
        """現在の手動ゲイン値をパブリッシュ"""
        gain_msg = Float32()
        gain_msg.data = float(self.manual_gain)
        self.manual_gain_pub.publish(gain_msg)
        self.get_logger().info(f'Manual gain: {self.manual_gain:.1f}')
    
    def publish_torch_gain(self):
        """現在のtorch_gainをパブリッシュ"""
        gain_msg = Float32()
        gain_msg.data = float(self.torch_gain)
        self.torch_gain_pub.publish(gain_msg)


def main(args=None):
    rclpy.init(args=args)
    node = PwmMuxNode()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()