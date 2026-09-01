"""
bookshelf_bridge.py ― アプリ（Supabase）とロボをつなぐブリッジ  ★Issue #8 / #9★

アプリとロボは直接やり取りせず、Supabase のテーブルを介してつながります
（robot/README.md「疎結合の原則」）。このファイルがその接ぎ目です。

    アプリ ──insert──> robot_calls ──> 【ここ】 ──> Nav2 ゴール
    アプリ <──select── robot_status <── 【ここ】 <── Nav2 の結果

────────────────────────────────────────────────────────────
★なぜポーリングが主で、Realtime が従なのか★
────────────────────────────────────────────────────────────
会場の大学 Wi-Fi は不通前提という裁定が出ています（発表会場デモ運用方針書 A）。
WebSocket 一本に頼ると、切れたときに呼出を取りこぼします。
そこで

    ・1秒ごとのポーリング  … いつでも確実に拾える。これだけでも運用可能
    ・Realtime（任意）      … 来たら即座に反応する「速さのための飾り」

という二段構えにしています。Realtime が動かなくても業務は止まりません。
（Realtime のクライアント API は supabase-py のバージョンで変わるため、
  動かない場合は --realtime を付けずにポーリングだけで運用してください）

────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────

  # まずはこれ（DBを書き換えない・ログを出すだけ）★Issue #8 はここまで★
  python bookshelf_bridge.py

  # Realtime も試す
  python bookshelf_bridge.py --realtime

  # 実際にステータスを書き戻す（走行はまだダミー：--simulate 秒で「到着」扱い）
  python bookshelf_bridge.py --live --simulate 5

環境変数（.env でも可）:
  SUPABASE_URL=https://xxxx.supabase.co
  SUPABASE_ANON_KEY=eyJ...

★service_role キーは絶対にラズパイに置かないこと。anon キーで足ります★
（RLS は select/insert/update を全員に許可。delete はポリシー未定義＝誰も消せない）
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import time
from datetime import datetime, timezone

import requests


# =====================================================================
#  設定の読み込み
# =====================================================================

def load_env(path: str = ".env"):
    """.env があれば読む（python-dotenv を入れなくて済むように最小限だけ）。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
#  Supabase への読み書き（PostgREST）
# =====================================================================

