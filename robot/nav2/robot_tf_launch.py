"""
robot_tf_launch.py ― 実走（Nav2）のときの静的 TF  ★段階3★

    map ──(AMCL)──> odom ──(mecanum_node.py)──> base_footprint ──【ここ】──> base_link ──【ここ】──> laser

地図づくりで使う ~/bringup/temp_tf_launch.py は odom→base_footprint まで「動かない仮の TF」で出します。
実走ではそこを mecanum_node.py（車輪オドメトリ）が出すので、★temp_tf_launch.py は止めて★、
かわりにこの launch で残りの 2 本（base_footprint→base_link、base_link→laser）だけを出します。
両方動かすと base_footprint の親が 2 つになって TF が壊れます。

鎖の形を地図づくりのときと同じ（完全ガイドの正準チェーン）にしてあるので、
Nav2 標準の nav2_params.yaml のフレーム名（amcl は base_footprint、costmap は base_link）を書き換えずに済みます。
そのかわり mecanum_node.py は base_frame を base_footprint にして起動します。

    # 窓A: 車輪オドメトリ（odom → base_footprint）
    python3 ~/moving-bookshelf-app/robot/mecanum_node.py --ros-args -p base_frame:=base_footprint
    # 窓B: この launch（★数値は temp_tf_launch.py と同じにする★。既定値はこの機体の値なので、引数なしで合う）
    ros2 launch ~/moving-bookshelf-app/robot/nav2/robot_tf_launch.py

★既定値はこの機体の値。機体の「前」（robot_params.py の 0 章）に合わせて自動で決まります★
    base_footprint→base_link  z 0.05
    base_link→laser
        前＝電池・基板の側（既定。2026-09-30 から）  x -0.10, z 0.15, yaw 0
        前＝本棚側（ROBOT_FRONT=shelf。2026-09-20 に実機で確認した値）  x 0.10, z 0.15, yaw 3.14159
  LiDAR は本棚の上に付いていて、RPLIDAR の 0° は電池・基板の側を向いています。
  前後を入れ替えると base_link が 180° 回るので、同じ取り付けでも x の符号と yaw が変わります。
  ここが機体の前と食い違うと、地図は作れてしまいますが、Nav2 がゴールと反対へ走らせます。
  確かめ方: ラジコンで前進させたとき、RViz の base_link の赤い軸の向きへロボが進めば合っています。

LiDAR の取付位置は、地図を作ったときと同じ場所にすること（違うと地図と /scan がずれます）。
前後を入れ替えても取り付けは同じなので、前に作った地図はそのまま使えます。
確認:
    ros2 run tf2_ros tf2_echo odom laser        # 数値が流れれば鎖がつながっている
"""

import os
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# robot_params.py はひとつ上のフォルダ（robot/）にある
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import robot_params as P        # noqa: E402


def generate_launch_description():
    lx, ly, lyaw = P.laser_mount()
    print(f"[robot_tf_launch] 機体の前: {P.FRONT_NAME}（ROBOT_FRONT={P.FRONT}）"
          f" → LiDAR の既定値 x={lx:+.2f} y={ly:+.2f} yaw={lyaw:.5f}")
    args = [
        DeclareLaunchArgument("laser_x", default_value=f"{lx:.3f}",
                              description="base_link から見た LiDAR の前後位置 [m]（前が +）"),
        DeclareLaunchArgument("laser_y", default_value=f"{ly:.3f}",
                              description="同 左右位置 [m]（左が +）"),
        DeclareLaunchArgument("laser_z", default_value="0.15",
                              description="同 高さ [m]"),
        DeclareLaunchArgument("laser_yaw", default_value=f"{lyaw:.5f}",
                              description="LiDAR の向き [rad]。RPLIDAR の 0° は電池・基板の側を向いている"
                                          "（前＝電池・基板の側なら 0、前＝本棚側なら 3.14159）"),
        DeclareLaunchArgument("base_z", default_value="0.05",
                              description="base_footprint（床）から base_link までの高さ [m]（RViz の見た目だけに効く）"),
    ]

    footprint_tf = Node(
        package="tf2_ros", executable="static_transform_publisher", name="tf_footprint_to_base",
        arguments=["--x", "0", "--y", "0", "--z", LaunchConfiguration("base_z"),
                   "--frame-id", "base_footprint", "--child-frame-id", "base_link"],
    )
    laser_tf = Node(
        package="tf2_ros", executable="static_transform_publisher", name="tf_base_to_laser",
        arguments=["--x", LaunchConfiguration("laser_x"), "--y", LaunchConfiguration("laser_y"),
                   "--z", LaunchConfiguration("laser_z"), "--yaw", LaunchConfiguration("laser_yaw"),
                   "--frame-id", "base_link", "--child-frame-id", "laser"],
    )
    return LaunchDescription(args + [footprint_tf, laser_tf])
