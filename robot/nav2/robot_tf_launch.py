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
    # 窓B: この launch（★数値は temp_tf_launch.py と同じにする★）
    ros2 launch ~/moving-bookshelf-app/robot/nav2/robot_tf_launch.py laser_x:=0.10 laser_z:=0.20

LiDAR の取付位置は、地図を作ったときと同じ値にすること（違うと地図と /scan がずれます）。
    grep -n -E "'--(x|y|z|yaw)'" ~/bringup/temp_tf_launch.py      # いまの値を確かめる
確認:
    ros2 run tf2_ros tf2_echo odom laser        # 数値が流れれば鎖がつながっている
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [
        DeclareLaunchArgument("laser_x", default_value="0.10",
                              description="base_link から見た LiDAR の前後位置 [m]（前が +）"),
        DeclareLaunchArgument("laser_y", default_value="0.0",
                              description="同 左右位置 [m]（左が +）"),
        DeclareLaunchArgument("laser_z", default_value="0.20",
                              description="同 高さ [m]"),
        DeclareLaunchArgument("laser_yaw", default_value="0.0",
                              description="LiDAR の向き [rad]（前後が逆なら 3.14159）"),
        DeclareLaunchArgument("base_z", default_value="0.0",
                              description="base_footprint（床）から base_link までの高さ [m]。0 のままで構わない"),
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
