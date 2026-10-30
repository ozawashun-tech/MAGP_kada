#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import Joy, LaserScan
from geometry_msgs.msg import TwistStamped
from std_msgs.msg import String
import numpy as np
import json
from ament_index_python.packages import get_package_share_directory
import os
import time
from .model.model import Model


class NNControllerNode(Node):
    def __init__(self):
        super().__init__('nn_controller_node')
        self.start_time = time.time()
        
        # scan_rangesのシーケンスを保持するリスト
        self.scan_sequence = []
        # Auto に切り替わったら、次のスキャンで履歴を埋め直す
        self.auto_mode = False
        self.reset_history = False
        self.ready_logged = False
        
        # パラメータの宣言
        self.declare_parameter('model_path', '')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('pwm_topic', '/torch_pwm')
        self.declare_parameter('prediction_steps', 1)
        self.declare_parameter('scan_history_length', 20)
        self.declare_parameter('scan_history_stride', 10)
        self.declare_parameter('mode_topic', '/mux_pwm/status')
        self.declare_parameter('reset_history_on_auto', True)
        # model_path が無いときに使うモデル（current.pth を一度も deploy していないとき）
        self.declare_parameter('fallback_model_path', '')
        # model_path の差し替え（current.pth の付け替え）を見て、Manual 中に読み込み直す
        self.declare_parameter('reload_model', True)
        self.declare_parameter('model_check_period', 1.0)
        # モデルの枠（model_path と同じディレクトリの <枠>.pth）。Manual 中に R1 でこの順に切り替える
        # PC の make dagger / mirror が枠を付け替え、current.pth（model_path）をその枠に向ける
        self.declare_parameter('slot_names', [
            'origin_clockwise', 'origin_counter_clockwise', 'mirror_clockwise', 'mirror_counter_clockwise',
        ])
        self.declare_parameter('joy_topic', '/joy')
        self.declare_parameter('l1_button_index', 4)
        self.declare_parameter('r1_button_index', 5)
        # 使っているモデルの名前（ファイル名から .pth を除いたもの）。M5Stack に出す
        self.declare_parameter('model_name_topic', '/nn_pwm_controller/model')
        
        # パラメータの取得
        model_path = self.get_parameter('model_path').get_parameter_value().string_value
        self.prediction_steps = self.get_parameter('prediction_steps').get_parameter_value().integer_value
        self.scan_history_length = self.get_parameter('scan_history_length').get_parameter_value().integer_value
        self.scan_history_stride = self.get_parameter('scan_history_stride').get_parameter_value().integer_value
        self.reset_history_on_auto = self.get_parameter('reset_history_on_auto').get_parameter_value().bool_value
        self.fallback_model_path = self.get_parameter('fallback_model_path').get_parameter_value().string_value
        reload_model = self.get_parameter('reload_model').get_parameter_value().bool_value
        model_check_period = self.get_parameter('model_check_period').get_parameter_value().double_value
        slot_names = self.get_parameter('slot_names').get_parameter_value().string_array_value
        self.l1_index = self.get_parameter('l1_button_index').get_parameter_value().integer_value
        self.r1_index = self.get_parameter('r1_button_index').get_parameter_value().integer_value
        
        # モデルパスが指定されていない場合はデフォルトパスを使用
        if not model_path:
            package_share_dir = get_package_share_directory('pytorch_pwm_controller')
            model_path = os.path.join(package_share_dir, 'model', 'model_v9.pth')
        self.model_path = model_path
        model_dir = os.path.dirname(model_path)
        self.slot_paths = [os.path.join(model_dir, f'{name}.pth') for name in slot_names]
        # 使うモデルのパス（枠のリンクか model_path）。起動時と current.pth の付け替え時は、current.pth が指す枠
        self.link_signature = self.current_link_signature()
        self.selected_path = self.path_for_link(self.link_signature)
        signature = self.model_signature()
        if signature is None:
            raise FileNotFoundError(f'{model_path} not found (fallback: {self.fallback_model_path or "none"})')
        model_path = signature[0]

        scan_topic = self.get_parameter('scan_topic').get_parameter_value().string_value
        pwm_topic = self.get_parameter('pwm_topic').get_parameter_value().string_value
        mode_topic = self.get_parameter('mode_topic').get_parameter_value().string_value
        joy_topic = self.get_parameter('joy_topic').get_parameter_value().string_value
        model_name_topic = self.get_parameter('model_name_topic').get_parameter_value().string_value

        self.get_logger().info(f'Loading model from: {self.selected_path} ({model_path})')
        self.get_logger().info(f'Prediction steps: {self.prediction_steps}')
        self.get_logger().info(f'Scan history: length={self.scan_history_length}, stride={self.scan_history_stride}')

        # モデルの初期化
        try:
            self.model = self.load_model(model_path)
            self.get_logger().info(f'Model loaded successfully: {model_path}')
        except Exception as e:
            self.get_logger().error(f'Failed to load model: {str(e)}')
            raise
        self.loaded_signature = signature
        self.failed_signature = None
        self.reload_pending = False

        # R1 を離したときに枠を切り替える。押している間に L1 も押したら（L1+R1 の非常停止）切り替えない
        self.r1_pressed = False
        self.l1_during_r1 = False
        
        # Subscriber
        self.scan_sub = self.create_subscription(
            LaserScan,
            scan_topic,
            self.scan_callback,
            10
        )
        # 手動中に車を運ぶと、そのスキャンが Auto 再開後も約 5 秒間履歴に残るため、モードの切り替えを見る
        self.mode_sub = self.create_subscription(
            String,
            mode_topic,
            self.mode_callback,
            10
        )
        self.joy_sub = self.create_subscription(Joy, joy_topic, self.joy_callback, 10)

        # Publisher (TwistStamped for PWM commands)
        self.pwm_pub = self.create_publisher(TwistStamped, pwm_topic, 10)
        # あとから起動した購読者（M5Stack のブリッジなど）にも届くよう、最後の名前を残し、1 秒ごとにも出す
        self.model_name_pub = self.create_publisher(
            String, model_name_topic, QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        )
        self.publish_model_name()
        self.model_name_timer = self.create_timer(1.0, self.publish_model_name)

        if reload_model:
            self.model_timer = self.create_timer(model_check_period, self.check_model_file)

        self.get_logger().info(f'Subscribing to: {scan_topic}')
        self.get_logger().info(f'Publishing to: {pwm_topic}')
        self.get_logger().info(f'Reset history on Auto: {self.reset_history_on_auto} ({mode_topic})')
        self.get_logger().info(f'Reload model in Manual: {reload_model} ({self.model_path})')
        self.get_logger().info(f'Model slots (R1 in Manual): {", ".join(slot_names)} ({joy_topic})')
        self.get_logger().info(f'Publishing model name to: {model_name_topic}')

    def load_model(self, path):
        return Model(
            path,
            prediction_steps=self.prediction_steps,
            scan_history_length=self.scan_history_length,
        )

    def current_link_signature(self):
        """model_path（current.pth）の (リンクの行き先, 実体のパス, 更新時刻)。deploy・rollback で変わる。無ければ None"""
        try:
            target = os.readlink(self.model_path) if os.path.islink(self.model_path) else None
            real_path = os.path.realpath(self.model_path)
            return target, real_path, os.stat(real_path).st_mtime_ns
        except OSError:
            return None

    def path_for_link(self, link_signature):
        """current.pth が枠（<枠>.pth）を指していればその枠、そうでなければ model_path を使う"""
        target = link_signature[0] if link_signature else None
        for path in self.slot_paths:
            if target is not None and os.path.basename(target) == os.path.basename(path):
                return path
        return self.model_path

    def model_signature(self):
        """今読むべきモデルファイルの (実体のパス, 更新時刻)。無ければ None"""
        for path in (self.selected_path, self.model_path, self.fallback_model_path):
            if path and os.path.exists(path):
                real_path = os.path.realpath(path)
                return real_path, os.stat(real_path).st_mtime_ns
        return None

    def model_name(self):
        """読み込んでいるモデルのファイル名（.pth を除く）"""
        return os.path.splitext(os.path.basename(self.loaded_signature[0]))[0]

    def publish_model_name(self):
        self.model_name_pub.publish(String(data=self.model_name()))

    def check_model_file(self):
        """current.pth の付け替え（deploy・rollback）とモデルファイルの差し替えを見て、Manual 中に読み込み直す"""
        # deploy・rollback したら、R1 で選んだ枠に関わらず、current.pth が指す枠に切り替える
        link_signature = self.current_link_signature()
        if link_signature != self.link_signature:
            self.link_signature = link_signature
            selected_path = self.path_for_link(link_signature)
            if selected_path != self.selected_path:
                self.selected_path = selected_path
                self.get_logger().info(f'{self.model_path} changed, selecting {selected_path}')
        self.load_selected_model()

    def load_selected_model(self):
        """選んでいるモデルが読み込んだものと違えば、Manual 中に読み込む"""
        signature = self.model_signature()
        if signature is None or signature in (self.loaded_signature, self.failed_signature):
            return
        
        # 読み込み中は推論が止まり、pwm_mux_node は古い /torch_pwm を出し続けるため、Auto 中は読まない
        if self.auto_mode:
            if not self.reload_pending:
                self.reload_pending = True
                self.get_logger().info(f'New model found, will load it in Manual: {signature[0]}')
            return
        
        try:
            model = self.load_model(signature[0])
        except Exception as e:
            self.failed_signature = signature
            self.get_logger().error(f'Failed to reload model, keeping the current one: {str(e)}')
            return
        self.model = model
        self.loaded_signature = signature
        self.reload_pending = False
        self.get_logger().info(f'Model reloaded: {signature[0]}')
        self.publish_model_name()

    def joy_callback(self, msg: Joy):
        """R1 を離したら、次の枠に切り替える（L1+R1 の非常停止では切り替えない）"""
        def pressed(index):
            return 0 <= index < len(msg.buttons) and msg.buttons[index] == 1

        r1 = pressed(self.r1_index)
        if r1:
            if not self.r1_pressed:
                self.l1_during_r1 = False
            self.l1_during_r1 = self.l1_during_r1 or pressed(self.l1_index)
        elif self.r1_pressed and not self.l1_during_r1:
            self.select_next_slot()
        self.r1_pressed = r1

    def select_next_slot(self):
        """リンクがある次の枠を選んで読み込む（Manual 中のみ）"""
        if self.auto_mode:
            self.get_logger().info('R1 ignored in Auto (switch the model in Manual)')
            return

        start = self.slot_paths.index(self.selected_path) + 1 if self.selected_path in self.slot_paths else 0
        candidates = self.slot_paths[start:] + self.slot_paths[:start]
        next_path = next((path for path in candidates if os.path.exists(path)), None)
        if next_path is None:
            self.get_logger().warn(f'No model slots in {os.path.dirname(self.model_path)}, keeping {self.model_name()}')
            return

        self.selected_path = next_path
        self.get_logger().info(f'Model slot selected: {os.path.basename(next_path)}')
        self.load_selected_model()

    def mode_callback(self, msg: String):
        """pwm_mux_node のモード（{"mode": "Auto" | "Manual", ...}）を受け取る"""
        try:
            auto_mode = json.loads(msg.data).get('mode') == 'Auto'
        except (json.JSONDecodeError, AttributeError):
            self.get_logger().warn(f'Unknown mode message: {msg.data}')
            return
        
        if auto_mode and not self.auto_mode and self.reset_history_on_auto:
            self.reset_history = True
        self.auto_mode = auto_mode
    
    def scan_callback(self, msg: LaserScan):
        try:
            # LaserScanデータをNumPy配列に変換
            scan_ranges = np.array(msg.ranges, dtype=np.float32)
            
            # inf値を最大値に、nan値を0に置換
            scan_ranges = np.where(np.isinf(scan_ranges), msg.range_max, scan_ranges)
            scan_ranges = np.where(np.isnan(scan_ranges), 0.0, scan_ranges)
            
            # シーケンスにscan_rangesを追加
            self.scan_sequence.append(scan_ranges)
            
            # 必要なデータ数を計算（strideを考慮）
            required_length = (self.scan_history_length - 1) * self.scan_history_stride + 1
            
            # Auto に切り替わった直後: 手動中のスキャンを捨て、最新のスキャン 1 枚で履歴を埋める
            # （学習でも bag の先頭は、足りない履歴を最初のフレームで埋めている）
            if self.reset_history:
                self.reset_history = False
                self.scan_sequence = [scan_ranges] * required_length
                self.get_logger().info('Switched to Auto: scan history reset to the latest scan')
            
            # 必要な数が溜まるまで待つ
            if len(self.scan_sequence) < required_length:
                return
            
            # 初回のみログ出力（履歴をリセットしたときは出さない）
            if not self.ready_logged:
                self.ready_logged = True
                self.get_logger().info(f'Ready for inference (scan: {required_length})')
            
            # 必要な数を超えたら古いものを削除
            while len(self.scan_sequence) > required_length:
                self.scan_sequence.pop(0)
            
            # スキャンデータをstrideを考慮して取得
            scan_indices = [i * self.scan_history_stride for i in range(self.scan_history_length)]
            inference_scan = np.stack(
                [self.scan_sequence[i] for i in scan_indices], axis=0
            )  # shape: (scan_history_length, scan_size)

            # 推論実行
            prediction = self.model.inference_next_step_only(inference_scan)

            # self.get_logger().info(f'DNN output is {prediction[0]:.4f}, {prediction[1]:.4f}')
            
            # 予測結果を取得 [throttle, angle]
            throttle = prediction[0]
            angle = prediction[1]
            
            # TwistStampedメッセージの作成
            pwm_msg = TwistStamped()
            pwm_msg.header.stamp = self.get_clock().now().to_msg()
            pwm_msg.header.frame_id = "base_link"
            
            # throttle
            pwm_msg.twist.linear.x = float(throttle)
            pwm_msg.twist.linear.y = 0.0
            pwm_msg.twist.linear.z = 0.0
            
            # angle
            pwm_msg.twist.angular.x = 0.0
            pwm_msg.twist.angular.y = 0.0
            pwm_msg.twist.angular.z = float(angle)
            
            # パブリッシュ
            self.pwm_pub.publish(pwm_msg)
            
        except Exception as e:
            self.get_logger().error(f'Error in scan_callback: {str(e)}')


def main(args=None):
    rclpy.init(args=args)
    try:
        node = NNControllerNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'Error: {str(e)}')
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()