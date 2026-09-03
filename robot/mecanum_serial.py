"""
Arduino 2枚との USB シリアル通信（ライブラリ）

このファイルは「送る・受け取る」だけを担当します。
何を送るかを決めるのは pi_controller.py（手動）や、将来の ROS2 ノード（自動）です。

仕様の出どころ: simulink/arduino/README_raspberrypi.md（v2）  ★食い違ったら向こうが正★

  指令   7バイト  [0xA5][0x5A][連番][動作][速度下位][速度上位][検算]  20〜50ms ごと
  報告  24バイト  [0x5A][0xA5][連番][前輪u][前輪rpm×10][後輪u][後輪rpm×10]
                            [ax][ay][az][gx][gy][gz]                    10回/秒
        （v1 は 12バイトでした。先頭 12バイトの並びは同じで、末尾に IMU の 6 値が増えています）

★守らなければならない6つの決まり（README_raspberrypi.md §7）と、このファイルの対応★

  1. 20〜50ms ごとに送り続ける  → _sender_loop が独立したスレッドで SEND_PERIOD ごと（既定 40ms = 25Hz）に回す
                                   ★20ms にすると Arduino の取り出し（Ts ごとに 1 パケット）と同じ速さになり、
                                     取りこぼしが溜まって片側だけ遅れる。robot_params.SEND_PERIOD の注記を参照★
  2. 連番を必ず1ずつ増やす      → 送信のたびに seq を +1
  3. 検算に 0x5A を混ぜる       → build_packet の CHK_SEED
  4. 両方の基板に同じものを送る → 1つのパケットを2つのポートへ書く
  5. 2枚とも USB を挿す         → コードでは守れません（README.md §7「安全」）
  6. 終了時は停止指令を送る     → close() が動作0を5回送ってから切断

★v2 で増えた「暴走防止」（README_raspberrypi.md §6.2）★
  PWM を振り切っているのに目標の向きに回らない状態が 1秒 続くと、Arduino はその車輪を止めて
  ラッチします（u=0・rpm=0 のまま、指令に応答しなくなる）。解除は停止指令（動作=0）だけです。
  → stalled_wheels() で疑わしい車輪を検出できます。
"""

from __future__ import annotations

import math
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import serial
import serial.tools.list_ports

import robot_params as P


# =====================================================================
#  定数（★Arduino 側と必ず一致させること★ README_raspberrypi.md §12）
# =====================================================================

CMD_HEADER = (0xA5, 0x5A)     # 指令のヘッダ
TLM_HEADER = (0x5A, 0xA5)     # 報告のヘッダ（指令とは逆順なので区別できる）
CHK_SEED = 0x5A               # ★これを混ぜないと全0が「停止指令」として通り、振動する★
TLM_LEN = 24                  # ヘッダ込みの1回ぶん（★v2。v1 は 12 でした★）
TLM_FIELDS = (TLM_LEN - 2) // 2   # ヘッダの後ろの int16 の個数 = 11
TLM_RPM_SCALE = 10            # rpm はこの倍率の整数で送られてくる
TLM_ACC_SCALE = 1000          # 加速度は mg で来る  → 1000 で割ると g
TLM_GYR_SCALE = 10            # 角速度は 0.1dps で来る → 10 で割ると dps

# 暴走防止ラッチの検出（Arduino 側は STALL_SEC = 1.0 s で止める）
STALL_FRAMES = 5              # u=0 かつ rpm=0 がこの回数（10Hz なので 0.5 s）続いたら疑う
STALL_GRACE_SEC = 0.5         # 動き出し直後は u がまだ 0 のことがあるので、この間は見ない

# 動作番号
STOP, FORWARD, BACKWARD, LEFT, RIGHT, TURN_LEFT, TURN_RIGHT = range(7)

MOTION_NAME = {
    STOP: "停止", FORWARD: "前進", BACKWARD: "後退",
    LEFT: "左横", RIGHT: "右横", TURN_LEFT: "左回転", TURN_RIGHT: "右回転",
}