class Supa:
    """REST だけを使う最小クライアント。ライブラリのバージョン差に振り回されない。"""

    def __init__(self, url: str, key: str, timeout: float = 5.0):
        self.base = url.rstrip("/") + "/rest/v1"
        self.timeout = timeout
        self.h = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def select(self, table: str, query: str = "") -> list[dict]:
        r = requests.get(f"{self.base}/{table}?{query}", headers=self.h, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def patch(self, table: str, query: str, body: dict) -> list[dict]:
        # ★updated_at は自動更新されません★
        #   schema.sql にトリガがないため、UPDATE のたびにこちらで入れます。
        #   sql/01_realtime_と_updated_at.sql を流してトリガを付けた場合も、
        #   ここで入れておいて害はありません（同じ値で上書きされるだけ）。
        body = dict(body)
        body.setdefault("updated_at", now_iso())
        r = requests.patch(f"{self.base}/{table}?{query}", headers=self.h,
                           json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


# =====================================================================
#  席座標 → Nav2 ゴール  ★Issue #9★
# =====================================================================

def goal_from_stop_point(sp: dict) -> dict:
    """
    stop_points の1行を map フレームの姿勢に変換する。

    theta [rad] → クォータニオン は z 軸まわりの回転なので
        z = sin(theta / 2),  w = cos(theta / 2)
    （席座標の採り方は「発表会場デモ運用方針書」の席座標採取手順を参照）
    """
    th = float(sp.get("theta") or 0.0)
    return {
        "frame_id": "map",
        "x": float(sp["x"]),
        "y": float(sp["y"]),
        "z": 0.0,
        "qz": math.sin(th / 2.0),
        "qw": math.cos(th / 2.0),
        "theta": th,
        "label": sp.get("label"),
    }


class LoggingNavigator:
    """走行のかわりにログを出すだけの偽ナビ。Issue #8 の段階ではこれを使う。"""

    def __init__(self, simulate_sec: float = 0.0):
        self.simulate_sec = simulate_sec

    def go(self, goal: dict) -> bool:
        print(f"    [nav] ゴール送出（ふり）: ({goal['x']:.3f}, {goal['y']:.3f}) "
              f"theta={goal['theta']:.3f}rad  qz={goal['qz']:.4f} qw={goal['qw']:.4f}")
        if self.simulate_sec > 0:
            print(f"    [nav] {self.simulate_sec:.0f} 秒走ったことにします…")
            time.sleep(self.simulate_sec)
        return True


class Nav2Navigator:
    """
    本物のナビ。★Issue #9 / タスク B3★

    ROS2 と Nav2 が動いているラズパイの上でだけ使えます。
    import はここでしか行わないので、ROS2 が無い PC でも
    このファイル自体は今までどおり動きます（LoggingNavigator を使う限り）。

    ★事前に必要なもの★
      ・Nav2 が起動していること（map_server + amcl + controller + planner）
      ・stop_points の座標が map フレームで採ってあること
      ・odom→base_link の TF が出ていること（mecanum_node.py か EKF）
    """

    def __init__(self, timeout: float = 180.0, verbose: bool = True):
        import rclpy
        from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
        from geometry_msgs.msg import PoseStamped

        self._PoseStamped = PoseStamped
        self._TaskResult = TaskResult
        self.timeout = timeout
        self.verbose = verbose

        if not rclpy.ok():
            rclpy.init()
        self.nav = BasicNavigator()
        print("    [nav] Nav2 の起動を待っています…")
        self.nav.waitUntilNav2Active()      # amcl と bt_navigator が立つまで待つ
        print("    [nav] Nav2 が有効になりました")

    def go(self, goal: dict) -> bool:
        p = self._PoseStamped()
        p.header.frame_id = goal["frame_id"]
        p.header.stamp = self.nav.get_clock().now().to_msg()
        p.pose.position.x = goal["x"]
        p.pose.position.y = goal["y"]
        p.pose.orientation.z = goal["qz"]
        p.pose.orientation.w = goal["qw"]

        print(f"    [nav] ゴール送出: ({goal['x']:.3f}, {goal['y']:.3f}) "
              f"theta={goal['theta']:.3f}rad")
        self.nav.goToPose(p)

        t0 = time.time()
        last_log = 0.0
        while not self.nav.isTaskComplete():
            # ★タイムアウトは必ず入れること★
            #   人に囲まれて詰まったまま永久に待つと、キューが止まります。
            if time.time() - t0 > self.timeout:
                print(f"    [nav] {self.timeout:.0f} 秒を超えたので中止します")
                self.nav.cancelTask()
                return False

            fb = self.nav.getFeedback()
            if self.verbose and fb is not None and time.time() - last_log > 2.0:
                last_log = time.time()
                try:
                    print(f"    [nav] 残り {fb.distance_remaining:.2f} m")
                except AttributeError:
                    pass
            time.sleep(0.2)

        result = self.nav.getResult()
        if result == self._TaskResult.SUCCEEDED:
            print("    [nav] 到着しました")
            return True
        name = getattr(result, "name", str(result))
        print(f"    [nav] 失敗しました（{name}）")
        return False


# =====================================================================
#  ブリッジ本体
# =====================================================================

class Bridge:
    def __init__(self, supa: Supa, navigator, live: bool, arrive_hold: float):
        self.supa = supa
        self.nav = navigator
        self.live = live
        self.arrive_hold = arrive_hold
        self.seen: set[str] = set()      # Realtime とポーリングの二重処理を防ぐ
        self.current: dict | None = None
        self.busy = False

    # ---------------- 状態の読み書き ----------------

    def set_call(self, call_id: str, status: str):
        print(f"    [db] robot_calls {call_id[:8]}… → {status}"
              + ("" if self.live else "（dry-run なので書き込みません）"))
        if self.live:
            self.supa.patch("robot_calls", f"id=eq.{call_id}", {"status": status})

    def set_status(self, state: str, call_id: str | None = None):
        print(f"    [db] robot_status → {state}"
              + ("" if self.live else "（dry-run なので書き込みません）"))
        if self.live:
            self.supa.patch("robot_status", "id=eq.1",
                            {"state": state, "current_call_id": call_id})

    # ---------------- 1件を処理する ----------------

    def handle(self, call: dict):
        cid = call["id"]
        if cid in self.seen or self.busy:
            return
        self.seen.add(cid)
        self.busy = True
        self.current = call
        try:
            print(f"\n[呼出] id={cid[:8]}…  book={str(call['book_id'])[:8]}…  "
                  f"seat={call['seat_id']}  requested_by={call.get('requested_by')}")

            rows = self.supa.select("stop_points", f"id=eq.{call['seat_id']}&select=*")
            if not rows:
                print(f"    [err] stop_points に id={call['seat_id']} がありません → canceled")
                self.set_call(cid, "canceled")
                return
            goal = goal_from_stop_point(rows[0])
            print(f"    [席] {goal['label']}  ({goal['x']:.3f}, {goal['y']:.3f}) "
                  f"theta={goal['theta']:.3f} rad")

            self.set_call(cid, "moving")
            self.set_status("moving", cid)

            ok = self.nav.go(goal)

            if ok:
                self.set_call(cid, "arrived")
                self.set_status("arrived", cid)
                # TODO: ★未決事項★ 設計メモ §11 #8
                #   arrived → done を「アプリの受取ボタン」で進めるのか、
                #   一定時間で自動にするのか。S-2 の画面設計と関わるので、
                #   アプリ担当が動き出す前に決めること。いまは時間で進めています。
                time.sleep(self.arrive_hold)
                self.set_call(cid, "done")
                self.set_status("returning", cid)
                self.set_status("idle", None)
            else:
                self.set_call(cid, "canceled")
                self.set_status("idle", None)
        finally:
            self.busy = False
            self.current = None

    # ---------------- ポーリング ----------------

    def poll_once(self):
        if self.busy:
            return
        rows = self.supa.select(
            "robot_calls",
            "status=eq.queued&order=created_at.asc&limit=1&select=*")   # FIFO
        if rows:
            self.handle(rows[0])

    # ---------------- 後始末 ----------------

    def shutdown(self):
        if self.live and self.current:
            print("\n[終了] 処理中の呼出を canceled にして idle へ戻します。")
            try:
                self.set_call(self.current["id"], "canceled")
                self.set_status("idle", None)
            except Exception as e:
                print(f"    [err] 後始末に失敗: {e}")


# =====================================================================
#  Realtime（任意）
# =====================================================================

def try_realtime(url: str, key: str, on_insert):
    """
    supabase-py で robot_calls の INSERT を購読する。失敗しても止めない。

    ★事前に一度だけ SQL Editor で実行が必要★
        alter publication supabase_realtime add table robot_calls;
      これをやらないと、購読できているのに何も飛んできません
      （sql/01_realtime_と_updated_at.sql に入れてあります）

    ★Realtime は非同期クライアント専用★（supabase>=2.6 の同期クライアントは
      NotImplementedError を投げる）。そのため asyncio のループを
      デーモンスレッドで回し、コールバックだけメイン側の on_insert に渡します。
    """
    try:
        import asyncio
        import threading

        from supabase import acreate_client        # 非同期クライアント（supabase>=2.6）

        subscribed = threading.Event()

        def extract_record(payload) -> dict:
            # ライブラリの版によって dict / オブジェクトの両方があり得るので両対応
            if isinstance(payload, dict):
                return payload.get("data", {}).get("record", payload)
            data = getattr(payload, "data", None)
            record = getattr(data, "record", None)
            return record if isinstance(record, dict) else {}

        async def run():
            client = await acreate_client(url, key)
            ch = client.channel("bridge_robot_calls")
            ch.on_postgres_changes(
                event="INSERT", schema="public", table="robot_calls",
                callback=lambda payload: on_insert(extract_record(payload)),
            )
            await ch.subscribe()
            subscribed.set()
            while True:                             # 購読を維持するためスレッドを生かし続ける
                await asyncio.sleep(3600)

        threading.Thread(target=lambda: asyncio.run(run()),
                         daemon=True, name="realtime").start()

        if subscribed.wait(timeout=10.0):
            print("[realtime] 購読しました。")
        else:
            print("[realtime] 10秒待っても購読が確立しませんでした。"
                  "ポーリングだけで動作を続けます。")
        return None
    except Exception as e:
        print(f"[realtime] 使えませんでした（{type(e).__name__}: {e}）")
        print("           ポーリング（1秒）だけで動作を続けます。実用上は十分です。")
        return None


# =====================================================================
#  main
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description="Supabase ↔ ロボ ブリッジ")
    ap.add_argument("--live", action="store_true",
                    help="実際に DB のステータスを書き換える（既定はログのみ）")
    ap.add_argument("--realtime", action="store_true", help="Realtime 購読も試す")
    ap.add_argument("--interval", type=float, default=1.0, help="ポーリング間隔 [s]")
    ap.add_argument("--simulate", type=float, default=0.0,
                    help="走行を N 秒かかったことにする（Nav2 未接続のとき）")
    ap.add_argument("--arrive-hold", type=float, default=3.0,
                    help="arrived → done までの待ち時間 [s]（未決事項）")
    ap.add_argument("--nav2", action="store_true",
                    help="本物の Nav2 にゴールを送る（ラズパイ上でのみ）")
    ap.add_argument("--nav-timeout", type=float, default=180.0,
                    help="1回の走行の打ち切り時間 [s]")
    args = ap.parse_args()

    load_env()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL と SUPABASE_ANON_KEY を .env か環境変数に入れてください。\n"
                 "（.env.example を .env にコピーして値を入れる）")
    if "service_role" in key or len(key) > 500:
        sys.exit("★service_role キーのように見えます。ラズパイには anon キーだけを置いてください★")

    supa = Supa(url, key)

    if args.nav2:
        try:
            navigator = Nav2Navigator(timeout=args.nav_timeout)
        except ImportError as e:
            sys.exit(f"Nav2 に接続できません（{e}）。\n"
                     "  ラズパイ上で ROS2 環境を source してから実行してください。\n"
                     "  PC で試すときは --nav2 を外してください。")
    else:
        navigator = LoggingNavigator(args.simulate)

    bridge = Bridge(supa, navigator, args.live, args.arrive_hold)

    print(f"接続先 : {url}")
    print(f"モード : {'★LIVE（DBを書き換えます）★' if args.live else 'dry-run（ログのみ）'}")
    print(f"走行   : {'Nav2（実走）' if args.nav2 else 'ログのみ（ダミー）'}")

    # 起動時の疎通確認
    try:
        st = supa.select("robot_status", "id=eq.1&select=*")
        print(f"現在の robot_status: {st[0] if st else '（行がありません）'}")
        q = supa.select("robot_calls", "status=eq.queued&select=id&limit=100")
        print(f"未処理の呼出: {len(q)} 件")
    except Exception as e:
        sys.exit(f"Supabase に接続できません: {e}")

    if args.realtime:
        try_realtime(url, key, bridge.handle)

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))

    print(f"\n{args.interval:.0f}秒ごとに robot_calls を見ています。Ctrl-C で終了。")
    try:
        while not stop["flag"]:
            try:
                bridge.poll_once()
            except requests.RequestException as e:
                print(f"[warn] 通信エラー（続行します）: {e}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.shutdown()
        print("終了しました。")


if __name__ == "__main__":
    main()
