"""
ロボットの寸法・速度パラメータ（★ここだけ直せばよい場所★）

このファイルの値は Pi 側のすべてのプログラムが参照します。
Arduino 側の値（ゲイン・制御周期）は simulink/arduino/init_mecanum_params.m にあり、
そちらとは役割が分かれています。

  ・init_mecanum_params.m … 車輪を「目標rpmどおりに回す」ための値（Arduino）
  ・robot_params.py       … 車体を「目標の速度で動かす」ための値（Pi）

★★★ 未採寸の値が3つあります（下の TODO）。実機で測って埋めてください ★★★
"""

import math
import os

# =====================================================================
#  0. 機体の「前」（どちらの端を先頭にして走るか）
# =====================================================================
#
# ★2026-09-30 PM 決定: 前＝電池・基板の側（本棚は後ろ）★
#   それまでは「前＝本棚側」でした（2026-09-20 に実機で確認した向き）。
#   UWB の基板を電池・基板の側の縁に前向きに付けるため、走る向きを入れ替えます。
#   機体の作りと Arduino のファームは変えません。入れ替えるのは Pi 側の次の 3 つだけです。
#     ・前進と後退（と左横・右横）の指令 … mecanum_serial.hw_motion（Arduino へ送る直前に読み替える）
#     ・オドメトリの前後（と左右）の符号 … mecanum_serial.MecanumLink.body_velocity
#     ・LiDAR の取り付け位置の登録       … nav2/robot_tf_launch.py の既定値（下の laser_mount）
#   回転の向き（反時計回りが +）とジャイロの符号は変わりません。地図もそのまま使えます。
#
#   ★ピン（stop_points）の theta は「ロボの前が向く向き」です。入れ替えたら、すべてのピンを 180° 回すこと★
#       python3 pin_tool.py --turn-all 180
#
#   元の向きに戻すとき・試すときは、環境変数で上書きできます（★すべての窓で同じ値にすること★）:
#       export ROBOT_FRONT=shelf      # 前＝本棚側（2026-09-29 までの向き）
#       export ROBOT_FRONT=board      # 前＝電池・基板の側（既定）
#   戻したら、ピンもまた 180° 回します。
FRONT = os.environ.get("ROBOT_FRONT", "board").strip().lower()
if FRONT not in ("board", "shelf"):
    raise ValueError(f"ROBOT_FRONT は board か shelf です（いまの値: {FRONT!r}）")
FRONT_IS_SHELF = FRONT == "shelf"
FRONT_NAME = "本棚側" if FRONT_IS_SHELF else "電池・基板の側"

# LiDAR の取り付け位置。★「前＝本棚側」のときの値で書いておく★（2026-09-20 に実機で確認した値）
#   回転の中心から本棚側へ 0.10 m、RPLIDAR の 0° は電池・基板の側を向いている。
LASER_X_SHELF_FRONT = 0.10
LASER_Y_SHELF_FRONT = 0.0
LASER_YAW_SHELF_FRONT = math.pi


def laser_mount(front_is_shelf: bool | None = None) -> tuple[float, float, float]:
    """
    base_link から見た LiDAR の (x, y, yaw)。

    前後を入れ替えると base_link が 180° 回るので、x・y の符号が変わり、yaw も 180° 回ります
    （LiDAR は同じ場所に付いたまま。数値の見え方だけが変わる）。
    """
    shelf = FRONT_IS_SHELF if front_is_shelf is None else front_is_shelf
    if shelf:
        return LASER_X_SHELF_FRONT, LASER_Y_SHELF_FRONT, LASER_YAW_SHELF_FRONT
    yaw = LASER_YAW_SHELF_FRONT + math.pi
    yaw = math.atan2(math.sin(yaw), math.cos(yaw))
    if abs(yaw) < 1e-9:
        yaw = 0.0
    # 「+ 0.0」は -0.0 を 0.0 にそろえるため（launch の引数に "-0.0" が出ないように）
    return -LASER_X_SHELF_FRONT + 0.0, -LASER_Y_SHELF_FRONT + 0.0, yaw


# =====================================================================
#  1. 車体の寸法
# =====================================================================