# =====================================================================
#  パケットの組み立て
# =====================================================================

def build_packet(seq: int, motion: int, rpm: float) -> bytes:
    """指令パケット（7バイト）を作る。mecanum_packet.m と同じ形。"""
    rpm = max(0, min(int(round(rpm)), P.MAX_RPM_HW))
    seq &= 0xFF
    motion &= 0xFF
    lo = rpm & 0xFF
    hi = (rpm >> 8) & 0xFF
    chk = seq ^ motion ^ lo ^ hi ^ CHK_SEED
    return bytes([CMD_HEADER[0], CMD_HEADER[1], seq, motion, lo, hi, chk])


class FrameMismatch(ValueError):
    """
    本文の途中に次のヘッダが見えた = フレーム長が合っていない。
    ★Arduino に v1（12バイト）のモデルが書き込まれたままのときにこうなります★
    """


def read_frame(ser: serial.Serial):
    """
    報告（24バイト）を1つ読む。読めなければ None。

    戻り値: 11 個の int16 のタプル
      (連番, 前輪u, 前輪rpm×10, 後輪u, 後輪rpm×10, ax, ay, az, gx, gy, gz)
      単位の換算は telemetry_from_frame() がやります。

    ★readline() は使えません★
      データの中にバイト値 0x0A（改行コード）が現れるため、途中で切れます。
      必ずヘッダを探してからバイト数で読むこと（README_raspberrypi.md §4.3）。

    ★v1 の Arduino（12バイト）につなぐと FrameMismatch を投げます★
      12バイトのフレームを 24バイトとして読むと、本文の 10バイト目に次のフレームの
      ヘッダ 5A A5 が現れます。v2 なら本文のその位置は加速度X（±4000mg）なので、
      5A A5（= -23206）にはなり得ません。これでフレーム長の食い違いを検出します。
    """
    state = 0
    while True:
        b = ser.read(1)
        if not b:
            return None                       # タイムアウト
        v = b[0]
        if state == 0:
            if v == TLM_HEADER[0]:
                state = 1
        else:
            if v == TLM_HEADER[1]:
                body = ser.read(TLM_LEN - 2)
                if len(body) != TLM_LEN - 2:
                    return None
                if body[10] == TLM_HEADER[0] and body[11] == TLM_HEADER[1]:
                    raise FrameMismatch(
                        "テレメトリの長さが合いません。Arduino が v1（12バイト）のままです")
                # 連番, 前輪u, 前輪rpm×10, 後輪u, 後輪rpm×10, ax, ay, az, gx, gy, gz
                return struct.unpack(f"<{TLM_FIELDS}h", body)
            # ずれていた。いま読んだバイトが次の候補かもしれない
            state = 1 if v == TLM_HEADER[0] else 0


# =====================================================================
#  テレメトリ 1サンプル
# =====================================================================

@dataclass
class Telemetry:
    t: float          # 受信時刻 [s]
    seq: int
    u_front: int      # 前輪の PWM 指令 (-255〜255)
    rpm_front: float  # 前輪の実測回転数（★フィルタ前の生の値。ギザギザが正常★）
    u_rear: int
    rpm_rear: float
    # ★v2 で追加★ 基板内蔵の IMU（LSM6DS3）。軸は +X=前 / +Y=左 / +Z=上（Arduino 側でそろえ済み）
    #   ・左右の基板で別々に届く。加速度は旋回中に遠心加速度を拾うので左右で違って正常
    #   ・角速度は取り付け位置に依存しない。旋回の速さは gyro[2]（Z）を見る
    #   ・地磁気は無いので方位は取れない。積分した角度はドリフトする（README_raspberrypi.md §5）
    acc: tuple = (0.0, 0.0, 0.0)     # [g]   静止・水平なら (0, 0, +1.0) 前後
    gyro: tuple = (0.0, 0.0, 0.0)    # [dps] 左回転（反時計）で gyro[2] > 0


