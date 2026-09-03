"""
pi_controller.py ― ラズパイ（や PC）から実機を動かす手動ツール

★これが mecanum_packet.m のコメントに出てくる pi_controller.py です★
MATLAB の pc_controller.m と同じことを Python でやります。ROS2 は不要。
「ラズパイから実機が動く」ことを最初に確かめるための道具です。

────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────

  # 手動操作（W/S=前後, A/D=横, Q/E=回転, X/Space=停止, +/-=速度, ESC=終了）
  python pi_controller.py

  # ★どちらの基板が左か確かめる★（車輪を浮かせてから）
  python pi_controller.py --identify

  # 低速での測定のばらつきを見る（★最終進入速度の検証★）
  python pi_controller.py --lowspeed 19 --seconds 10

  # 何rpmから実際に回り始めるかを探す（robot_params.MIN_RPM を決める）
  python pi_controller.py --sweep

  # ポートを明示する（Windows など）
  python pi_controller.py --left COM3 --right COM4

★車輪を浮かせた状態から始めてください★
"""

from __future__ import annotations

import argparse
import signal
import statistics
import sys
import time

import serial

import robot_params as P
from mecanum_serial import (
    STOP, FORWARD, BACKWARD, LEFT, RIGHT, TURN_LEFT, TURN_RIGHT,
    MOTION_NAME, MecanumLink, build_packet, find_ports,
)


# =====================================================================
#  キー入力（Windows / Linux 両対応）
# =====================================================================

class KeyReader:
    """1文字ずつ、押されたときだけ読む。押されていなければ None を返す。"""

    def __enter__(self):
        if sys.platform == "win32":
            import msvcrt
            self._msvcrt = msvcrt
            self._posix = False
        else:
            import termios, tty
            self._termios, self._tty = termios, tty
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._posix = True
        return self

    def __exit__(self, *exc):
        if self._posix:
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)
        return False

    def get(self):
        if not self._posix:
            if self._msvcrt.kbhit():
                ch = self._msvcrt.getch()
                try:
                    return ch.decode("utf-8", "ignore")
                except Exception:
                    return None
            return None
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None


# =====================================================================
#  ① 手動操作
# =====================================================================

KEYMAP = {
    "w": FORWARD, "s": BACKWARD, "a": LEFT, "d": RIGHT,
    "q": TURN_LEFT, "e": TURN_RIGHT, "x": STOP, " ": STOP,
}


def teleop(link: MecanumLink, max_rpm: int):
    rpm = min(20, max_rpm)
    motion = STOP

    print()
    print("  W/S 前進・後退   A/D 左横・右横   Q/E 左回転・右回転")
    print("  X または Space 停止      +/- 速度変更      ESC または Ctrl-C 終了")
    print(f"  速度上限 {max_rpm} rpm（{P.rpm_to_mps(max_rpm):.2f} m/s）")
    print()

    with KeyReader() as keys:
        last_draw = 0.0
        while True:
            ch = keys.get()
            if ch:
                if ch in ("\x1b", "\x03"):       # ESC / Ctrl-C
                    break
                low = ch.lower()
                if low in KEYMAP:
                    motion = KEYMAP[low]
                elif ch in ("+", "="):
                    rpm = min(rpm + 5, max_rpm)
                elif ch in ("-", "_"):
                    rpm = max(rpm - 5, 0)
                link.set_command(motion, rpm if motion != STOP else 0)

            now = time.time()
            if now - last_draw > 0.15:
                last_draw = now
                sys.stdout.write("\r" + _status_line(link, motion, rpm) + "   ")
                sys.stdout.flush()
            time.sleep(0.01)

    print()


def _status_line(link: MecanumLink, motion: int, rpm: float) -> str:
    L, R = link.tlm_left, link.tlm_right
    if L is None or R is None:
        return f"[{MOTION_NAME[motion]:>4s} {rpm:5.1f}rpm]  テレメトリ待ち…"
    vel = link.body_velocity()
    v = "" if vel is None else f"  vx={vel[0]:+.2f} vy={vel[1]:+.2f} wz={vel[2]:+.2f}"
    # IMU の角速度 Z（左/右）。Q/E で回すと符号つきで動き、左右がほぼ同じ値なら正常
    gz = f"  gz={L.gyro[2]:+5.0f}/{R.gyro[2]:+5.0f}dps"
    # ★暴走防止（v2）★ 指令中なのに u=0・rpm=0 が続く車輪 → X（停止）で解除してから原因を確認
    stalled = link.stalled_wheels()
    warn = f"  ★暴走防止? {','.join(stalled)} → X で解除★" if stalled else ""
    return (f"[{MOTION_NAME[motion]:>4s} {rpm:5.1f}rpm]  "
            f"FL{L.rpm_front:+6.1f} RL{L.rpm_rear:+6.1f} "
            f"FR{R.rpm_front:+6.1f} RR{R.rpm_rear:+6.1f}  "
            f"u=({L.u_front:+4d},{L.u_rear:+4d},{R.u_front:+4d},{R.u_rear:+4d}){v}{gz}{warn}")


