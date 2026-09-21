"""
manual_control.py ― 管理者画面の「ラジコンモード」を受け取ってロボを動かす

    管理者画面(/admin) ──update──> robot_manual <──manual_poll()── 【ここ】──> Arduino

アプリとロボは直接やり取りせず、Supabase のテーブルを介してつながります
（robot/README.md「疎結合の原則」）。手動操作もその原則どおり DB 経由です。

────────────────────────────────────────────────────────────
★安全設計（デッドマン）★
────────────────────────────────────────────────────────────
・アプリはボタンを押している間だけ指令を更新し続ける（0.5秒ごと）
・このスクリプトは manual_poll() が返す cmd_age（指令からの経過秒。
  ★DBの時計で計算済み★なのでクロックずれの影響なし）を見て、
  DEADMAN_SEC を超えたら motion=0（停止）に落とす
・ブラウザが閉じられても、Wi-Fi が切れても、最大 DEADMAN_SEC + ポーリング
  1周期で止まる。さらに実機側は Arduino の 0.5秒ウォッチドッグが最後の砦

────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────

  # まずはこれ（実機なし・受信した指令をログに出すだけ）
  python manual_control.py

  # 実機を動かす（ラズパイ上・Arduino 2枚接続時のみ）
  python manual_control.py --serial

  # 実機を mecanum_node.py 経由で動かす（/cmd_vel に出す）★地図づくりで車輪オドメトリを使うとき★
  #   先に mecanum_node.py を起動しておくこと。横移動（左横・右横）は mecanum_node.py が封印しているので動きません
  #   （前が重く、横移動すると一緒に回ってしまうため。2026-09-21）。前進・後退・その場回転で操作します
  python3 manual_control.py --ros

環境変数（.env でも可）: SUPABASE_URL / SUPABASE_ANON_KEY

★ bookshelf_bridge.py と同時に実機へつながないこと ★
  どちらも Arduino へ指令を送るため、取り合いになります。
  手動テスト中はブリッジを止める（またはブリッジを --live なしで動かす）こと。
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

import requests

# 動作番号（simulink の動作表 = mecanum_serial.py = robot_manual.motion と同じ）
MOTION_NAME = {
    0: "停止", 1: "前進", 2: "後退",
    3: "左横", 4: "右横", 5: "左回転", 6: "右回転",
}

DEADMAN_SEC = 1.2          # 指令がこの秒数より古ければ停止（アプリは0.5秒ごとに更新）
POLL_FAST = 0.2            # 手動モードON中のポーリング間隔 [s]
POLL_SLOW = 1.0            # 手動モードOFF中のポーリング間隔 [s]


def load_env(path: str = ".env"):
    """.env があれば読む（bookshelf_bridge.py と同じ最小実装）。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# =====================================================================
#  ドライバ（実際にロボを動かす部分の差し替え口）
# =====================================================================

class SimDriver:
    """実機のかわりに何もしない。指令の中身は main 側が変化時にログを出す。"""

    def apply(self, motion: int, rpm: int):
        pass

    def stop(self):
        pass

    def close(self):
        print("    [sim] 停止して終了")