# --- 車輪半径 [m] ---
# ★2026-08-20 修正: φ100mm ではなく φ80mm でした★
#   使用しているのは OSOYOO の 80mm メカナムホイール（4個セット・幅40mm・
#   ローラー9本・アルミ合金）。設計資料の「φ100mm（部品表より）」は誤りです。
#   → 速度の計算がすべて 25% ずれていました（0.314m → 0.251m/回転）。
#
# TODO: ★実測での確認は未実施★
#       ゴムの潰れがあるので、荷重をかけた状態の「実効半径」が正しい値です。
#       測り方: 床にテープで印を付け、車輪を手で正確に10回転させて
#       進んだ距離 D [m] を測る。  実効半径 = D / (10 × 2π)
#       80mm 公称なら D ≒ 2.51 m になるはずです。
WHEEL_RADIUS = 0.040

# --- (lx + ly) [m] ---
# ★2026-09-01 実機を採寸して確定★
#       ホイールベース(前後の車輪中心間) = 95 mm
#       トレッド      (左右の車輪中心間) = 175 mm
#       → (lx + ly) = (95 + 175) / 2 = 135 mm
#       （仮値 180mm から 25% の修正。旋回の odom に直接効く値です）
#
# ★2026-09-20 実機を定規で測り直して再確認: ホイール直径 80 mm、ホイールベース 95 mm（軸の中心どうし。9/1 の採寸どおり）★
#       CAD（Fusion の「全体像 v4」）はホイールのハブ穴の中心間が 100 mm、モータ軸の前後間隔が 105 mm で、
#       どちらも実機の 95 mm と合いません（ハブ穴の縁どうしの最短距離が 95.5 mm なのは偶然の一致）。
#       実機と食い違っているのは CAD の側なので、このファイルは実機の 95 mm のままにします。
#       ※ Fusion に入っているメカナムホイールのモデルも 外径 約 98 mm × 幅 46 mm（φ100 級の汎用品）で、実機（OSOYOO φ80 × 幅 40）とは
#         別物です。CAD のホイール寸法・ホイールベース（105 mm）・トレッド（209 mm）で、このファイルの値を直さないこと。
#
# TODO: ★較正はこれから★ この値は「図面上の寸法」です。
#       実際にはタイヤの接地点がずれるため、少し違う値が正解になります。
#       その場で正確に2回転させ、/odom の yaw が 720度 になるよう
#       この値を微調整してください（設計メモの較正手順）。
#         odom の回転が実際より小さい → この値を小さくする
#         odom の回転が実際より大きい → この値を大きくする
WHEEL_GEOM_L = 0.135

# --- 横移動の効率 ---
# TODO: ★未実測★ メカナムは横に動くときローラーが滑るため、
#       計算どおりの速度が出ない（simulink/arduino/README_raspberrypi.md §9 に「体感6〜7割」）。
#       ★v2 の IMU なら旋回の速さは実測できる（mecanum_serial.MecanumLink.yaw_rate）★
#       測り方: 横移動を一定rpm・一定秒数で走らせ、実際に進んだ距離を測る。
#         K_STRAFE = 実際に進んだ距離 / 計算上進むはずだった距離
#       ★床材で変わります。会場リハでは会場の床でも測り直すこと★
K_STRAFE = 0.65


# =====================================================================
#  2. 速度の上限・下限
# =====================================================================

# Arduino 側の絶対上限 [rpm]。これを超えて送っても Arduino が切り詰める。
# （init_mecanum_params.m の MAX_RPM と一致させること）
MAX_RPM_HW = 150

# 会場運用での上限 [rpm]。0.25 m/s 相当（会場デモ方針書 B3）。
# Nav2 の速度上限もこれに合わせる。
# ★φ80mm に修正したので 48 → 60 rpm に変わりました★
MAX_RPM_NAV = 60

# 最終進入の速度 [rpm]。0.10〜0.15 m/s 相当。
# ★同じく 19〜29 → 24〜36 rpm に変わりました★
MIN_APPROACH_RPM = 24
MAX_APPROACH_RPM = 36

# これ未満の指令は 0（停止）に丸める [rpm]。
#
# ★2026-09-01 --sweep の実測（車輪を浮かせた状態）★
#     5rpm→101.3%  10rpm→100.5%  15rpm→100.5%  20rpm→99.9%  25rpm→99.9%
#    30rpm→ 99.6%  35rpm→ 99.6%  40rpm→100.1%  45rpm→100.2%
#   全域で誤差1%以内。5rpm でも指令どおり回っており、
#   FRIT で決めた I-PD ゲインがよく効いていることが確認できました。
#
# ★それでも 5 にしない理由★
#   上の測定は「無負荷」です。機体の重さも床の摩擦もかかっていません。
#   実走では低速ほど厳しくなるため、余裕を見て 10 とします。
#   （10rpm = 0.042m/s。エンコーダ分解能 2.01rpm に対して 20%）
#
# TODO: 床に置いた状態で改めて確認し、失速するようなら上げること。
MIN_RPM = 10


