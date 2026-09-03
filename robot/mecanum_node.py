"""
mecanum_node.py ― ROS2 ノード（Nav2 と Arduino のあいだ）  ★タスク A3 + A5★

    /cmd_vel  ──> 量子化 ──> 7バイトのパケット（SEND_PERIOD ごと・既定 25Hz）──> Arduino ×2
    /odom     <── 順運動学 <── テレメトリ（10Hz）        <── Arduino ×2

通信は **USB シリアル**です（SPI ではありません）。
仕様は simulink/arduino/README_raspberrypi.md（v2・テレメトリ 24 バイト）、
実装の中身は mecanum_serial.py にあります。
IMU（加速度・角速度）は Telemetry.acc / .gyro に入っていますが、まだ /imu には出していません。
★IMU は Arduino Nano 33 IoT 内蔵の LSM6DS3 を使う方針です（2026-09-03 決定。BNO055 は使いません）★
  次の仕事: /imu（sensor_msgs/Imu）を出して robot_localization に角速度 Z を入れる（設計メモ §10 A6）。

────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────

  ros2 run <pkg> mecanum_node
  python3 mecanum_node.py                    # パッケージ化前はこれでも動きます

  # 動作確認（別の端末から）
  ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1}}"
  ros2 topic echo /odom

  # パラメータを変える
  python3 mecanum_node.py --ros-args -p use_vy:=true -p max_rpm:=30.0

────────────────────────────────────────────────────────────
★publish_tf について★
────────────────────────────────────────────────────────────
robot_localization（EKF）を動かすときは **publish_tf:=false** にしてください。
EKF が odom→base_link を出すので、両方が出すと TF が二重になって壊れます。
単体で動作確認するあいだだけ true（既定）で使います。
"""

from __future__ import annotations

import math
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy

from geometry_msgs.msg import Twist, TwistStamped, TransformStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32MultiArray
from tf2_ros import TransformBroadcaster

import robot_params as P
from mecanum_serial import MecanumLink, Quantizer, MOTION_NAME, find_ports


def yaw_to_quat(yaw: float):
    """z 軸まわりの回転だけなので、これで足ります。"""
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