class RosDriver:
    """
    /cmd_vel に Twist を出して、mecanum_node.py 経由で実機を動かす。★ラズパイ上でのみ★

    --serial は Arduino を直接つかむので、mecanum_node.py と同時には動かせません。すると地図づくりの
    あいだ車輪オドメトリが出せず、SLAM は LiDAR の形の重ね合わせだけが頼りになって、壁が二重・三重に
    写りやすくなります（2026-09-21）。こちらは指令を mecanum_node.py に渡すので、Arduino へ行く指令は
    同じまま、/odom と TF（odom→base_footprint）が出続けます。

    ・指令は 20 Hz で出し続ける（mecanum_node.py は 0.2 秒指令が来ないと止める。ポーリングは 0.2 秒ごとなので
      受け取ったときだけ出すのでは間に合わない）
    ・止まっているあいだは何も出さない（停止に変わった直後の 1 秒だけ 0 を出す）。
      Nav2 が同じ /cmd_vel を使うので、出し続けると Nav2 の指令を打ち消してしまうため
    ・このスクリプトが落ちても、mecanum_node.py が 0.2 秒で停止、Arduino が 0.5 秒で停止する
    """

    RATE_HZ = 20.0
    ZERO_TAIL_SEC = 1.0

    def __init__(self):
        try:
            import rclpy
            from geometry_msgs.msg import Twist
            from mecanum_serial import twist_for_motion
        except ImportError as e:
            sys.exit(f"ROS 2 か mecanum_serial.py が読み込めません（{e}）。\n"
                     "  ラズパイ上で ROS 2 の環境を source してから実行してください。")
        import threading
        self._rclpy, self._Twist, self._twist_for_motion = rclpy, Twist, twist_for_motion
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node("manual_control")
        self.pub = self.node.create_publisher(Twist, "cmd_vel", 10)
        self._cmd = (0, 0)
        self._last_moving = 0.0
        self._alive = True
        self._lock = threading.Lock()
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        print("[ros] /cmd_vel に出します。mecanum_node.py が動いていることを確かめてください。")
        print("      ※横移動（左横・右横）は mecanum_node.py が封印しているので動きません。"
              "前進・後退・その場回転で操作してください。")

    def _publish(self, motion: int, rpm: int):
        vx, vy, wz = self._twist_for_motion(motion, rpm)
        msg = self._Twist()
        msg.linear.x, msg.linear.y, msg.angular.z = float(vx), float(vy), float(wz)
        self.pub.publish(msg)

    def _loop(self):
        while self._alive:
            with self._lock:
                motion, rpm = self._cmd
            now = time.time()
            if motion != 0:
                self._last_moving = now
                self._publish(motion, rpm)
            elif now - self._last_moving < self.ZERO_TAIL_SEC:
                self._publish(0, 0)
            time.sleep(1.0 / self.RATE_HZ)

    def apply(self, motion: int, rpm: int):
        with self._lock:
            self._cmd = (int(motion), int(rpm))

    def stop(self):
        self.apply(0, 0)

    def close(self):
        self._alive = False
        self._th.join(timeout=1.0)
        # Ctrl+C では rclpy が先に通信を閉じてしまうことがあり、そのあとの publish は例外になる。
        # 閉じていても危なくはない（mecanum_node.py は指令が 0.2 秒来なければ止める）ので、静かに終わる
        try:
            for _ in range(5):             # 停止を確実に届けてから終わる
                if not self._rclpy.ok():
                    break
                self._publish(0, 0)
                time.sleep(0.05)
            self.node.destroy_node()
            if self._rclpy.ok():
                self._rclpy.shutdown()
            print("    [ros] 停止を送って終了")
        except Exception:
            print("    [ros] 終了（停止は mecanum_node.py の 0.2 秒タイムアウトに任せます）")


class SerialDriver:
    """
    実機（Arduino 2枚）を MecanumLink で動かす。★ラズパイ上でのみ★

    import はここでしか行わないので、mecanum_serial.py が無い PC でも
    このファイル自体は今までどおり動きます（SimDriver を使う限り）。

    cmd_timeout=0.5 を渡しているため、このスクリプトごと落ちても
    MecanumLink の送信スレッドが 0.5 秒で停止指令に切り替えます（多重の安全網）。
    """

    def __init__(self, left: str | None, right: str | None):
        try:
            from mecanum_serial import MecanumLink, find_ports
        except ImportError:
            sys.exit("mecanum_serial.py が見つかりません。\n"
                     "  実機ツール一式（mecanum_serial.py / robot_params.py）を\n"
                     "  このフォルダに置いてから --serial を使ってください。\n"
                     "  PC での連携確認は --serial を外せばできます。")
        l, r = find_ports(left, right)
        self.link = MecanumLink(l, r, cmd_timeout=0.5)
        self.link.open()
        print(f"[serial] 接続しました: 左={l} 右={r}")

    def apply(self, motion: int, rpm: int):
        self.link.set_command(motion, rpm)

    def stop(self):
        self.link.stop()

    def close(self):
        self.link.close()      # 停止指令を送ってから切断してくれる


