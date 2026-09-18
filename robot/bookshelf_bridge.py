"""
bookshelf_bridge.py ― アプリ（Supabase）とロボをつなぐブリッジ  ★Issue #8 / #9 ／ 段階3★

アプリとロボは直接やり取りせず、Supabase のテーブルを介してつながります
（robot/README.md「疎結合の原則」）。このファイルがその接ぎ目です。

    アプリ ──insert──> robot_calls ──> 【ここ】 ──> Nav2 ゴール
    アプリ <──select── robot_status <── 【ここ】 <── Nav2 の結果・AMCL の現在地

────────────────────────────────────────────────────────────
1 回の呼出で起きること（段階3・2026-09-18〜）
────────────────────────────────────────────────────────────

    robot_calls : queued ──> moving ─────────────────────> arrived ──(アプリ「本を取得した」)──> done
    robot_status: idle ──> localizing ──> moving ──> arrived ──> returning ──> idle
                          自己位置推定    席へ走行    受取待ち     本棚の場所へ帰る

  ・自己位置推定 … ロボが「本棚の場所」（stop_points の kind='home'・id=0）に居る前提で、
    その座標を AMCL の初期位置として送り、/amcl_pose が返るのを待ちます。
    帰還に失敗したときなど「本棚の場所に居ない」ときは初期位置を送らず、AMCL の追跡をそのまま使います。
    RViz の 2D Pose Estimate で人が与える運用にしたいときは --no-localize を付けます。
  ・帰還 … 受取（done）のあと、本棚の場所（home）へ Nav2 で戻ってから idle にします（--no-return-home で無効）。
  ・現在地 … 走行中は 1 秒ごとに AMCL の推定位置と「残り x m」を robot_status に書き、
    アプリの管理者画面（/admin/pins）の地図に出します。
  ・上の 3 つは sql/03_pins_home_pose.sql を実行してあることが前提です。未適用でも動きますが、
    localizing と現在地は書かず（列が無いため）、ホームは id=0 の行を探します。

────────────────────────────────────────────────────────────
★なぜポーリングが主で、Realtime が従なのか★
────────────────────────────────────────────────────────────
会場の大学 Wi-Fi は不通前提という裁定が出ています（発表会場デモ運用方針書 A）。
WebSocket 一本に頼ると、切れたときに呼出を取りこぼします。
そこで

    ・1秒ごとのポーリング  … いつでも確実に拾える。これだけでも運用可能
    ・Realtime（任意）      … 来たら即座に反応する「速さのための飾り」

という二段構えにしています。Realtime が動かなくても業務は止まりません。

────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────

  # まずはこれ（DBを書き換えない・ログを出すだけ）
  python bookshelf_bridge.py

  # 走行のかわりに 5 秒待って「到着」扱い（段階2）。自己位置推定・帰還も「ふり」で再現する
  python bookshelf_bridge.py --live --simulate 5

  # 本物の Nav2 で走る（段階3・Pi 上で ROS 2 環境を source してから）
  python3 bookshelf_bridge.py --live --nav2

環境変数（.env でも可）:
  SUPABASE_URL=https://xxxx.supabase.co
  SUPABASE_ANON_KEY=eyJ...

★service_role キーは絶対にラズパイに置かないこと。anon キーで足ります★
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


def probe_schema_v3(supa) -> bool:
    """
    sql/03_pins_home_pose.sql が適用済みか（robot_status に pose_x / detail があるか）。
    未適用なら False。列が無い状態で書くと 400 になるので、起動時に 1 回だけ調べておく。
    """
    try:
        supa.select("robot_status", "id=eq.1&select=pose_x,detail")
        return True
    except Exception:
        return False


def load_home(supa, home_id: int | None = None) -> dict | None:
    """
    「本棚の場所」（ホーム）の stop_points 行を返す。無ければ None。
      --home-id 指定 → その id
      指定なし        → kind='home' の行（sql/03 適用後）。kind 列が無ければ id=0 の行
    """
    if home_id is not None:
        rows = supa.select("stop_points", f"id=eq.{int(home_id)}&select=*")
    else:
        try:
            rows = supa.select("stop_points", "kind=eq.home&select=*&limit=1")
        except Exception:
            rows = supa.select("stop_points", "id=eq.0&select=*")
    return rows[0] if rows else None


# =====================================================================
#  席座標 → Nav2 ゴール  ★Issue #9★
# =====================================================================

def goal_from_stop_point(sp: dict) -> dict:
    """
    stop_points の1行を map フレームの姿勢に変換する。

    theta [rad] → クォータニオン は z 軸まわりの回転なので
        z = sin(theta / 2),  w = cos(theta / 2)
    （席座標の採り方は「発表会場デモ運用方針書」§5 と pin_tool.py を参照）
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


def yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
    """クォータニオン → ヨー角 [rad]（平面ロボなので z 軸まわりだけ取り出す）。"""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


# =====================================================================
#  ナビゲータ（走行の差し替え口）
#
#   localize(init_goal)      … 自己位置推定。init_goal があればそれを初期位置として与える。
#                              None なら「いまの推定をそのまま使う」。成功なら True
#   go(goal, on_progress)    … ゴールへ走る。1 秒ごとに on_progress(残り[m], 現在地) を呼ぶ。到着で True
#   current_pose()           … (x, y, theta) か None
#   cancel()                 … 走行中なら止める（Ctrl-C 時）
# =====================================================================

class LoggingNavigator:
    """走行のかわりにログを出すだけの偽ナビ。段階1〜2 ではこれを使う。

    --simulate N のときは N 秒かけてゴールへ「動いたふり」をし、現在地も直線補間で進める。
    自己位置推定の「ふり」も 1 秒入れるので、アプリ側で localizing 表示が確認できる。
    """

    def __init__(self, simulate_sec: float = 0.0, localize_sec: float | None = None,
                 start_pose: tuple[float, float, float] = (0.0, 0.0, 0.0)):
        self.simulate_sec = simulate_sec
        self.localize_sec = (1.0 if simulate_sec > 0 else 0.0) if localize_sec is None else localize_sec
        self.pose = start_pose

    def localize(self, init_goal: dict | None) -> bool:
        if init_goal is not None:
            self.pose = (init_goal["x"], init_goal["y"], init_goal["theta"])
            print(f"    [loc] 初期位置を与えたことにします: ({init_goal['x']:.3f}, {init_goal['y']:.3f}) "
                  f"theta={init_goal['theta']:.3f}rad")
        else:
            print("    [loc] 現在の推定位置を使います（ふり）")
        if self.localize_sec > 0:
            time.sleep(self.localize_sec)
        return True

    def go(self, goal: dict, on_progress=None) -> bool:
        print(f"    [nav] ゴール送出（ふり）: ({goal['x']:.3f}, {goal['y']:.3f}) "
              f"theta={goal['theta']:.3f}rad  qz={goal['qz']:.4f} qw={goal['qw']:.4f}")
        x0, y0, _ = self.pose
        dist = math.hypot(goal["x"] - x0, goal["y"] - y0)
        if self.simulate_sec > 0:
            print(f"    [nav] {self.simulate_sec:.0f} 秒走ったことにします…")
            steps = max(1, int(round(self.simulate_sec / 0.5)))
            for i in range(1, steps + 1):
                time.sleep(self.simulate_sec / steps)
                f = i / steps
                self.pose = (x0 + (goal["x"] - x0) * f, y0 + (goal["y"] - y0) * f, goal["theta"])
                if on_progress:
                    on_progress(dist * (1.0 - f), self.pose)
        else:
            self.pose = (goal["x"], goal["y"], goal["theta"])
        return True

    def current_pose(self):
        return self.pose

    def cancel(self):
        pass