class MecanumNode(Node):

    def __init__(self):
        super().__init__("mecanum_serial")

        # ---------------- パラメータ ----------------
        self.declare_parameter("left_port", "")     # 空なら自動で探す
        self.declare_parameter("right_port", "")
        self.declare_parameter("use_vy", False)     # ★v1 は横移動を封印する★
        self.declare_parameter("max_rpm", float(P.MAX_RPM_NAV))
        self.declare_parameter("cmd_timeout", P.CMD_TIMEOUT)
        self.declare_parameter("hysteresis", 1.5)
        self.declare_parameter("hold_sec", 0.2)
        self.declare_parameter("publish_tf", True)  # ★EKF を使うときは false★
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_link")
        self.declare_parameter("odom_rate", 50.0)
        self.declare_parameter("use_stamped_cmd_vel", False)

        gp = self.get_parameter
        left = gp("left_port").value or None
        right = gp("right_port").value or None
        self.odom_frame = gp("odom_frame").value
        self.base_frame = gp("base_frame").value
        self.publish_tf = gp("publish_tf").value

        # ---------------- シリアル ----------------
        left, right = find_ports(left, right, verbose=False)
        self.get_logger().info(f"左基板: {left} / 右基板: {right}")

        self.link = MecanumLink(left, right, cmd_timeout=gp("cmd_timeout").value)
        self.link.open()

        self.quant = Quantizer(
            use_vy=gp("use_vy").value,
            hysteresis=gp("hysteresis").value,
            hold_sec=gp("hold_sec").value,
            max_rpm=float(gp("max_rpm").value),
        )
        if not gp("use_vy").value:
            self.get_logger().info("横移動は封印しています（use_vy:=true で解除）")

        # ---------------- 通信 ----------------
        # Nav2 の cmd_vel は「最新の1つだけ届けばよい」ので best_effort / depth 1
        qos_cmd = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                             history=HistoryPolicy.KEEP_LAST, depth=1)
        # Nav2 が TwistStamped を出す設定（enable_stamped_cmd_vel）なら
        # use_stamped_cmd_vel:=true にしてください。
        if gp("use_stamped_cmd_vel").value:
            self.create_subscription(TwistStamped, "cmd_vel",
                                     lambda m: self.on_cmd(m.twist), qos_cmd)
        else:
            self.create_subscription(Twist, "cmd_vel", self.on_cmd, qos_cmd)

        qos_odom = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.VOLATILE,
                              history=HistoryPolicy.KEEP_LAST, depth=10)
        self.pub_odom = self.create_publisher(Odometry, "odom", qos_odom)
        self.pub_rpm = self.create_publisher(Float32MultiArray, "wheel_rpm", 10)
        self.tf_bc = TransformBroadcaster(self) if self.publish_tf else None
        if self.publish_tf:
            self.get_logger().warn(
                "publish_tf=true です。robot_localization を動かすときは false にしてください")

        # ---------------- 自己位置（デッドレコニング）----------------
        self.x = 0.0
        self.y = 0.0
        self.th = 0.0
        self._last_t = time.perf_counter()

        rate = float(gp("odom_rate").value)
        self.create_timer(1.0 / rate, self.on_odom_tick)
        self.create_timer(2.0, self.on_health_tick)
        self._last_sent = 0
        self._last_recv = 0
        self._last_bad = 0

        self.get_logger().info("起動しました。/cmd_vel を待っています。")

    # =================================================================
    #  cmd_vel → 動作番号
    # =================================================================

    def on_cmd(self, msg: Twist):
        motion, rpm = self.quant(msg.linear.x, msg.linear.y, msg.angular.z)
        self.link.set_command(motion, rpm)

    # =================================================================
    #  テレメトリ → odom
    # =================================================================

    def on_odom_tick(self):
        now = time.perf_counter()
        dt = now - self._last_t
        self._last_t = now
        if dt <= 0.0 or dt > 0.5:        # 起動直後・詰まったときは積分しない
            return

        vel = self.link.body_velocity()
        if vel is None:
            return                        # まだテレメトリが来ていない
        vx, vy, wz = vel

        # 車体座標 → 世界座標（区間の中央の角度を使うと精度が上がる）
        th_mid = self.th + wz * dt * 0.5
        self.x += (vx * math.cos(th_mid) - vy * math.sin(th_mid)) * dt
        self.y += (vx * math.sin(th_mid) + vy * math.cos(th_mid)) * dt
        self.th = math.atan2(math.sin(self.th + wz * dt),
                             math.cos(self.th + wz * dt))   # −π〜π に畳む

        qz, qw = yaw_to_quat(self.th)
        stamp = self.get_clock().now().to_msg()

        od = Odometry()
        od.header.stamp = stamp
        od.header.frame_id = self.odom_frame
        od.child_frame_id = self.base_frame
        od.pose.pose.position.x = self.x
        od.pose.pose.position.y = self.y
        od.pose.pose.orientation.z = qz
        od.pose.pose.orientation.w = qw
        od.twist.twist.linear.x = vx
        od.twist.twist.linear.y = vy
        od.twist.twist.angular.z = wz

        # ★共分散★ 対角だけ入れる。メカナムは横行と旋回で滑るので、
        #   vy と wz は vx より大きめに見積もる（EKF に IMU を優先させるため）。
        #   使わない自由度（z, roll, pitch）は大きな値で「信用するな」と伝える。
        BIG = 1e6
        pc = [0.0] * 36
        pc[0] = 0.05 ** 2      # x
        pc[7] = 0.05 ** 2      # y
        pc[14] = BIG           # z
        pc[21] = BIG           # roll
        pc[28] = BIG           # pitch
        pc[35] = 0.10 ** 2     # yaw
        od.pose.covariance = pc

        tc = [0.0] * 36
        tc[0] = 0.02 ** 2      # vx  ← いちばん信用できる
        tc[7] = 0.10 ** 2      # vy  ← ローラーが滑るぶん粗い
        tc[14] = BIG
        tc[21] = BIG
        tc[28] = BIG
        tc[35] = 0.20 ** 2     # wz  ← その場旋回はいちばん滑る
        od.twist.covariance = tc

        self.pub_odom.publish(od)

        if self.tf_bc is not None:
            tf = TransformStamped()
            tf.header.stamp = stamp
            tf.header.frame_id = self.odom_frame
            tf.child_frame_id = self.base_frame
            tf.transform.translation.x = self.x
            tf.transform.translation.y = self.y
            tf.transform.rotation.z = qz
            tf.transform.rotation.w = qw
            self.tf_bc.sendTransform(tf)

        # 4輪の生の rpm（A4 の採寸・A5 の確認用）
        L, R = self.link.tlm_left, self.link.tlm_right
        if L and R:
            m = Float32MultiArray()
            m.data = [L.rpm_front, L.rpm_rear, R.rpm_front, R.rpm_rear]   # FL RL FR RR
            self.pub_rpm.publish(m)

    # =================================================================
    #  健全性の監視
    # =================================================================

    def on_health_tick(self):
        """
        送受信が止まっていないかを2秒ごとに見る。

        ★送信が止まる＝0.5秒後にウォッチドッグで機体が停止します★
        気づかないまま「なぜか動かない」になるのを防ぐための見張りです。
        """
        sent = self.link.sent_count - self._last_sent
        recv = self.link.recv_count - self._last_recv
        self._last_sent = self.link.sent_count
        self._last_recv = self.link.recv_count

        expected = 2.0 / P.SEND_PERIOD     # 2秒ぶんの本来の送信回数（25Hz なら 50 回）
        if sent < expected / 2:
            self.get_logger().error(
                f"送信が滞っています（2秒で {sent} 回、本来 {expected:.0f} 回）。ウォッチドッグで停止します")
        if recv < 10:      # 2秒なら本来 40 回前後（10Hz × 2枚）
            self.get_logger().warn(
                f"テレメトリが少ないです（2秒で {recv} 回）。"
                "モデルを［ビルド、展開、起動］したか、ケーブルを確認してください")

        # ★v2★ フレーム長の食い違い（Arduino が v1 のまま）
        if self.link.bad_frames > self._last_bad:
            self.get_logger().error(
                f"テレメトリの長さが合いません（累計 {self.link.bad_frames} 回）。"
                "Arduino が v1（12バイト）のままです。simulink/arduino/ の v2 を書き込んでください")
        self._last_bad = self.link.bad_frames

        # ★v2★ 暴走防止のラッチ（README_raspberrypi.md §6.2）
        #   解除は停止指令だけ。Quantizer が cmd_vel=0 を STOP に落とすので、Nav2 が止まれば解ける。
        #   ただし止まった理由（配線・障害物・拘束）を直さないと、動かした瞬間にまた止まる。
        stalled = self.link.stalled_wheels()
        if stalled:
            self.get_logger().warn(
                f"暴走防止が働いた可能性: {','.join(stalled)}（指令中なのに u=0・rpm=0）。"
                "cmd_vel を 0 にすると解除されます。配線・障害物・タイヤの拘束を確認してください")

    # =================================================================
    #  終了
    # =================================================================

    def shutdown(self):
        """★決まり6★ 停止指令を送ってから切断する。"""
        try:
            self.link.stop()
            self.link.close()
            self.get_logger().info(
                f"停止しました（送信 {self.link.sent_count} / 受信 {self.link.recv_count}）")
        except Exception as e:
            self.get_logger().error(f"終了処理で例外: {e}")


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = MecanumNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[mecanum_node] 起動できませんでした: {e}", file=sys.stderr)
    finally:
        if node is not None:
            node.shutdown()          # ← ここを必ず通す（機体を止めるため）
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