# =====================================================================
#  3. 通信（Arduino 側と必ず一致させること）
#     simulink/arduino/README_raspberrypi.md §12「変更してはいけないもの」
#     ★フレームの長さ・ヘッダ・倍率は mecanum_serial.py の TLM_* にあります（v2 = 24 バイト）★
# =====================================================================

BAUDRATE = 115200

# --- 指令の送信周期 [s]。仕様の「20〜50ms」の範囲で選ぶ ---
# ★2026-09-03 に 0.02 → 0.04 へ変更（右 2 輪だけ 0.3 秒ほど遅れて動き出す症状の対策）★
#   Arduino 側の Serial Receive ブロックは制御周期 Ts=20ms ごとに「ヘッダ付き 1 パケット」しか
#   取り出しません。こちらが同じ 20ms ごとに送ると、Arduino が 1 周期でも取りこぼす
#   （IMU の I2C 読み取りや USB 送信で周期がずれる等）たびに受信バッファに 1 パケット溜まり、
#   消費が生産に追いつけないため遅れが溜まる一方になります。溜まり方は基板ごとに違うので
#   片側だけ遅れて見えます。MATLAB の pc_controller.m は 20 Hz（50ms）で送っていて遅れが出ません。
#   40ms（25 Hz）なら Arduino 側に毎秒 25 回ぶんの余裕ができ、溜まったぶんも自然に消えます。
#   ウォッチドッグ 0.5s に対しては 12 パケット分の余裕があります。
SEND_PERIOD = 0.04
WATCHDOG_SEC = 0.5      # [s] Arduino 側のウォッチドッグ（参考値）
CMD_TIMEOUT = 0.2       # [s] 上位から指令が来なくなったら停止扱いにする時間


# =====================================================================
#  4. 換算（ここから下は計算だけ。触らなくてよい）
# =====================================================================

WHEEL_CIRCUMFERENCE = 2 * math.pi * WHEEL_RADIUS    # [m] 車輪1回転で進む距離

# エンコーダの分解能 [rpm/count]（init_mecanum_params.m より）
#   回転数 = カウント差 × 60 / (Ts × COUNTS_PER_REV)
ENCODER_TS = 0.02
COUNTS_PER_REV = 1495
RPM_PER_COUNT = 60.0 / (ENCODER_TS * COUNTS_PER_REV)   # ≒ 2.01 rpm


def rpm_to_mps(rpm: float) -> float:
    """車輪の回転数 [rpm] → 車輪の周速 [m/s]"""
    return rpm * WHEEL_CIRCUMFERENCE / 60.0


def mps_to_rpm(mps: float) -> float:
    """車輪の周速 [m/s] → 車輪の回転数 [rpm]"""
    return mps * 60.0 / WHEEL_CIRCUMFERENCE


if __name__ == "__main__":
    print("=== robot_params ===")
    lx, ly, lyaw = laser_mount()
    print(f"機体の前      : {FRONT_NAME}（ROBOT_FRONT={FRONT}）")
    print(f"LiDAR の位置  : x={lx:+.3f} m  y={ly:+.3f} m  yaw={lyaw:.5f} rad（base_link から見て）")
    print(f"車輪半径      : {WHEEL_RADIUS*1000:.0f} mm  (周長 {WHEEL_CIRCUMFERENCE:.3f} m)")
    print(f"(lx + ly)     : {WHEEL_GEOM_L*1000:.0f} mm")
    print(f"横移動の効率  : {K_STRAFE:.2f}")
    print(f"エンコーダ分解能: {RPM_PER_COUNT:.2f} rpm/count")
    print()
    print("  rpm |   速度")
    print(" -----+---------")
    for r in (MIN_RPM, MIN_APPROACH_RPM, MAX_APPROACH_RPM, MAX_RPM_NAV, MAX_RPM_HW):
        print(f"  {r:3d} | {rpm_to_mps(r):.3f} m/s")
    print()
    print(f"★最終進入 {MIN_APPROACH_RPM} rpm は分解能 {RPM_PER_COUNT:.2f} rpm の "
          f"{RPM_PER_COUNT/MIN_APPROACH_RPM*100:.0f}% にあたります★")
