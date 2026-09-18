"""
pin_tool.py ― ピン建て：席1〜3 と「本棚の場所」（ホーム）の map 座標を stop_points に登録する  ★段階3★

    地図（SLAM / AMCL）──TF map→base_link──> 【ここ】──upsert──> stop_points <──select── ブリッジ・アプリ
    RViz の 2D Goal Pose / Publish Point ──/goal_pose・/clicked_point──┘

ピンは 4 つ:  id=0 本棚の場所（kind='home'）／ id=1,2,3 席（kind='seat'）
  ・本棚の場所 … ロボの定位置。受取後に帰る先で、呼出のたびに AMCL の初期位置にもなる
                 → 床にテープで印を付け、毎回同じ向きに置くこと
  ・席        … アプリの「届け先の席」。theta は「席に着いたときロボが向く向き」

────────────────────────────────────────────────────────────
使い方（3 通り）
────────────────────────────────────────────────────────────

  A) ロボを置いた場所を登録する（推奨。SLAM か AMCL が動いていて map→base_link の TF があるとき）
       python3 pin_tool.py              # 対話モード。番号 → c で現在地を取り込む
     裁定書 §5 の手順どおり: 席へ 0.15 m/s 以下で進入 → ±40° 首振り → 2〜3 秒静止 → c
     TF を 5 回読んで中央値を採ります。

  B) RViz でクリックした点を登録する（母艦でも Pi でも。ROS_DOMAIN_ID が同じなら届く）
       python3 pin_tool.py              # 同じ対話モード。番号 → r で待ち受け → RViz でクリック
     2D Goal Pose = 位置と向き／Publish Point = 位置だけ（向きは今の値を維持）
     ★Nav2 が起動中に 2D Goal Pose を押すとロボが走り出します。ピン建ては SLAM 中か Nav2 停止中に★

  C) 数値を直接書く（ROS 2 不要。PC からでも）
       python3 pin_tool.py --set 1 1.85 0.42 1.57 --label 席1
       python3 pin_tool.py --list

環境変数（.env でも可）: SUPABASE_URL / SUPABASE_ANON_KEY（ブリッジと同じ .env を使う）
書き込みには sql/03_pins_home_pose.sql の insert/update ポリシーが必要です。
弾かれた場合は同じ内容の SQL を表示するので、Supabase の SQL Editor に貼ってください。
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import sys
import threading
import time

import requests


# =====================================================================
#  設定
# =====================================================================

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
#  計算（実機なしで test_logic.py が検証する部分）
# =====================================================================

def yaw_from_quat(x: float, y: float, z: float, w: float) -> float:
    """クォータニオン → ヨー角 [rad]（平面ロボなので z 軸まわりだけ）。"""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def normalize_angle(a: float) -> float:
    """-π〜π に畳む。"""
    return math.atan2(math.sin(a), math.cos(a))


def median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("empty")
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def median_pose(samples: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """(x, y, yaw) の標本から中央値の姿勢を返す。yaw は ±π の境界をまたいでも壊れないよう平均方向で採る。"""
    xs = [s[0] for s in samples]
    ys = [s[1] for s in samples]
    # 角度の中央値: 平均方向を基準にずらしてから中央値を取り、戻す
    cx = sum(math.cos(s[2]) for s in samples)
    cy = sum(math.sin(s[2]) for s in samples)
    mean_yaw = math.atan2(cy, cx)
    offs = [normalize_angle(s[2] - mean_yaw) for s in samples]
    return median(xs), median(ys), normalize_angle(mean_yaw + median(offs))


def default_label(pin_id: int) -> str:
    return "本棚の場所" if pin_id == 0 else f"席{pin_id}"


def pin_row(pin_id: int, x: float, y: float, theta: float, label: str | None = None) -> dict:
    """stop_points に upsert する 1 行を作る（kind は id から決まる）。"""
    pin_id = int(pin_id)
    if pin_id < 0:
        raise ValueError("id は 0（本棚の場所）か 1 以上（席）です")
    return {
        "id": pin_id,
        "label": (label or default_label(pin_id)).strip(),
        "x": round(float(x), 3),
        "y": round(float(y), 3),
        "theta": round(normalize_angle(float(theta)), 4),
        "kind": "home" if pin_id == 0 else "seat",
    }


def sql_for_pins(rows: list[dict]) -> str:
    """REST で書けなかったときに SQL Editor へ貼るための文。"""
    lines = []
    for r in rows:
        label = str(r["label"]).replace("'", "''")
        lines.append(
            f"insert into stop_points (id, label, x, y, theta, kind) "
            f"values ({r['id']}, '{label}', {r['x']}, {r['y']}, {r['theta']}, '{r['kind']}')\n"
            f"  on conflict (id) do update set label = excluded.label, x = excluded.x, "
            f"y = excluded.y, theta = excluded.theta, kind = excluded.kind;")
    return "\n".join(lines)


def format_pin(r: dict) -> str:
    th = float(r.get("theta") or 0.0)
    kind = "本棚" if r.get("kind") == "home" or int(r["id"]) == 0 else "席 "
    return (f"  [{kind}] id={int(r['id']):<2d} {str(r.get('label') or ''):<10s} "
            f"x={float(r['x']):+7.3f}  y={float(r['y']):+7.3f}  theta={th:+6.3f} rad ({math.degrees(th):+6.1f}°)")


# =====================================================================
#  Supabase（REST）
# =====================================================================

class PinStore:
    def __init__(self, url: str, key: str, timeout: float = 8.0):
        self.base = url.rstrip("/") + "/rest/v1/stop_points"
        self.timeout = timeout
        self.h = {"apikey": key, "Authorization": f"Bearer {key}",
                  "Content-Type": "application/json"}

    def list(self) -> list[dict]:
        r = requests.get(f"{self.base}?select=*&order=id", headers=self.h, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def has_kind_column(self) -> bool:
        r = requests.get(f"{self.base}?select=kind&limit=1", headers=self.h, timeout=self.timeout)
        return r.status_code == 200

    def upsert(self, row: dict) -> list[dict]:
        """id が同じ行があれば更新、無ければ追加（PostgREST の merge-duplicates）。"""
        h = dict(self.h)
        h["Prefer"] = "resolution=merge-duplicates,return=representation"
        body = dict(row)
        if not self.has_kind_column():
            body.pop("kind", None)      # sql/03 未適用なら kind 列が無い
        r = requests.post(f"{self.base}?on_conflict=id", headers=h, json=body, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()


# =====================================================================
#  ROS 2 側（TF と RViz のクリック）★import はここでしか行わない★
# =====================================================================

class RosPinSource:
    """
    ・map→base_link の TF から現在地を読む（current_pose）
    ・RViz の 2D Goal Pose（/goal_pose）と Publish Point（/clicked_point）を受け取る（wait_click）
    spin はデーモンスレッドで回すので、メインスレッドは input() を使える。
    """

    def __init__(self, map_frame: str = "map", base_frame: str = "base_link"):
        import rclpy
        from rclpy.node import Node
        from geometry_msgs.msg import PoseStamped, PointStamped
        from tf2_ros import Buffer, TransformListener

        self._rclpy = rclpy
        self.map_frame = map_frame
        self.base_frame = base_frame
        self.clicks: "queue.Queue[tuple]" = queue.Queue()

        if not rclpy.ok():
            rclpy.init()
        self.node = Node("pin_tool")
        self.buf = Buffer()
        self.listener = TransformListener(self.buf, self.node)
        self.node.create_subscription(PoseStamped, "goal_pose", self._on_goal, 10)
        self.node.create_subscription(PointStamped, "clicked_point", self._on_point, 10)
        self.thread = threading.Thread(target=lambda: rclpy.spin(self.node), daemon=True, name="pin_tool_spin")
        self.thread.start()

    def _on_goal(self, msg):
        q = msg.pose.orientation
        self.clicks.put(("pose", float(msg.pose.position.x), float(msg.pose.position.y),
                         yaw_from_quat(q.x, q.y, q.z, q.w), msg.header.frame_id))

    def _on_point(self, msg):
        self.clicks.put(("point", float(msg.point.x), float(msg.point.y), None, msg.header.frame_id))

    def read_tf_once(self):
        from rclpy.time import Time
        t = self.buf.lookup_transform(self.map_frame, self.base_frame, Time())
        tr, q = t.transform.translation, t.transform.rotation
        return float(tr.x), float(tr.y), yaw_from_quat(q.x, q.y, q.z, q.w)

    def current_pose(self, n: int = 5, interval: float = 0.4) -> tuple[float, float, float]:
        """TF を n 回読んで中央値を返す（裁定書 §5「数回読み、中央値を採用」）。"""
        samples = []
        for i in range(n):
            samples.append(self.read_tf_once())
            print(f"      読み取り {i + 1}/{n}: x={samples[-1][0]:+.3f} y={samples[-1][1]:+.3f} "
                  f"yaw={samples[-1][2]:+.3f}")
            if i < n - 1:
                time.sleep(interval)
        return median_pose(samples)

    def drain_clicks(self):
        while not self.clicks.empty():
            try:
                self.clicks.get_nowait()
            except queue.Empty:
                break

    def wait_click(self, timeout: float = 120.0):
        try:
            return self.clicks.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        try:
            self.node.destroy_node()
            self._rclpy.shutdown()
        except Exception:
            pass


# =====================================================================
#  対話モード
# =====================================================================

def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def write_pin(store: PinStore, row: dict) -> bool:
    try:
        out = store.upsert(row)
        print("    ✓ 書き込みました:")
        for r in out:
            print(format_pin(r))
        return True
    except Exception as e:
        print(f"    ✗ 書き込めませんでした: {e}")
        print("    → Supabase の SQL Editor に次を貼って実行してください"
              "（insert/update ポリシーが無い＝sql/03 未適用のときはこうなります）:")
        print(sql_for_pins([row]))
        return False


def interactive(store: PinStore, ros: RosPinSource | None):
    print("\n=== ピン建て（対話モード） ===")
    print("  番号 → 取り込み方法 を選びます。0=本棚の場所、1〜=席。q で終了。")
    if ros is None:
        print("  ★ROS 2 が無い環境なので、数値入力（m）だけ使えます★")
    while True:
        try:
            rows = store.list()
            print("\n現在のピン:")
            for r in rows:
                print(format_pin(r))
            if not any(int(r["id"]) == 0 for r in rows):
                print("  [本棚] （未登録）id=0 を登録してください")
        except Exception as e:
            print(f"  [warn] 一覧の取得に失敗: {e}")
            rows = []

        s = ask("\n登録するピンの番号（0=本棚の場所, 1〜=席, q=終了）> ")
        if s.lower() in ("q", "quit", "exit"):
            return
        if not s.isdigit():
            continue
        pin_id = int(s)
        current = next((r for r in rows if int(r["id"]) == pin_id), None)
        label = (current or {}).get("label") or default_label(pin_id)
        cur_theta = float((current or {}).get("theta") or 0.0)
        print(f"\n  {label}（id={pin_id}）を登録します。方法:")
        if ros is not None:
            print("    c … ロボの現在地（TF map→base_link を 5 回読んで中央値）★推奨★")
            print("    r … RViz でクリック（2D Goal Pose = 位置+向き ／ Publish Point = 位置のみ）")
        print("    m … 数値を入力（x y theta）")
        print("    b … 戻る")
        how = ask("  > ").lower()

        x = y = th = None
        if how == "c" and ros is not None:
            print("  首振り（±40°）のあと 2〜3 秒静止してから Enter …")
            ask("  ")
            try:
                x, y, th = ros.current_pose()
            except Exception as e:
                print(f"    ✗ TF を読めません（{ros.map_frame}→{ros.base_frame}）: {e}")
                print("      SLAM か AMCL が動いているか、フレーム名（--map-frame / --base-frame）を確認")
                continue
        elif how == "r" and ros is not None:
            ros.drain_clicks()
            print("  RViz でクリックしてください（2 分待ちます。Ctrl-C で中止）…")
            ev = ros.wait_click()
            if ev is None:
                print("    ✗ クリックが来ませんでした")
                continue
            kind, x, y, th_click, frame = ev
            if frame and frame != ros.map_frame:
                print(f"    ✗ クリックのフレームが {frame} です（{ros.map_frame} ではない）。RViz の Fixed Frame を map に")
                continue
            th = th_click if th_click is not None else cur_theta
            print(f"    受信: {'2D Goal Pose' if kind == 'pose' else 'Publish Point'}  x={x:+.3f} y={y:+.3f}"
                  + (f" yaw={th:+.3f}" if th_click is not None else f"（向きは今の値 {th:+.3f} を維持）"))
        elif how == "m":
            v = ask("  x y theta（m m rad。度で入れるなら末尾に d、例: 1.2 0.4 90d）> ").split()
            if len(v) != 3:
                print("    ✗ 3 つの数を空白区切りで")
                continue
            try:
                x, y = float(v[0]), float(v[1])
                th = math.radians(float(v[2][:-1])) if v[2].lower().endswith("d") else float(v[2])
            except ValueError:
                print("    ✗ 数として読めません")
                continue
        else:
            continue

        row = pin_row(pin_id, x, y, th, label)
        new_label = ask(f"  ラベル [{row['label']}]（Enter でそのまま）> ")
        if new_label:
            row["label"] = new_label
        print("  書き込む内容:")
        print(format_pin(row))
        if ask("  書き込みますか？ [Y/n] > ").lower() in ("", "y", "yes"):
            write_pin(store, row)


# =====================================================================
#  main
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description="席・本棚の場所のピン建て（stop_points へ登録）")
    ap.add_argument("--list", action="store_true", help="いまのピン一覧を表示して終了")
    ap.add_argument("--set", nargs=4, metavar=("ID", "X", "Y", "THETA"),
                    help="数値を直接登録（ROS 2 不要）。例: --set 1 1.85 0.42 1.57")
    ap.add_argument("--label", help="--set のときのラベル（省略時: 0=本棚の場所, N=席N）")
    ap.add_argument("--sql", action="store_true", help="--set の内容を書き込まず SQL として表示する")
    ap.add_argument("--map-frame", default="map")
    ap.add_argument("--base-frame", default="base_link",
                    help="ロボ本体のフレーム名（暫定 TF 構成では base_footprint でも可）")
    ap.add_argument("--no-ros", action="store_true", help="ROS 2 を使わない（数値入力のみ）")
    args = ap.parse_args()

    load_env()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL と SUPABASE_ANON_KEY を .env か環境変数に入れてください。")
    store = PinStore(url, key)
    print(f"接続先 : {url}")

    if args.set:
        pid, x, y, th = args.set
        row = pin_row(int(pid), float(x), float(y), float(th), args.label)
        if args.sql:
            print(sql_for_pins([row]))
            return
        print("書き込む内容:")
        print(format_pin(row))
        sys.exit(0 if write_pin(store, row) else 1)

    if args.list:
        for r in store.list():
            print(format_pin(r))
        return

    ros = None
    if not args.no_ros:
        try:
            ros = RosPinSource(args.map_frame, args.base_frame)
            print(f"ROS 2  : 接続（TF {args.map_frame}→{args.base_frame} ／ /goal_pose ／ /clicked_point）")
        except ImportError:
            print("ROS 2  : 無し（数値入力のみ）。Pi か母艦で ROS 2 環境を source すると c / r が使えます")
        except Exception as e:
            print(f"ROS 2  : 初期化に失敗（{e}）。数値入力のみで続けます")
    try:
        interactive(store, ros)
    except KeyboardInterrupt:
        pass
    finally:
        if ros is not None:
            ros.close()
        print("終了しました。")


if __name__ == "__main__":
    main()
