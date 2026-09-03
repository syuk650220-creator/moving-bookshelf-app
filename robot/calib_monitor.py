"""
calib_monitor.py ― 較正用の読み取りツール

`/odom` を購読して、**起動した時点を原点とした**移動量を表示し続けます。

    前後 x=+2.987 m   横 y=+0.031 m   直線距離  2.987 m   回転  +12.40 度 (+0.03 回転)

────────────────────────────────────────────────────────────
なぜ専用のツールが要るのか
────────────────────────────────────────────────────────────
`/odom` の姿勢はクォータニオンで、**yaw は −180°〜+180° に畳まれています**。
そのため何回転しても値が戻ってしまい、「5回転した」ことが読み取れません。

このツールは毎回の差分を足し込むので、**何度でも累積した角度**を表示します。
1800度（5回転）や −3600度（−10回転）がそのまま出ます。

────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────

    python3 calib_monitor.py

  ・**起動した瞬間が原点**です。測定のたびに起動し直してください
  ・Ctrl-C で終了。最後に表示されていた値が測定値です
  ・mecanum_node.py が動いている必要があります（別の窓で）
"""

import math
import sys

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry


class CalibMonitor(Node):

    def __init__(self):
        super().__init__("calib_monitor")
        self.x0 = None
        self.y0 = None
        self.yaw_prev = None
        self.yaw_total = 0.0          # ★畳まれていない累積角度 [rad]★
        self.count = 0

        self.create_subscription(Odometry, "odom", self.on_odom, 10)

        print("=" * 72)
        print(" 較正モニタ ― いまの位置を原点として測ります")
        print(" 測定が終わったら Ctrl-C。最後の行が測定値です。")
        print("=" * 72)

    def on_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation

        # クォータニオン → yaw（z軸まわりだけなので atan2 で足りる）
        yaw = 2.0 * math.atan2(q.z, q.w)

        if self.x0 is None:                       # 最初の1回を原点にする
            self.x0, self.y0, self.yaw_prev = p.x, p.y, yaw

        # ★ここが肝★ 差分を −180°〜+180° に畳んでから足し込むことで、
        #   何回転しても途切れない累積角度になる。
        d = yaw - self.yaw_prev
        while d > math.pi:
            d -= 2.0 * math.pi
        while d < -math.pi:
            d += 2.0 * math.pi
        self.yaw_total += d
        self.yaw_prev = yaw

        dx = p.x - self.x0
        dy = p.y - self.y0
        self.count += 1

        sys.stdout.write(
            "\r 前後 x=%+7.3f m   横 y=%+7.3f m   直線距離 %6.3f m   "
            "回転 %+9.2f 度 (%+6.2f 回転)   "
            % (dx, dy, math.hypot(dx, dy),
               math.degrees(self.yaw_total), self.yaw_total / (2.0 * math.pi))
        )
        sys.stdout.flush()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = CalibMonitor()
        rclpy.spin(node)
    except KeyboardInterrupt:
        print()
        if node is not None and node.count == 0:
            print("★/odom を1回も受け取っていません。"
                  "mecanum_node.py が動いているか確認してください★")
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