# =====================================================================
#  main
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description="管理者画面の手動操作を受け取る")
    ap.add_argument("--serial", action="store_true",
                    help="実機（Arduino）を動かす（既定はログのみのシミュレータ）")
    ap.add_argument("--ros", action="store_true",
                    help="/cmd_vel に出して mecanum_node.py 経由で動かす（地図づくりで車輪オドメトリを使うとき）")
    ap.add_argument("--left", help="左基板のポート（--serial 時。省略で自動）")
    ap.add_argument("--right", help="右基板のポート（--serial 時。省略で自動）")
    args = ap.parse_args()
    if args.serial and args.ros:
        sys.exit("--serial と --ros は同時に使えません（--ros のときは mecanum_node.py が Arduino をつかみます）。")

    load_env()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL と SUPABASE_ANON_KEY を .env か環境変数に入れてください。")

    rpc = url.rstrip("/") + "/rest/v1/rpc/manual_poll"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}

    driver = (SerialDriver(args.left, args.right) if args.serial
              else RosDriver() if args.ros else SimDriver())

    print(f"接続先 : {url}")
    print("モード : " + ("★実機（Arduinoを動かします）★" if args.serial
                        else "★実機（/cmd_vel → mecanum_node.py 経由）★" if args.ros else "シミュレータ（ログのみ）"))
    print(f"デッドマン: 指令が {DEADMAN_SEC} 秒更新されなければ停止")
    print("Ctrl-C で終了。\n")

    stop_flag = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop_flag.update(flag=True))

    last = (None, None, None)      # (enabled, motion, rpm) 前回値。変化したときだけログを出す
    deadman_logged = False

    try:
        while not stop_flag["flag"]:
            try:
                r = requests.post(rpc, headers=headers, json={}, timeout=5)
                r.raise_for_status()
                rows = r.json()
            except requests.RequestException as e:
                # 通信が切れたら安全側＝停止して、つながるまでリトライ
                print(f"[warn] 通信エラー → 停止して再試行します: {e}")
                driver.stop()
                last = (None, None, None)
                time.sleep(POLL_SLOW)
                continue

            if not rows:
                print("[err] robot_manual に行がありません。sql/02_manual_control.sql を実行してください。")
                time.sleep(POLL_SLOW)
                continue

            row = rows[0]
            enabled, motion, rpm = row["enabled"], row["motion"], row["rpm"]
            cmd_age = float(row["cmd_age"])

            # ★デッドマン★ 動いている最中に指令が古くなったら停止
            if enabled and motion != 0 and cmd_age > DEADMAN_SEC:
                if not deadman_logged:
                    print(f"[deadman] 指令が {cmd_age:.1f} 秒更新されていません → 停止")
                    deadman_logged = True
                motion, rpm = 0, 0
            else:
                deadman_logged = False

            if not enabled:
                motion, rpm = 0, 0

            # ★毎周期 apply する★
            #   MecanumLink は cmd_timeout=0.5 で「指令が途切れたら停止」するため、
            #   変化したときだけ渡す実装だと押しっぱなしで止まってしまう。
            #   ログだけ変化時に出す。
            driver.apply(motion, rpm)

            cur = (enabled, motion, rpm)
            if cur != last:
                mode = "ON " if enabled else "OFF"
                print(f"[手動 {mode}] {MOTION_NAME.get(motion, '?')} rpm={rpm}"
                      f"  (指令から {cmd_age:.2f} 秒)")
                last = cur

            time.sleep(POLL_FAST if enabled else POLL_SLOW)
    except KeyboardInterrupt:
        pass
    finally:
        driver.close()
        print("終了しました。")


if __name__ == "__main__":
    main()