def telemetry_from_frame(t: float, frame) -> Telemetry:
    """read_frame() が返した 11 個の int16 を、単位を直して Telemetry にする。"""
    seq, uF, rpmF10, uR, rpmR10, ax, ay, az, gx, gy, gz = frame
    return Telemetry(
        t, seq,
        uF, rpmF10 / TLM_RPM_SCALE,
        uR, rpmR10 / TLM_RPM_SCALE,
        acc=(ax / TLM_ACC_SCALE, ay / TLM_ACC_SCALE, az / TLM_ACC_SCALE),
        gyro=(gx / TLM_GYR_SCALE, gy / TLM_GYR_SCALE, gz / TLM_GYR_SCALE),
    )


# =====================================================================
#  ポートを探す
# =====================================================================

def find_ports(left: str | None = None, right: str | None = None,
               wait_sec: float = 30.0, verbose: bool = True):
    """
    左右のポートを決める。

    ★起動直後は /dev/serial/by-id/ がまだ空のことがあります★
      Arduino はラズパイからの USB 給電で動くため、見つかるまでリトライします
      （README_raspberrypi.md §3）。

    優先順位:
      1. 引数で明示された値（--left / --right）
      2. udev で作った固定名 /dev/mecanum_left, /dev/mecanum_right   ← 推奨
      3. Arduino らしきポートを自動検出（2つ見つかったときだけ。順番は保証されない）
    """
    deadline = time.time() + wait_sec
    announced = False

    while True:
        if left and right:
            return left, right

        # 2. udev の固定名
        import os
        if os.path.exists("/dev/mecanum_left") and os.path.exists("/dev/mecanum_right"):
            return left or "/dev/mecanum_left", right or "/dev/mecanum_right"

        # 3. 自動検出（Arduino のベンダID = 0x2341）
        cands = [p.device for p in serial.tools.list_ports.comports()
                 if (p.vid == 0x2341) or ("Arduino" in (p.description or ""))]
        cands.sort()
        if len(cands) >= 2:
            if verbose:
                print(f"[warn] 自動検出しました: 左={cands[0]} 右={cands[1]}")
                print("       ★左右が逆かもしれません。--identify で確認してください★")
                print("       恒久対策は udev ルール（README.md 参照）")
            return left or cands[0], right or cands[1]

        if time.time() > deadline:
            raise RuntimeError(
                f"ポートが見つかりません（{wait_sec:.0f}秒待ちました）。見つかった候補: {cands}\n"
                "  ・ケーブルを挿し直す\n"
                "  ・Arduino IDE / MATLAB がポートを掴んでいないか確認する\n"
                "  ・--left COM3 --right COM4 のように明示指定する"
            )

        if verbose and not announced:
            print("ポートが見つかるまで待っています…（USB給電なので起動直後は数秒かかります）")
            announced = True
        time.sleep(1.0)


# =====================================================================
#  本体
# =====================================================================

