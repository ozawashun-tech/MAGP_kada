#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy


class JoyMuxNode(Node):
    def __init__(self):
        super().__init__('joy_mux_node')

        self.declare_parameter('input_topics', ['/joy0', '/joy1'])
        self.declare_parameter('output_topic', '/joy')

        input_topics = self.get_parameter('input_topics').value
        output_topic = self.get_parameter('output_topic').value

        self.pub = self.create_publisher(Joy, output_topic, 10)

        # トピックごとの最新メッセージとニュートラル値
        self.latest = {topic: None for topic in input_topics}
        self.neutral = {topic: None for topic in input_topics}

        self.subs = []
        for topic in input_topics:
            sub = self.create_subscription(
                Joy,
                topic,
                lambda msg, t=topic: self.joy_callback(msg, t),
                10
            )
            self.subs.append(sub)

        self.get_logger().info(f'joy_mux: {list(input_topics)} -> {output_topic}')

    def joy_callback(self, msg, topic):
        # 初回メッセージをニュートラルとして記録して終了
        if self.neutral[topic] is None:
            self.neutral[topic] = list(msg.axes)
            self.get_logger().info(f'Neutral recorded for {topic}: {list(msg.axes)}')
            return

        self.latest[topic] = msg
        self.publish_merged()

    def publish_merged(self):
        # ニュートラル記録済みかつ最新メッセージがあるトピックのみ対象
        available = {
            t: msg for t, msg in self.latest.items()
            if msg is not None and self.neutral[t] is not None
        }
        if not available:
            return

        n_axes = min(len(msg.axes) for msg in available.values())
        n_buttons = min(len(msg.buttons) for msg in available.values())

        # 各軸: ニュートラルからのズレが大きい方を採用
        merged_axes = []
        for i in range(n_axes):
            best = max(
                available,
                key=lambda t: abs(available[t].axes[i] - self.neutral[t][i])
            )
            merged_axes.append(available[best].axes[i])

        # ボタン: どちらかが押していれば押したとみなす
        merged_buttons = [
            max(msg.buttons[i] for msg in available.values())
            for i in range(n_buttons)
        ]

        out = Joy()
        out.header.stamp = self.get_clock().now().to_msg()
        out.axes = merged_axes
        out.buttons = merged_buttons
        self.pub.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = JoyMuxNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