# =====================================================================
#  ② どちらが左かを確かめる
# =====================================================================

def identify(port_a: str, port_b: str, rpm: int = 20, sec: float = 3.0):
    """
    片方のポートにだけ低速で前進を送り、目視でどちらの車輪が回るか確かめる。

    ★simulink/arduino/README_raspberrypi.md §3 が求めている手順です★
      「COM/デバイス名は挿し直すと変わるので、番号を覚えさせないでください」

    確認できたら、udev ルールで固定名を作ってください（README.md 参照）。
    片方だけに送るとその2輪しか回りません（＝床の上ならその場で回ります）。
    """
    print()
    print("★★★ 車輪を浮かせてから実行してください ★★★")
    print("    片方の基板にだけ指令を送るため、床の上だとその場で回ります。")
    input("    準備できたら Enter を押してください… ")

    for port in (port_a, port_b):
        print(f"\n--- {port} だけに前進 {rpm} rpm を {sec:.0f} 秒送ります ---")
        ser = serial.Serial(port, P.BAUDRATE, timeout=0.1)
        seq = 0
        t_end = time.time() + sec
        try:
            while time.time() < t_end:
                ser.write(build_packet(seq, FORWARD, rpm))
                seq = (seq + 1) & 0xFF
                time.sleep(P.SEND_PERIOD)
        finally:
            for _ in range(5):
                ser.write(build_packet(seq, STOP, 0))
                seq = (seq + 1) & 0xFF
                time.sleep(0.02)
            time.sleep(0.05)
            ser.close()
        ans = input(f"    {port} で回ったのはどちら側でしたか？ [l=左 / r=右 / s=わからない] ")
        if ans.strip().lower().startswith("l"):
            print(f"    → {port} が【左】です")
        elif ans.strip().lower().startswith("r"):
            print(f"    → {port} が【右】です")

    print("\n次は udev ルールで固定名を作ってください（README.md の「左右の固定」）。")
    print("シリアル番号の調べ方:  udevadm info -a -n /dev/ttyACM0 | grep '{serial}' | head -1")


# =====================================================================
#  ③ 低速での測定ばらつき（★最終進入速度の検証★）
# =====================================================================

def lowspeed(link: MecanumLink, rpm: int, sec: float):
    """
    指定rpmで一定時間回し、テレメトリのばらつきを見る。

    ★なぜこれを測るのか★
      エンコーダは 1カウント ≒ 2.01 rpm の段差でしか測れません。
      最終進入の 19 rpm は、その約10%にあたります。
      つまり「いちばん精度が欲しい場面が、いちばん測定が粗い領域」です。
      ここで実際にどれだけ揺れるかを見ておかないと、±10cm 停止の見通しが立ちません。
    """
    print(f"\n--- 前進 {rpm} rpm を {sec:.0f} 秒 --- （{P.rpm_to_mps(rpm):.3f} m/s 相当）")
    print("★車輪を浮かせるか、十分な走行距離を確保してください★")
    input("準備できたら Enter… ")

    link.set_command(FORWARD, rpm)
    time.sleep(1.5)                                   # 立ち上がりを捨てる
    link.hist_left.clear()
    link.hist_right.clear()
    t_end = time.time() + sec
    while time.time() < t_end:
        L, R = link.tlm_left, link.tlm_right
        if L and R:
            sys.stdout.write(f"\r  FL{L.rpm_front:+6.1f} RL{L.rpm_rear:+6.1f} "
                             f"FR{R.rpm_front:+6.1f} RR{R.rpm_rear:+6.1f}   ")
            sys.stdout.flush()
        time.sleep(0.1)
    link.stop()
    print()

    data = {
        "FL": [t.rpm_front for t in link.hist_left],
        "RL": [t.rpm_rear for t in link.hist_left],
        "FR": [t.rpm_front for t in link.hist_right],
        "RR": [t.rpm_rear for t in link.hist_right],
    }
    q = P.RPM_PER_COUNT

    print(f"\n  指令 {rpm} rpm / エンコーダ分解能 {q:.2f} rpm")
    print("  車輪 |  平均   標準偏差    最小    最大  | 誤差   ばらつき/分解能")
    print("  -----+----------------------------------+---------------------")
    for name, xs in data.items():
        if len(xs) < 3:
            print(f"   {name}  | データ不足（{len(xs)}点）")
            continue
        m, sd = statistics.mean(xs), statistics.pstdev(xs)
        print(f"   {name}  | {m:6.2f}  {sd:6.2f}  {min(xs):6.1f}  {max(xs):6.1f}  |"
              f" {m - rpm:+5.2f}   {sd / q:5.2f} 倍")

    print("\n  読み方:")
    print("   ・平均が指令から大きくずれる → 車輪半径の採寸ずれ、または負荷で失速")
    print("   ・ばらつき/分解能 が 1 前後  → 正常（量子化そのもの）")
    print("   ・ばらつき/分解能 が 2 以上  → 制御が量子化を増幅している。")
    print("     arduino/README.md §6「ゲインを手で調整する」で Kp を下げる")
    print("     （または ../simulink/FRIT/frit_run で決め直す）")