class MecanumLink:
    """
    2枚の Arduino に同じ指令を送り続け、報告を受け取り続ける。

        link = MecanumLink(left_port, right_port)
        link.open()
        link.set_command(FORWARD, 30)
        ...
        link.close()          # ← 停止指令を送ってから切断してくれる

    with 文でも使えます（推奨。例外が出ても必ず停止指令が送られます）。
    """

    def __init__(self, left_port: str, right_port: str,
                 cmd_timeout: float | None = None, history: int = 2000):
        """
        cmd_timeout : この秒数 set_command が呼ばれないと動作0を送る。
                      ROS2 ノードでは 0.2 を推奨。
                      手動操作ツールでは None（＝最後の指令を保持）。
        """
        self.left_port = left_port
        self.right_port = right_port
        self.cmd_timeout = cmd_timeout

        self._ser_l: serial.Serial | None = None
        self._ser_r: serial.Serial | None = None

        self._lock = threading.Lock()
        self._motion = STOP
        self._rpm = 0.0
        self._last_cmd_t = time.time()
        self._seq = 0
        self._running = False
        self._threads: list[threading.Thread] = []

        self.tlm_left: Telemetry | None = None
        self.tlm_right: Telemetry | None = None
        self.hist_left: deque[Telemetry] = deque(maxlen=history)
        self.hist_right: deque[Telemetry] = deque(maxlen=history)

        self.sent_count = 0
        self.recv_count = 0
        self.bad_frames = 0          # 長さの合わないフレームの回数（v1 の Arduino が疑わしい）

        # 暴走防止ラッチの検出用（stalled_wheels）
        self._moving_since: float | None = None      # 0 でない指令を出し始めた時刻
        self._zero_frames = {"FL": 0, "RL": 0, "FR": 0, "RR": 0}   # u=0 かつ rpm=0 の連続回数

    # ---------------- 開始・終了 ----------------

    def open(self):
        self._ser_l = serial.Serial(self.left_port, P.BAUDRATE, timeout=0.1)
        self._ser_r = serial.Serial(self.right_port, P.BAUDRATE, timeout=0.1)
        time.sleep(0.2)                     # ポートが落ち着くのを待つ
        self._ser_l.reset_input_buffer()
        self._ser_r.reset_input_buffer()

        self._running = True
        self._threads = [
            threading.Thread(target=self._sender_loop, daemon=True, name="sender"),
            threading.Thread(target=self._reader_loop, args=("left",), daemon=True),
            threading.Thread(target=self._reader_loop, args=("right",), daemon=True),
        ]
        for t in self._threads:
            t.start()
        return self

    def close(self):
        """★決まり6★ 停止指令を送ってから切断する。"""
        self._running = False
        for t in self._threads:
            t.join(timeout=1.0)

        # いきなり切ってもウォッチドッグで0.5秒後に止まるが、
        # その 0.5秒ぶん（上限0.25m/sなら約12cm）進んでしまう。
        try:
            for _ in range(5):
                pkt = build_packet(self._seq, STOP, 0)
                for ser in (self._ser_l, self._ser_r):
                    if ser and ser.is_open:
                        ser.write(pkt)
                self._seq = (self._seq + 1) & 0xFF
                time.sleep(0.02)
            time.sleep(0.05)
        finally:
            for ser in (self._ser_l, self._ser_r):
                if ser and ser.is_open:
                    ser.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------------- 指令 ----------------

    def set_command(self, motion: int, rpm: float):
        """次に送る指令を差し替える。実際の送信は SEND_PERIOD ごとのスレッドが行う。"""
        with self._lock:
            self._motion = motion
            self._rpm = rpm
            self._last_cmd_t = time.time()
            if motion == STOP or rpm <= 0:
                self._moving_since = None            # 停止指令 = 暴走防止のラッチも解除される
            elif self._moving_since is None:
                self._moving_since = self._last_cmd_t

    def stop(self):
        self.set_command(STOP, 0)

    def get_command(self):
        with self._lock:
            return self._motion, self._rpm

    # ---------------- 内部ループ ----------------

    def _sender_loop(self):
        """
        ★決まり1★ 20〜50ms ごとに送り続ける。

        ここを「上位から指令が来たら送る」実装にしてはいけません。
        Nav2 の cmd_vel は 20Hz 程度でしか来ないため、
        0.5秒のウォッチドッグに引っかかってガクガクします。
        """
        next_t = time.perf_counter()
        while self._running:
            with self._lock:
                motion, rpm = self._motion, self._rpm
                stale = (self.cmd_timeout is not None
                         and time.time() - self._last_cmd_t > self.cmd_timeout)
                if stale:
                    self._moving_since = None
            if stale:
                motion, rpm = STOP, 0        # 上位が黙ったら止める（暴走防止のラッチも解除される）

            pkt = build_packet(self._seq, motion, rpm)
            for ser in (self._ser_l, self._ser_r):     # ★決まり4★ 両方に同じものを
                try:
                    if ser and ser.is_open:
                        ser.write(pkt)
                except serial.SerialException:
                    pass
            self._seq = (self._seq + 1) & 0xFF          # ★決まり2★ 必ず1増やす
            self.sent_count += 1

            next_t += P.SEND_PERIOD
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()            # 遅れたら追いかけずに仕切り直す

    def _reader_loop(self, side: str):
        ser = self._ser_l if side == "left" else self._ser_r
        while self._running:
            try:
                fr = read_frame(ser)
            except FrameMismatch:
                self.bad_frames += 1     # v1 の Arduino。上位（pi_controller / node）が警告を出す
                continue
            except serial.SerialException:
                break
            if fr is None:
                continue
            t = telemetry_from_frame(time.time(), fr)
            if side == "left":
                self.tlm_left = t
                self.hist_left.append(t)
            else:
                self.tlm_right = t
                self.hist_right.append(t)
            self._update_stall(side, t)
            self.recv_count += 1

    # ---------------- odom のもと ----------------

    def body_velocity(self):
        """
        4輪の実測rpmから車体速度 (vx, vy, wz) を出す（メカナムの順運動学）。

        各車輪の周速 u_i [m/s]（符号は「+ = 車体が前へ」でそろえ済み）として

            vx = ( u_FL + u_RL + u_FR + u_RR) / 4
            vy = ( u_FL - u_RL - u_FR + u_RR) / 4 × K_STRAFE
            wz = (-u_FL - u_RL + u_FR + u_RR) / (4 × L)

        ★符号は build_mecanum_models.m の動作表と一致させること★
          上の vy の符号は「左横移動 = FL+1, RL-1, FR-1, RR+1」から導いています。
          実機で横移動が逆で動作表を直したら、この式の vy も一緒に直してください。
          片方だけ直すと odom が逆を向きます。

        戻り値: (vx, vy, wz) [m/s, m/s, rad/s]。データが揃っていなければ None。
        """
        L, R = self.tlm_left, self.tlm_right
        if L is None or R is None:
            return None
        u_FL = P.rpm_to_mps(L.rpm_front)
        u_RL = P.rpm_to_mps(L.rpm_rear)
        u_FR = P.rpm_to_mps(R.rpm_front)
        u_RR = P.rpm_to_mps(R.rpm_rear)

        vx = (u_FL + u_RL + u_FR + u_RR) / 4.0
        vy = (u_FL - u_RL - u_FR + u_RR) / 4.0 * P.K_STRAFE
        wz = (-u_FL - u_RL + u_FR + u_RR) / (4.0 * P.WHEEL_GEOM_L)
        return vx, vy, wz

    def yaw_rate(self):
        """
        IMU の角速度 Z（左右2枚の平均）[rad/s]。データが揃っていなければ None。

        角速度は基板の取り付け位置に依存しないので、旋回の速さは
        車輪 rpm から出す wz（ローラーが滑る）より信用できます。左右はほぼ同じ値のはずです。
        ★電源投入直後の数百 ms は値が落ち着きません。起動時は機体を静止させておくこと★
        """
        L, R = self.tlm_left, self.tlm_right
        if L is None or R is None:
            return None
        return math.radians((L.gyro[2] + R.gyro[2]) / 2.0)

    # ---------------- 暴走防止ラッチの検出（v2） ----------------

    def _update_stall(self, side: str, t: Telemetry):
        """u=0 かつ rpm=0 の連続回数を車輪ごとに数える（受信スレッドから呼ぶ）。"""
        if side == "left":
            wheels = (("FL", t.u_front, t.rpm_front), ("RL", t.u_rear, t.rpm_rear))
        else:
            wheels = (("FR", t.u_front, t.rpm_front), ("RR", t.u_rear, t.rpm_rear))
        for name, u, rpm in wheels:
            if abs(u) < 1 and abs(rpm) < 1.0:
                self._zero_frames[name] += 1
            else:
                self._zero_frames[name] = 0

    def stalled_wheels(self, now: float | None = None) -> list:
        """
        暴走防止ラッチが働いたと疑われる車輪の名前（"FL" など）のリスト。なければ空。

        Arduino は「PWM を振り切っているのに目標の向きに 5rpm も出ない」状態が 1秒 続くと
        その車輪を止め、★停止指令（動作=0）を送るまで解除しません★（README_raspberrypi.md §6.2）。
        止まった車輪は u も rpm も 0 になります。ここではそれを
        「0 でない指令を出し続けているのに、u=0 かつ rpm=0 が STALL_FRAMES 回続いた」で検出します。
        7 種類の動作はどれも 4輪すべてを回すので、指令中に u=0 の車輪は本来ありません。

        ★解除して同じ指令を出し直すだけだと、また止まります★
          配線・障害物・タイヤの拘束を確認してから動かし直してください。
        """
        now = time.time() if now is None else now
        since = self._moving_since
        if since is None or now - since < STALL_GRACE_SEC:
            return []
        return [w for w, n in self._zero_frames.items() if n >= STALL_FRAMES]


