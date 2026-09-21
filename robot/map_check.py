"""
map_check.py ― 保存した地図（~/maps/my_map.yaml ＋ .pgm）とピンの点検  ★段階3★

「地図はちゃんと保存できているか」「ピンは Nav2 が行ける場所に立っているか」を、
ROS 2 も RViz も使わずに Pi の画面だけで確かめる道具です（何も書き換えません）。

    cd ~/moving-bookshelf-app/robot
    python3 map_check.py                         # ~/maps/my_map.yaml と Supabase のピンを点検
    python3 map_check.py --map ~/maps/other.yaml # 別の地図
    python3 map_check.py --no-pins               # 地図だけ（Supabase を見ない）
    python3 map_check.py --pin 1.20 0.10         # 数値で点を指定して調べる（何個でも）

見るところ
  1. 地図の大きさ・範囲（何 m 四方か。走らせる範囲が入っているか）
  2. 空き・障害物・未知のマスの割合（ほとんど未知なら、地図が育つ前に保存している）
  3. ピンごとに「地図の中か」「空きマスか」「いちばん近い障害物まで何 m か」
     ── 機体の半径（--radius、既定 0.21 m）より近いと、Nav2 はそこを「入れない場所」とみなし、
        経路を作れません（アプリの表示が「残り 0.0 m」のまま、その場で回るだけになる）
  4. 本棚の場所から各席まで、機体の半径ぶん障害物を太らせても通れる道があるか
  5. 地図を文字で表示（# 障害物  . 空き  空白 未知  0〜9 ピン）

地図の座標の決まり（map_server と同じ）
  ・.yaml の origin は「画像の左下の角」の map 座標 [x, y, yaw]
  ・.pgm は 1 行目が地図の「上の端」。白っぽい＝空き、黒っぽい＝障害物、灰色＝未知
  ・占有率 p = (255 − 画素値) / 255（negate: 0 のとき）。p > occupied_thresh で障害物、p < free_thresh で空き
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import deque

FREE, OCC, UNKNOWN = 0, 1, 2


# =====================================================================
#  読み込み（実機なしで test_logic.py が検証する部分）
# =====================================================================

def parse_map_yaml(text: str) -> dict:
    """map_saver が書く .yaml を読む。PyYAML があれば使い、無ければ「キー: 値」だけの最小実装で読む。"""
    try:
        import yaml
        data = yaml.safe_load(text) or {}
    except ImportError:
        data = {}
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                data[k.strip()] = [float(t) for t in v[1:-1].split(",") if t.strip()]
            else:
                try:
                    data[k.strip()] = float(v) if any(c in v for c in ".eE") else int(v)
                except ValueError:
                    data[k.strip()] = v.strip("'\"")
    origin = data.get("origin") or [0.0, 0.0, 0.0]
    return {
        "image": str(data.get("image", "")),
        "resolution": float(data.get("resolution", 0.05)),
        "origin": [float(origin[0]), float(origin[1]), float(origin[2]) if len(origin) > 2 else 0.0],
        "negate": int(data.get("negate", 0)),
        "occupied_thresh": float(data.get("occupied_thresh", 0.65)),
        "free_thresh": float(data.get("free_thresh", 0.25)),
        "mode": str(data.get("mode", "trinary")),
    }


def parse_pgm(data: bytes) -> tuple[int, int, int, list[int]]:
    """PGM（P5＝バイナリ／P2＝テキスト）を読み、(幅, 高さ, 最大値, 画素の並び) を返す。"""
    pos = 0
    tokens: list[bytes] = []
    while len(tokens) < 4:                     # マジック・幅・高さ・最大値（# から行末はコメント）
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if pos >= len(data):
            raise ValueError("PGM のヘッダが途中で終わっています")
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        tokens.append(data[start:pos])
    magic, w, h, maxval = tokens[0], int(tokens[1]), int(tokens[2]), int(tokens[3])
    if magic == b"P5":
        pos += 1                               # 最大値のあとの空白 1 文字
        n = w * h
        if maxval < 256:
            px = list(data[pos:pos + n])
        else:                                  # 16 bit（上位バイトが先）
            raw = data[pos:pos + 2 * n]
            px = [(raw[i] << 8) | raw[i + 1] for i in range(0, len(raw) - 1, 2)]
    elif magic == b"P2":
        px = [int(t) for t in data[pos:].split()]
    else:
        raise ValueError(f"PGM ではありません（先頭が {magic!r}）")
    if len(px) < w * h:
        raise ValueError(f"画素が足りません（{w}×{h}＝{w * h} 個のはずが {len(px)} 個）。保存が途中で切れた可能性")
    return w, h, maxval, px[:w * h]


def classify(px: list[int], maxval: int, meta: dict) -> list[int]:
    """画素値 → FREE / OCC / UNKNOWN（map_server の trinary と同じ判定）。"""
    occ_th, free_th, negate = meta["occupied_thresh"], meta["free_thresh"], meta["negate"]
    # map_saver は「未知」を灰色 205 で書く。占有率に直すと 0.196 で、free_thresh が 0.25（Nav2 の
    # map_saver の既定）だと計算上は「空き」側に入ってしまうので、この灰色だけは先に未知として拾う。
    # （Nav2 の経路計画は未知のマスも通れる扱いなので、どちらに数えても道の有無は変わらない）
    gray = 205 * maxval / 255.0
    out = []
    for v in px:
        if not negate and abs(v - gray) <= 3 * maxval / 255.0:
            out.append(UNKNOWN)
            continue
        p = (v / maxval) if negate else ((maxval - v) / maxval)
        out.append(OCC if p > occ_th else FREE if p < free_th else UNKNOWN)
    return out


class GridMap:
    """分類済みの地図。row 0 が画像の上の端（＝地図の y が大きい側）。"""

    def __init__(self, w: int, h: int, cells: list[int], meta: dict):
        self.w, self.h, self.cells, self.meta = w, h, cells, meta
        self.res = meta["resolution"]
        self.ox, self.oy = meta["origin"][0], meta["origin"][1]

    # ---- 座標 ----
    def world_to_cell(self, x: float, y: float) -> tuple[int, int] | None:
        col = math.floor((x - self.ox) / self.res)
        up = math.floor((y - self.oy) / self.res)          # 下から数えた行
        if not (0 <= col < self.w and 0 <= up < self.h):
            return None
        return col, self.h - 1 - up

    def cell_center(self, col: int, row: int) -> tuple[float, float]:
        return (self.ox + (col + 0.5) * self.res,
                self.oy + (self.h - 1 - row + 0.5) * self.res)

    def at(self, col: int, row: int) -> int:
        return self.cells[row * self.w + col]

    def bounds(self) -> tuple[float, float, float, float]:
        return self.ox, self.ox + self.w * self.res, self.oy, self.oy + self.h * self.res

    def counts(self) -> dict:
        c = {FREE: 0, OCC: 0, UNKNOWN: 0}
        for v in self.cells:
            c[v] += 1
        return c

    # ---- 障害物までの距離 ----
    def clearance(self, x: float, y: float, search: float = 1.5) -> float | None:
        """点 (x, y) から、いちばん近い障害物マスの中心までの距離 [m]。search [m] 以内に無ければ None。"""
        r = int(math.ceil(search / self.res))
        c0 = math.floor((x - self.ox) / self.res)
        u0 = math.floor((y - self.oy) / self.res)
        best = None
        for up in range(max(0, u0 - r), min(self.h, u0 + r + 1)):
            row = self.h - 1 - up
            base = row * self.w
            for col in range(max(0, c0 - r), min(self.w, c0 + r + 1)):
                if self.cells[base + col] != OCC:
                    continue
                cx, cy = self.cell_center(col, row)
                d = math.hypot(cx - x, cy - y)
                if best is None or d < best:
                    best = d
        return best if best is not None and best <= search else None

    def distance_field(self) -> list[float]:
        """全マスの「いちばん近い障害物までの距離 [m]」（2 回なぞる近似。誤差は数 %）。"""
        inf = float("inf")
        w, h, res = self.w, self.h, self.res
        d = [0.0 if v == OCC else inf for v in self.cells]
        s, g = res, res * math.sqrt(2.0)
        for row in range(h):                                   # 左上 → 右下
            for col in range(w):
                i = row * w + col
                v = d[i]
                if col > 0: v = min(v, d[i - 1] + s)
                if row > 0:
                    v = min(v, d[i - w] + s)
                    if col > 0: v = min(v, d[i - w - 1] + g)
                    if col < w - 1: v = min(v, d[i - w + 1] + g)
                d[i] = v
        for row in range(h - 1, -1, -1):                       # 右下 → 左上
            for col in range(w - 1, -1, -1):
                i = row * w + col
                v = d[i]
                if col < w - 1: v = min(v, d[i + 1] + s)
                if row < h - 1:
                    v = min(v, d[i + w] + s)
                    if col < w - 1: v = min(v, d[i + w + 1] + g)
                    if col > 0: v = min(v, d[i + w - 1] + g)
                d[i] = v
        return d

    def path_length(self, a: tuple[float, float], b: tuple[float, float], radius: float,
                    field: list[float] | None = None) -> float | None:
        """
        a から b まで、障害物を機体の半径ぶん太らせても通れる道の長さ [m]（無ければ None）。
        Nav2 の経路計画と同じく、未知のマスは通れるものとして数える。出発点と到着点そのものが
        太らせた障害物の中にある場合も None（＝Nav2 も経路を作れない）。
        """
        ca, cb = self.world_to_cell(*a), self.world_to_cell(*b)
        if ca is None or cb is None:
            return None
        field = field if field is not None else self.distance_field()
        w, h = self.w, self.h
        ok = [dist >= radius for dist in field]
        ia, ib = ca[1] * w + ca[0], cb[1] * w + cb[0]
        if not ok[ia] or not ok[ib]:
            return None
        best = {ia: 0.0}
        q = deque([ia])
        s, g = self.res, self.res * math.sqrt(2.0)
        while q:                                               # 幅優先＋長さの更新（地図が小さいので十分）
            i = q.popleft()
            row, col = divmod(i, w)
            for dr, dc, step in ((0, 1, s), (0, -1, s), (1, 0, s), (-1, 0, s),
                                 (1, 1, g), (1, -1, g), (-1, 1, g), (-1, -1, g)):
                r2, c2 = row + dr, col + dc
                if not (0 <= r2 < h and 0 <= c2 < w):
                    continue
                j = r2 * w + c2
                if not ok[j]:
                    continue
                nd = best[i] + step
                if nd < best.get(j, float("inf")) - 1e-9:
                    best[j] = nd
                    q.append(j)
        return best.get(ib)


def load_map(yaml_path: str) -> GridMap:
    yaml_path = os.path.expanduser(yaml_path)
    with open(yaml_path, encoding="utf-8") as f:
        meta = parse_map_yaml(f.read())
    img = meta["image"]
    if not os.path.isabs(img):
        img = os.path.join(os.path.dirname(os.path.abspath(yaml_path)), img)
    with open(img, "rb") as f:
        w, h, maxval, px = parse_pgm(f.read())
    meta["image_path"] = img
    return GridMap(w, h, classify(px, maxval, meta), meta)


# =====================================================================
#  判定
# =====================================================================

def judge_point(m: GridMap, x: float, y: float, radius: float, margin: float = 0.05) -> tuple[str, str]:
    """(記号, 説明)。記号は OK / WARN / NG。"""
    cell = m.world_to_cell(x, y)
    if cell is None:
        return "NG", "★地図の外★ 地図とピンが別の回のものか、地図がここまで育つ前に保存しています"
    kind = m.at(*cell)
    if kind == OCC:
        return "NG", "★障害物のマスの上★ Nav2 はここへ行けません"
    d = m.clearance(x, y)
    near = "1.5 m 以内に障害物なし" if d is None else f"いちばん近い障害物まで {d:.2f} m"
    if d is not None and d < radius:
        return "NG", (f"★{near}（機体の半径 {radius:.2f} m より近い）★ "
                      "Nav2 はここを「入れない場所」とみなし、経路を作れません")
    if d is not None and d < radius + margin:
        return "WARN", f"△ {near}（半径 {radius:.2f} m ぎりぎり。少しのずれで経路が作れなくなります）"
    if kind == UNKNOWN:
        return "WARN", f"△ 未知のマス（LiDAR がまだ見ていない場所）。{near}"
    return "OK", f"✓ 空きマス。{near}"


def render_ascii(m: GridMap, marks: dict[tuple[int, int], str], max_cols: int = 110) -> str:
    """地図を文字で。# 障害物  . 空き  空白 未知。大きい地図は間引く（間引いたマスに障害物が 1 つでもあれば #）。
    端末の文字は縦長なので、幅に余裕があれば 1 マスを横 2 文字で描いて縦横の比を実物に近づける。"""
    step = max(1, math.ceil(m.w / max_cols))
    wide = 2 if math.ceil(m.w / step) * 2 <= max_cols else 1
    lines = []
    for row0 in range(0, m.h, step):
        chars = []
        for col0 in range(0, m.w, step):
            mark = None
            kinds = set()
            for row in range(row0, min(m.h, row0 + step)):
                for col in range(col0, min(m.w, col0 + step)):
                    kinds.add(m.at(col, row))
                    mark = marks.get((col, row), mark)
            chars.append((mark if mark else "#" if OCC in kinds else "." if FREE in kinds else " ") * wide)
        lines.append("".join(chars).rstrip())
    return "\n".join(lines)


# =====================================================================
#  画面
# =====================================================================

def fetch_pins() -> list[dict] | None:
    """Supabase のピン。.env が無い・通信できないときは None（地図だけ点検する）。"""
    try:
        import pin_tool
    except ImportError as e:                       # requests が無い PC など
        print(f"  （ピンは読めません: {e}）")
        return None
    pin_tool.load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    pin_tool.load_env(".env")
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        print("  （.env に SUPABASE_URL / SUPABASE_ANON_KEY が無いので、ピンは見ません）")
        return None
    try:
        return pin_tool.PinStore(url, key).list()
    except Exception as e:
        print(f"  （Supabase からピンを読めませんでした: {e}）")
        return None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="保存した地図とピンの点検（何も書き換えません）")
    ap.add_argument("--map", default="~/maps/my_map.yaml", help="地図の .yaml（既定 ~/maps/my_map.yaml）")
    ap.add_argument("--radius", type=float, default=0.21, help="機体の半径 [m]（make_nav2_params.py の --robot-radius と同じ値）")
    ap.add_argument("--no-pins", action="store_true", help="Supabase のピンを見ない")
    ap.add_argument("--pin", nargs=2, type=float, action="append", metavar=("X", "Y"),
                    help="調べたい点を数値で足す（何回でも）")
    ap.add_argument("--no-ascii", action="store_true", help="地図を文字で表示しない")
    args = ap.parse_args()

    path = os.path.expanduser(args.map)
    print(f"地図   : {path}")
    try:
        m = load_map(path)
    except FileNotFoundError as e:
        sys.exit(f"★ファイルがありません★ {e.filename}\n"
                 "  保存がまだか、失敗しています。SLAM が動いているあいだに:\n"
                 "    mkdir -p ~/maps\n"
                 "    ros2 run nav2_map_server map_saver_cli -f ~/maps/my_map --ros-args -p save_map_timeout:=10.0")
    except ValueError as e:
        sys.exit(f"★地図の画像が読めません★ {e}")

    st = os.stat(m.meta["image_path"])
    import time
    x0, x1, y0, y1 = m.bounds()
    c = m.counts()
    n = m.w * m.h
    print(f"画像   : {m.meta['image_path']}（{st.st_size} バイト、保存 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}）")
    print(f"大きさ : {m.w} × {m.h} マス ＝ {m.w * m.res:.2f} × {m.h * m.res:.2f} m（1 マス {m.res:.3f} m）")
    print(f"範囲   : x {x0:+.2f} 〜 {x1:+.2f} m ／ y {y0:+.2f} 〜 {y1:+.2f} m（origin＝左下の角 {m.meta['origin']}）")
    print(f"中身   : 空き {c[FREE]} マス（{100 * c[FREE] / n:.0f}%）・障害物 {c[OCC]}（{100 * c[OCC] / n:.0f}%）・未知 {c[UNKNOWN]}（{100 * c[UNKNOWN] / n:.0f}%）")

    problems = []
    if abs(m.meta["origin"][2]) > 1e-6:
        problems.append("origin の yaw が 0 ではありません（この道具は回転した地図を正しく扱えません）")
    if c[FREE] == 0:
        problems.append("空きマスが 1 つもありません（地図が育つ前に保存したか、しきい値が合っていません）")
    elif c[FREE] * m.res * m.res < 1.0:
        problems.append(f"空きの広さが {c[FREE] * m.res * m.res:.1f} m² しかありません（走らせる範囲を一周してから保存しましたか？）")
    if c[OCC] == 0:
        problems.append("障害物のマスが 1 つもありません（壁が写っていません）")

    # ---- ピン ----
    pins: list[dict] = []
    if not args.no_pins:
        rows = fetch_pins()
        for r in rows or []:
            pins.append({"id": int(r["id"]), "label": r.get("label") or f"id={r['id']}",
                         "x": float(r["x"]), "y": float(r["y"]), "home": r.get("kind") == "home" or int(r["id"]) == 0})
    for i, (px, py) in enumerate(args.pin or []):
        pins.append({"id": None, "label": f"指定した点 {i + 1}", "x": px, "y": py, "home": False})

    marks: dict[tuple[int, int], str] = {}
    if pins:
        print(f"\nピン（機体の半径 {args.radius:.2f} m で判定）:")
        field = m.distance_field()
        home = next((p for p in pins if p["home"]), None)
        for p in pins:
            sym, text = judge_point(m, p["x"], p["y"], args.radius)
            tag = "本棚" if p["home"] else "席 "
            print(f"  [{tag}] {p['label']:<10} ({p['x']:+.3f}, {p['y']:+.3f})  {text}")
            if sym == "NG":
                problems.append(f"{p['label']}: {text}")
            cell = m.world_to_cell(p["x"], p["y"])
            if cell is not None:
                marks[cell] = str(p["id"]) if p["id"] is not None and 0 <= p["id"] <= 9 else "*"
        if home:
            print("\n本棚の場所からの道（障害物を半径ぶん太らせても通れるか。未知のマスは通れる扱い）:")
            for p in pins:
                if p is home:
                    continue
                length = m.path_length((home["x"], home["y"]), (p["x"], p["y"]), args.radius, field)
                straight = math.hypot(p["x"] - home["x"], p["y"] - home["y"])
                if length is None:
                    print(f"  → {p['label']:<10} ★道がありません★（直線で {straight:.2f} m）")
                    problems.append(f"本棚の場所 → {p['label']}: 機体の半径 {args.radius:.2f} m で通れる道がありません")
                else:
                    print(f"  → {p['label']:<10} ✓ 道あり 約 {length:.2f} m（直線で {straight:.2f} m）")

    if not args.no_ascii:
        print("\n地図（# 障害物  . 空き  空白 未知  数字 ピンの id。上が y の大きい側、右が x の大きい側）:")
        print(render_ascii(m, marks))

    print()
    if problems:
        print("=== ★気になるところ★ ===")
        for t in problems:
            print("  ・" + t)
        print("  → 壁や机に近すぎるピンは、少し離した位置に取り直します（pin_tool.py／アプリの /admin/pins）。\n"
              "    地図そのものがおかしいときは、地図づくりからやり直します（ピンも取り直し）。")
    else:
        print("✓ 地図は読めて、ピンも Nav2 が行ける場所にあります。")


if __name__ == "__main__":
    main()