# =====================================================================
#  ④ 何rpmから回り始めるか（MIN_RPM を決める）
# =====================================================================

def sweep(link: MecanumLink, lo: int = 5, hi: int = 45, step: int = 5, sec: float = 3.0):
    """robot_params.MIN_RPM（これ未満は0に丸める下限）を実測で決めるためのツール。"""
    print("\n--- rpm スイープ --- ★車輪を浮かせてください★")
    input("準備できたら Enter… ")

    rows = []
    for rpm in range(lo, hi + 1, step):
        link.set_command(FORWARD, rpm)
        time.sleep(1.0)
        link.hist_left.clear()
        link.hist_right.clear()
        time.sleep(sec)
        xs = ([t.rpm_front for t in link.hist_left] + [t.rpm_rear for t in link.hist_left]
              + [t.rpm_front for t in link.hist_right] + [t.rpm_rear for t in link.hist_right])
        meas = statistics.mean(xs) if xs else 0.0
        rows.append((rpm, meas))
        print(f"  指令 {rpm:3d} rpm → 実測 {meas:6.2f} rpm  ({meas/rpm*100 if rpm else 0:5.1f}%)")
    link.stop()

    good = [r for r, m in rows if r > 0 and m / r > 0.8]
    print()
    if good:
        print(f"★ 指令の8割以上出ている最小の rpm は {min(good)} でした ★")
        print(f"  robot_params.py の MIN_RPM をこの値（か少し上）にしてください。")
    else:
        print("★ どの rpm でも指令の8割に届きませんでした ★")
        print("  車輪が浮いているか、電池電圧、Ki（arduino/README.md §6「ゲインを手で調整する」）を確認してください。")
        print("  ★指令中なのに u=0・rpm=0 の車輪があれば暴走防止のラッチです（README_raspberrypi.md §6.2）★")


# =====================================================================
#  main
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description="メカナムロボット 手動操作・診断ツール")
    ap.add_argument("--left", help="左基板のポート（例: /dev/mecanum_left, COM3）")
    ap.add_argument("--right", help="右基板のポート")
    ap.add_argument("--max-rpm", type=int, default=60, help="手動操作の速度上限 [rpm]")
    ap.add_argument("--identify", action="store_true", help="どちらが左かを目視で確かめる")
    ap.add_argument("--lowspeed", type=int, metavar="RPM", help="低速でのばらつきを測る")
    ap.add_argument("--seconds", type=float, default=10.0, help="--lowspeed の測定時間 [s]")
    ap.add_argument("--sweep", action="store_true", help="何rpmから回り始めるかを探す")
    args = ap.parse_args()

    left, right = find_ports(args.left, args.right)
    print(f"左基板: {left}\n右基板: {right}")

    if args.identify:
        identify(left, right)
        return

    link = MecanumLink(left, right, cmd_timeout=None)   # 手動操作なので最後の指令を保持

    # ★決まり6★ Ctrl-C でも kill でも、必ず停止指令を送ってから切断する
    def bye(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, bye)

    try:
        with link:
            time.sleep(0.5)
            if link.tlm_left is None or link.tlm_right is None:
                print("[warn] テレメトリが来ていません。")
                print("       Simulink のモデルを［ビルド、展開、起動］したか確認してください。")
                if link.bad_frames:
                    print(f"       ★フレーム長が合いません（{link.bad_frames} 回）。"
                          "Arduino が v1（12バイト）のままです。simulink/arduino/ の v2 を書き込んでください★")
            if args.lowspeed:
                lowspeed(link, args.lowspeed, args.seconds)
            elif args.sweep:
                sweep(link)
            else:
                teleop(link, args.max_rpm)
    except KeyboardInterrupt:
        print("\n停止します。")
    finally:
        print(f"送信 {link.sent_count} 回 / 受信 {link.recv_count} 回"
              + (f" / 長さ不一致 {link.bad_frames} 回 ★Arduino が v1 のまま？★" if link.bad_frames else ""))


if __name__ == "__main__":
    main()