class Nav2Navigator:
    """
    本物のナビ。★Issue #9 / タスク B3 ／ 段階3★

    ROS 2 と Nav2 が動いているラズパイの上でだけ使えます。
    import はここでしか行わないので、ROS 2 が無い PC でも
    このファイル自体は今までどおり動きます（LoggingNavigator を使う限り）。

    ★事前に必要なもの★
      ・Nav2 が起動していること（map_server + amcl + controller + planner + bt_navigator）
      ・stop_points の座標が map フレームで採ってあること（pin_tool.py）
      ・odom→base_link の TF が出ていること（mecanum_node.py か EKF）と base_link→laser の静的 TF

    ★初期位置について★
      nav2_simple_commander の waitUntilNav2Active() は使いません。Jazzy の実装は
      初期位置が未設定だと地図原点 (0,0,0) を勝手に送ってしまうためです
      （②完全ガイド E3 の注記）。初期位置は localize() で本棚の場所の座標を、
      共分散つきで /initialpose に送ります。
    """

    def __init__(self, timeout: float = 180.0, verbose: bool = True,
                 init_sigma_xy: float = 0.15, init_sigma_yaw: float = 0.17,
                 localize_timeout: float = 20.0, settle: float = 2.0):
        import rclpy
        from rclpy.qos import (QoSProfile, ReliabilityPolicy, DurabilityPolicy,
                               HistoryPolicy)
        from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
        from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped

        self._rclpy = rclpy
        self._PoseStamped = PoseStamped
        self._PoseWithCov = PoseWithCovarianceStamped
        self._TaskResult = TaskResult
        self.timeout = timeout
        self.verbose = verbose
        self.init_sigma_xy = init_sigma_xy
        self.init_sigma_yaw = init_sigma_yaw
        self.localize_timeout = localize_timeout
        self.settle = settle

        self._pose: tuple[float, float, float] | None = None
        self._pose_count = 0          # /amcl_pose を受け取った回数（初期位置が効いたかの判定に使う）

        if not rclpy.ok():
            rclpy.init()
        self.nav = BasicNavigator()

        # /amcl_pose は transient_local で最後の 1 件が残っている
        qos = QoSProfile(durability=DurabilityPolicy.TRANSIENT_LOCAL,
                         reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=1)
        self.nav.create_subscription(PoseWithCovarianceStamped, "amcl_pose", self._on_amcl, qos)
        self.pub_init = self.nav.create_publisher(PoseWithCovarianceStamped, "initialpose", 10)

        print("    [nav] Nav2 の起動を待っています…（amcl と bt_navigator が active になるまで）")
        try:
            self.nav._waitForNodeToActivate("amcl")
            self.nav._waitForNodeToActivate("bt_navigator")
        except AttributeError:
            # 版が変わって private メソッドが無くなった場合の逃げ道
            # （localizer='robot_localization' にすると初期位置の自動送信を通らない）
            self.nav.waitUntilNav2Active(localizer="robot_localization")
        print("    [nav] Nav2 が有効になりました")

    # ---------------- AMCL の現在地 ----------------

    def _on_amcl(self, msg):
        p = msg.pose.pose
        yaw = yaw_from_quat(p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w)
        self._pose = (float(p.position.x), float(p.position.y), yaw)
        self._pose_count += 1

    def current_pose(self):
        return self._pose

    def _spin(self, sec: float):
        self._rclpy.spin_once(self.nav, timeout_sec=sec)

    # ---------------- 自己位置推定 ----------------

    def _publish_initial_pose(self, goal: dict):
        msg = self._PoseWithCov()
        msg.header.frame_id = goal["frame_id"]
        msg.header.stamp = self.nav.get_clock().now().to_msg()
        msg.pose.pose.position.x = goal["x"]
        msg.pose.pose.position.y = goal["y"]
        msg.pose.pose.orientation.z = goal["qz"]
        msg.pose.pose.orientation.w = goal["qw"]
        cov = [0.0] * 36
        cov[0] = self.init_sigma_xy ** 2       # x
        cov[7] = self.init_sigma_xy ** 2       # y
        cov[35] = self.init_sigma_yaw ** 2     # yaw
        msg.pose.covariance = cov
        self.pub_init.publish(msg)

    def localize(self, init_goal: dict | None) -> bool:
        t0 = time.time()
        if init_goal is not None:
            before = self._pose_count
            print(f"    [loc] 初期位置を AMCL に与えます: ({init_goal['x']:.3f}, {init_goal['y']:.3f}) "
                  f"theta={init_goal['theta']:.3f}rad  σxy={self.init_sigma_xy:.2f}m σyaw={self.init_sigma_yaw:.2f}rad")
            last_pub = 0.0
            while self._pose_count == before:
                if time.time() - t0 > self.localize_timeout:
                    print(f"    [loc] {self.localize_timeout:.0f} 秒待っても /amcl_pose が来ません"
                          "（AMCL が /scan と TF を受け取れているか確認）")
                    return False
                if time.time() - last_pub > 3.0:      # 取りこぼし対策で 3 秒ごとに再送
                    self._publish_initial_pose(init_goal)
                    last_pub = time.time()
                self._spin(0.2)
            print("    [loc] AMCL が初期位置を受け付けました")
        else:
            print("    [loc] 初期位置は送らず、現在の推定位置を使います")
            while self._pose is None:
                if time.time() - t0 > self.localize_timeout:
                    print("    [loc] /amcl_pose がまだ一度も来ていません（RViz の 2D Pose Estimate で初期位置を与えてください）")
                    return False
                self._spin(0.2)

        # 粒子が落ち着くまで少し待つ（この間も /amcl_pose を受け続ける）
        t1 = time.time()
        while time.time() - t1 < self.settle:
            self._spin(0.2)
        if self._pose is not None:
            x, y, th = self._pose
            print(f"    [loc] 推定位置: ({x:.3f}, {y:.3f}) theta={th:.3f}rad")
        return True

    # ---------------- 走行 ----------------

    def go(self, goal: dict, on_progress=None) -> bool:
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
        last_tick = 0.0
        while not self.nav.isTaskComplete():      # 内部で 0.1 秒 spin する
            # ★タイムアウトは必ず入れること★
            #   人に囲まれて詰まったまま永久に待つと、キューが止まります。
            if time.time() - t0 > self.timeout:
                print(f"    [nav] {self.timeout:.0f} 秒を超えたので中止します")
                self.nav.cancelTask()
                return False

            fb = self.nav.getFeedback()
            remaining = None
            if fb is not None:
                try:
                    remaining = float(fb.distance_remaining)
                except AttributeError:
                    pass
            now = time.time()
            if now - last_tick > 1.0:
                last_tick = now
                if self.verbose and remaining is not None:
                    print(f"    [nav] 残り {remaining:.2f} m")
                if on_progress:
                    on_progress(remaining, self._pose)
            time.sleep(0.1)

        result = self.nav.getResult()
        if result == self._TaskResult.SUCCEEDED:
            print("    [nav] 到着しました")
            return True
        name = getattr(result, "name", str(result))
        print(f"    [nav] 失敗しました（{name}）")
        return False

    def cancel(self):
        try:
            self.nav.cancelTask()
        except Exception:
            pass