# =====================================================================
#  cmd_vel → 7動作 の量子化（A1）
# =====================================================================

class Quantizer:
    """
    Nav2 が出す連続指令 (vx, vy, wz) を、7動作 + 単一rpm に落とす。

    ★そのまま最大成分を選ぶとパタパタ切り替わります★
      2つの軸が拮抗すると毎周期で動作が入れ替わり、実機がぶるぶるします。
      そこで「いま選んでいる軸を有利に評価する（ヒステリシス）」と
      「切り替えたら最低 hold_sec は保つ」の2つを入れています。

    v1 では横移動を封印する（use_vy=False）ことを推奨します。
    前進・後退・左右回転の4択になり、挙動がぐっと素直になります。
    """

    def __init__(self, use_vy: bool = False, hysteresis: float = 1.5,
                 hold_sec: float = 0.2, max_rpm: float = P.MAX_RPM_NAV):
        self.use_vy = use_vy
        self.hysteresis = hysteresis
        self.hold_sec = hold_sec
        self.max_rpm = max_rpm
        self._motion = STOP
        self._changed_t = 0.0

    def __call__(self, vx: float, vy: float, wz: float, now: float | None = None):
        now = time.time() if now is None else now

        # 3つの軸を「必要な車輪rpm」に換算してから比べる（単位をそろえるため）
        cands = [
            (P.mps_to_rpm(abs(vx)), FORWARD if vx > 0 else BACKWARD),
            (P.mps_to_rpm(abs(wz) * P.WHEEL_GEOM_L), TURN_LEFT if wz > 0 else TURN_RIGHT),
        ]
        if self.use_vy:
            cands.append((P.mps_to_rpm(abs(vy)) / P.K_STRAFE, LEFT if vy > 0 else RIGHT))

        by_motion = {m: r for r, m in cands}

        # いま選んでいる軸を有利に評価する
        #   軸番号: 0=停止 1=前後 2=横 3=回転
        def axis_of(m):
            return (m + 1) // 2

        def score(item):
            rpm, motion = item
            same_axis = self._motion != STOP and axis_of(motion) == axis_of(self._motion)
            return rpm * (self.hysteresis if same_axis else 1.0)

        rpm, motion = max(cands, key=score)

        if rpm < P.MIN_RPM:
            motion, rpm = STOP, 0.0

        # 切り替え直後は最低 hold_sec は保つ（停止への切り替えだけは即時＝安全側）
        if motion != self._motion:
            if motion != STOP and (now - self._changed_t) < self.hold_sec:
                motion = self._motion
                rpm = by_motion.get(motion, 0.0)      # 保持する動作の rpm を採り直す
                if rpm < P.MIN_RPM:
                    motion, rpm = STOP, 0.0
            else:
                self._motion = motion
                self._changed_t = now

        return motion, min(rpm, self.max_rpm)