# =====================================================================
#  ブリッジ本体
# =====================================================================

class Bridge:
    def __init__(self, supa: Supa, navigator, live: bool, arrive_hold: float,
                 arrive_timeout: float = 0.0, home: dict | None = None,
                 return_home: bool = True, localize: bool = True,
                 pose_interval: float = 1.0, schema_v3: bool | None = None):
        self.supa = supa
        self.nav = navigator
        self.live = live
        self.arrive_hold = arrive_hold
        self.arrive_timeout = arrive_timeout
        self.home = home
        self.return_home = return_home
        self.localize = localize
        self.pose_interval = pose_interval
        self.v3 = probe_schema_v3(supa) if schema_v3 is None else schema_v3
        self.seen: set[str] = set()      # Realtime とポーリングの二重処理を防ぐ
        self.current: dict | None = None
        self.busy = False
        self._state_now: str = "idle"    # 走行中の進捗書き込み（_progress）が使う「いまの state」
        self._call_now: str | None = None
        # ★起動時はロボが「本棚の場所」に置かれている前提★
        #   ここが True のときだけ、呼出のたびにホーム座標を AMCL の初期位置として送る。
        #   席へ動き出したら False、ホームへ帰り着いたら True に戻す。
        self.at_home = True

    # ---------------- 状態の読み書き ----------------

    def set_call(self, call_id: str, status: str):
        print(f"    [db] robot_calls {call_id[:8]}… → {status}"
              + ("" if self.live else "（dry-run なので書き込みません）"))
        if self.live:
            self.supa.patch("robot_calls", f"id=eq.{call_id}", {"status": status})

    def set_status(self, state: str, call_id: str | None = None, detail: str | None = None,
                   pose=None, quiet: bool = False):
        body: dict = {"state": state, "current_call_id": call_id}
        if self.v3:
            body["detail"] = detail
            if pose is not None:
                body["pose_x"], body["pose_y"], body["pose_theta"] = (float(v) for v in pose)
                body["pose_at"] = now_iso()
        elif state == "localizing":
            # 旧スキーマ（sql/03 未適用）では CHECK 制約で弾かれるので書かない
            print("    [db] robot_status → localizing（sql/03 未適用のため書きません）")
            return
        if not quiet:
            print(f"    [db] robot_status → {state}"
                  + (f"  「{detail}」" if detail else "")
                  + ("" if self.live else "（dry-run なので書き込みません）"))
        if self.live:
            self.supa.patch("robot_status", "id=eq.1", body)

    def _progress(self, text: str):
        """走行中に 1 秒ごとに呼ばれるコールバックを作る（現在地と残り距離を robot_status へ）。"""
        last = {"t": 0.0}

        def cb(remaining, pose):
            now = time.time()
            if now - last["t"] < self.pose_interval:
                return
            last["t"] = now
            detail = f"{text}（残り {remaining:.1f} m）" if remaining is not None else text
            try:
                self.set_status(self._state_now, self._call_now, detail=detail, pose=pose, quiet=True)
            except Exception as e:                       # 通信の一時失敗で走行を止めない
                print(f"    [warn] 現在地の書き込みに失敗（続行します）: {e}")

        return cb

    # ---------------- 1件を処理する ----------------

    def handle(self, call: dict):
        cid = call["id"]
        if cid in self.seen or self.busy:
            return
        self.seen.add(cid)
        self.busy = True
        self.current = call
        try:
            self._process(call)
        except (KeyboardInterrupt, SystemExit):
            raise                     # Ctrl-C。current は残して shutdown() に後始末させる
        except Exception:
            self.current = None       # 通信エラー等。main 側が警告して次のポーリングへ進む
            raise
        else:
            self.current = None
        finally:
            self.busy = False

    def _process(self, call: dict):
        cid = call["id"]
        print(f"\n[呼出] id={cid[:8]}…  book={str(call['book_id'])[:8]}…  "
              f"seat={call['seat_id']}  requested_by={call.get('requested_by')}")

        seat_id = int(call["seat_id"])
        if seat_id == 0:
            print("    [err] seat_id=0 は本棚の場所（ホーム）です。呼出先にはできません → canceled")
            self.set_call(cid, "canceled")
            return
        rows = self.supa.select("stop_points", f"id=eq.{seat_id}&select=*")
        if not rows or rows[0].get("kind") == "home":
            print(f"    [err] stop_points に席 id={seat_id} がありません → canceled")
            self.set_call(cid, "canceled")
            return
        goal = goal_from_stop_point(rows[0])
        label = goal["label"] or f"席{seat_id}"
        print(f"    [席] {label}  ({goal['x']:.3f}, {goal['y']:.3f}) theta={goal['theta']:.3f} rad")

        self.set_call(cid, "moving")

        # ---- ① 自己位置推定 ----
        if self.localize:
            use_home = self.at_home and self.home is not None
            where = "本棚の場所を初期位置に" if use_home else "現在の推定位置から継続"
            self._state_now, self._call_now = "localizing", cid
            self.set_status("localizing", cid, detail=f"自己位置推定中（{where}）")
            init = goal_from_stop_point(self.home) if use_home else None
            if not self.nav.localize(init):
                self.set_call(cid, "canceled")
                self.set_status("idle", None,
                                detail="自己位置推定に失敗（AMCL の応答なし）。LiDAR・TF・Nav2 を確認してください",
                                pose=self.nav.current_pose())
                return

        # ---- ② 席へ走行 ----
        self._state_now, self._call_now = "moving", cid
        self.set_status("moving", cid, detail=f"{label} へ移動中", pose=self.nav.current_pose())
        self.at_home = False
        ok = self.nav.go(goal, on_progress=self._progress(f"{label} へ移動中"))
        if not ok:
            self.set_call(cid, "canceled")
            self.set_status("idle", None, detail=f"{label} への走行に失敗（タイムアウトか中止）",
                            pose=self.nav.current_pose())
            return

        # ---- ③ 到着 → 受取待ち ----
        self.set_call(cid, "arrived")
        self.set_status("arrived", cid, detail=f"{label} に到着。アプリの「本を取得した」を待っています",
                        pose=self.nav.current_pose())
        self.wait_for_receipt(cid)

        # ---- ④ 本棚の場所へ帰る ----
        self.go_home(cid)

    # ---------------- 受取待ち ----------------

    def wait_for_receipt(self, call_id: str):
        """
        到着後、アプリ(S-4)の「本を取得した」ボタンで status='done' になるのを待つ。
        （ゼミ決定 2026-09-01: arrived→done はアプリの受取ボタンで進める。設計メモ §11 #8）

        ・dry-run では DB に arrived を書いていない＝アプリからボタンを押せないため、
          arrive_hold 秒待って完了扱いにする
        ・--arrive-timeout N を指定すると、押し忘れ対策として N 秒で自動 done にできる
          （既定 0 = 無効。ロボはボタンが押されるまで席で待つ）
        """
        if not self.live:
            print(f"    [受取] dry-run なので {self.arrive_hold:.0f} 秒後に完了扱いにします")
            time.sleep(self.arrive_hold)
            return

        print("    [受取] アプリの「本を取得した」ボタンを待っています…")
        t0 = time.time()
        while True:
            try:
                rows = self.supa.select("robot_calls", f"id=eq.{call_id}&select=status")
                st = rows[0]["status"] if rows else None
            except requests.RequestException as e:
                print(f"    [warn] 受取確認の通信エラー（続行します）: {e}")
                st = None

            if st == "done":
                print("    [受取] 本の取得を確認しました")
                return
            if st == "canceled":
                print("    [受取] アプリ側でキャンセルされました")
                return
            if self.arrive_timeout > 0 and time.time() - t0 > self.arrive_timeout:
                print(f"    [受取] {self.arrive_timeout:.0f} 秒待ちました → 自動で完了にします")
                self.set_call(call_id, "done")
                return
            time.sleep(1.0)

    # ---------------- 帰還 ----------------

    def go_home(self, call_id: str):
        if not self.return_home or self.home is None:
            why = "ホーム未登録のため" if self.home is None else "--no-return-home のため"
            self.set_status("idle", None, detail=f"待機中（{why}帰還しません）", pose=self.nav.current_pose())
            return
        home_goal = goal_from_stop_point(self.home)
        hlabel = home_goal["label"] or "本棚の場所"
        self._state_now, self._call_now = "returning", call_id
        self.set_status("returning", call_id, detail=f"{hlabel}へ帰還中", pose=self.nav.current_pose())
        ok = self.nav.go(home_goal, on_progress=self._progress(f"{hlabel}へ帰還中"))
        if ok:
            self.at_home = True
            self.set_status("idle", None, detail=f"{hlabel}で待機中", pose=self.nav.current_pose())
        else:
            self.at_home = False
            self.set_status("idle", None,
                            detail=f"{hlabel}への帰還に失敗。手で戻してブリッジを再起動してください",
                            pose=self.nav.current_pose())

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
        self.nav.cancel()
        if self.live and self.current:
            print("\n[終了] 処理中の呼出を canceled にして idle へ戻します。")
            try:
                self.set_call(self.current["id"], "canceled")
                self.set_status("idle", None, detail="ブリッジ停止（処理中の呼出を取り消しました）",
                                pose=self.nav.current_pose())
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
                    help="dry-run 時のみ: arrived → 完了扱いまでの待ち [s]")
    ap.add_argument("--arrive-timeout", type=float, default=0.0,
                    help="到着後、受取ボタンをこの秒数待っても押されなければ自動で done にする"
                         "（0=無効。既定はボタンが押されるまで待つ）")
    ap.add_argument("--nav2", action="store_true",
                    help="本物の Nav2 にゴールを送る（ラズパイ上でのみ）")
    ap.add_argument("--nav-timeout", type=float, default=180.0,
                    help="1回の走行の打ち切り時間 [s]")
    # ---- 段階3 ----
    ap.add_argument("--home-id", type=int, default=None,
                    help="本棚の場所にする stop_points.id（既定: kind='home' の行。無ければ id=0）")
    ap.add_argument("--no-return-home", action="store_true",
                    help="受取後に本棚の場所へ帰らない（到着した席で待機する）")
    ap.add_argument("--no-localize", action="store_true",
                    help="呼出ごとの自己位置推定（初期位置の送信）をしない。"
                         "RViz の 2D Pose Estimate で人が与える運用向け")
    ap.add_argument("--localize-settle", type=float, default=2.0,
                    help="初期位置を送ってから走り出すまでの待ち [s]（粒子が落ち着く時間）")
    ap.add_argument("--localize-timeout", type=float, default=20.0,
                    help="AMCL が初期位置を受け付けるまでの待ち上限 [s]")
    ap.add_argument("--init-sigma-xy", type=float, default=0.15,
                    help="初期位置の位置のばらつき σ [m]（本棚の場所に置く精度）")
    ap.add_argument("--init-sigma-yaw", type=float, default=0.17,
                    help="初期位置の向きのばらつき σ [rad]（0.17 ≒ 10°）")
    ap.add_argument("--pose-interval", type=float, default=1.0,
                    help="走行中に現在地を robot_status へ書く間隔 [s]")
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

    v3 = probe_schema_v3(supa)
    print(f"スキーマ : {'段階3（sql/03 適用済み）' if v3 else '★sql/03 未適用★ localizing と現在地は書きません'}")

    home = load_home(supa, args.home_id)
    if home:
        print(f"ホーム   : id={home['id']} {home.get('label')} "
              f"({float(home['x']):.3f}, {float(home['y']):.3f}) theta={float(home.get('theta') or 0):.3f}")
    else:
        print("ホーム   : ★未登録★ 帰還と初期位置の自動送信はしません"
              "（sql/03 を実行し、pin_tool.py か /admin/pins で本棚の場所を登録してください）")

    if args.nav2:
        try:
            navigator = Nav2Navigator(timeout=args.nav_timeout,
                                      init_sigma_xy=args.init_sigma_xy,
                                      init_sigma_yaw=args.init_sigma_yaw,
                                      localize_timeout=args.localize_timeout,
                                      settle=args.localize_settle)
        except ImportError as e:
            sys.exit(f"Nav2 に接続できません（{e}）。\n"
                     "  ラズパイ上で ROS2 環境を source してから実行してください。\n"
                     "  PC で試すときは --nav2 を外してください。")
    else:
        start = (float(home["x"]), float(home["y"]), float(home.get("theta") or 0)) if home else (0.0, 0.0, 0.0)
        navigator = LoggingNavigator(args.simulate, start_pose=start)

    bridge = Bridge(supa, navigator, args.live, args.arrive_hold, args.arrive_timeout,
                    home=home, return_home=not args.no_return_home,
                    localize=not args.no_localize, pose_interval=args.pose_interval,
                    schema_v3=v3)

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
