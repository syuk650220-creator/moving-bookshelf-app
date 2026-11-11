"""
beacon_locator.py ― UWB の「距離と方向」を、地図の上の「ユニットの座標」にする  ★S2（UWB ビーコン拡張）★

uwb_reader.py（S1）が出す 1 回の測定（distance_m・bearing_deg）は「ロボのアンテナから見た相対の値」です。
ロボが地図のどこにいて、どちらを向いているか（AMCL の推定 x, y, yaw）と、アンテナが機体のどこに
どちら向きで付いているか（robot_params の UWB_MOUNT_*）を足すと、ユニットの地図座標になります。
その座標を bookshelf_bridge.py（S3）が Nav2 のゴール（0.5 m 手前）にします。

    AMCL: ロボ (rx, ry, ryaw) ──┐
    取り付け位置 (MX, MY, MYAW) ─┼─> アンテナの地図上の位置と向き (ax, ay, ayaw)
    UWB: 距離 d・方向 b ─────────┘      → ユニット = (ax + dh·cos(ayaw + b), ay + dh·sin(ayaw + b))
                                           dh = sqrt(d² − dz²)  …… 高さの差 dz の補正（UWB の距離は斜めの直線距離）

────────────────────────────────────────────────────────────
この計算で気をつけること（実測で決める値は robot_params.py と bridge の引数に出してある）
────────────────────────────────────────────────────────────
  ① 外れ値 … 1 回 1 回の値はばらつく（人や机ごし）。最近 N 回の点の「中央値」を取る（平均より外れ値に強い）。
     ばらつき（中央値からの距離の中央値）を確からしさとして返す。
  ② 前後の見分け … 基板のアンテナは 2 本横に並んでいるだけなので、相手が「左前 30°」でも「左後ろ 150°」でも
     同じ位相差になり、基板は −90〜+90 の範囲の角（鏡写し）しか出せない。
     → 1 回の測定から候補の点を 2 つ（前: b、後ろ: mirror(b)）作る。ロボが回る／動くと、本当の点は
       どの測定でも同じ場所に集まり、間違った候補は流れる。「いちばん多くの測定に支持される点」を選ぶ。
       回るまでは 2 つの山が同じ大きさなので ambiguous=True（前の候補を仮に返す）。
  ③ 使える方向の範囲 … 真横に近い角は当てにならない（目安 ±60°。実測で決める）。範囲外の回は
     「方向」には使わず「距離」にだけ使う。
  ④ 距離だけで決める … 一直線でない 3 か所以上から (x_i, y_i, d_i) が取れれば、円の交わり（三点測位）を
     最小二乗で解ける（numpy 不要の 2×2）。方向が当てにならないときの保険と、②の決め手。

使い方（S3 から）:
    est = BeaconEstimator()                       # 呼出ごとに新しく作る
    for r in uwb.poll():                          # 1 秒 5 回
        est.add(nav.current_pose(), r)            # ロボの位置（map, x y yaw）と測定を足す
    e = est.estimate()                            # BeaconEstimate（x, y, ambiguous, …）か None
    goal = standoff_goal(e, nav.current_pose())   # 0.5 m 手前の Nav2 ゴール（向きはユニットのほう）

確かめ方（基板なし）:
    python3 beacon_locator.py --robot 1.0 0.5 90 --d 1.66 --b -9.65     # ロボ (1.0, 0.5) 向き 90°・距離 1.66 m・方向 −9.65°
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections import deque
from dataclasses import dataclass

import robot_params as P
from uwb_reader import UwbReading, is_usable


# =====================================================================
#  設定（実測で決めるもの）
# =====================================================================

USABLE_BEARING_DEG = 60.0     # ★実測で決める（ロードマップ 6-1 ③）★ これより横の回は「方向」に使わない（「距離」には使う）
CLUSTER_RADIUS_M = 0.40       # 候補の点が「同じ場所」と見なす半径 [m]。ばらつき（±9 cm）と動きながらの測定の余裕
PRIOR_RADIUS_M = 0.8          # 前回の推定（stop_points のビーコンの行）から、この半径の内にある山を「同じユニット」とみなす [m]


# =====================================================================
#  幾何（1 回の測定 → 地図の点）
# =====================================================================

@dataclass
class Mount:
    """アンテナ（2 本の中点）の取り付け位置。base_link（回転の中心）から見て前が＋x、左が＋y。
    yaw はアンテナの面が向く方向（前向き 0、後ろ向き π）。height は床からの高さ。値は robot_params.py。"""
    x: float = P.UWB_MOUNT_X
    y: float = P.UWB_MOUNT_Y
    yaw: float = P.UWB_MOUNT_YAW
    height: float = P.UWB_ANTENNA_HEIGHT


def parse_mount(text: str) -> Mount:
    """「X,Y,向き[度],高さ」の文字（例 "-0.15,0,180,0.25"）→ Mount。ブリッジの --uwb-mount 用。
    X は前が＋、Y は左が＋ [m]（base_link＝回転の中心から）。向きはアンテナの面が向く方向（前 0、後ろ 180）。"""
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 4:
        raise ValueError(f"取り付けは「X,Y,向き[度],高さ」の 4 つの数で書いてください: {text!r}")
    try:
        x, y, yaw_deg, h = (float(p) for p in parts)
    except ValueError:
        raise ValueError(f"取り付けの値が数ではありません: {text!r}") from None
    if not (0.0 <= h <= 1.0):
        raise ValueError(f"高さは床から 0〜1 m で書いてください: {h}")
    return Mount(x=x, y=y, yaw=math.radians(yaw_deg), height=h)


def wrap_angle(a: float) -> float:
    """角度を −π〜π に収める。"""
    return math.atan2(math.sin(a), math.cos(a))


def antenna_pose(robot_pose, mount: Mount | None = None) -> tuple[float, float, float]:
    """ロボの位置と向き (x, y, yaw) → アンテナの地図上の位置と向き (ax, ay, ayaw)。"""
    m = mount or Mount()
    rx, ry, ryaw = robot_pose
    c, s = math.cos(ryaw), math.sin(ryaw)
    return (rx + c * m.x - s * m.y, ry + s * m.x + c * m.y, wrap_angle(ryaw + m.yaw))


def bearing_from_antenna(target_xy, robot_pose, mount: Mount | None = None) -> float:
    """地図上の点がアンテナから見て何度の方向か [度]（アンテナの正面 0、左が＋、−180〜180）。"""
    ax, ay, ayaw = antenna_pose(robot_pose, mount)
    return math.degrees(wrap_angle(math.atan2(target_xy[1] - ay, target_xy[0] - ax) - ayaw))


def in_antenna_cone(target_xy, robot_pose, mount: Mount | None = None,
                    usable_deg: float = USABLE_BEARING_DEG) -> bool:
    """地図上の点が、アンテナの正面 ±usable_deg の内にあるか。外なら、その点からの電波は機体ごし（陰）になりうる。"""
    return abs(bearing_from_antenna(target_xy, robot_pose, mount)) <= usable_deg


def horizontal_distance(d: float, dz: float) -> float:
    """斜めの直線距離 d → 水平距離。dz は相手とアンテナの高さの差。d が dz より短ければ真上（0）扱い。"""
    if d <= abs(dz):
        return 0.0
    return math.sqrt(d * d - dz * dz)


def mirror_bearing(b_deg: float) -> float:
    """基板が出す −90〜+90 の角 b に対する「後ろ側」の候補（鏡写し）。30 → 150、−30 → −150、0 → 180。"""
    return 180.0 - b_deg if b_deg >= 0 else -180.0 - b_deg


def point_from(ant_pose, dh: float, bearing_deg: float) -> tuple[float, float]:
    """アンテナの位置と向き、水平距離、方向（左が＋） → 地図の点。"""
    ax, ay, ayaw = ant_pose
    a = ayaw + math.radians(bearing_deg)
    return (ax + dh * math.cos(a), ay + dh * math.sin(a))


def median_point(pts) -> tuple[float, float, float]:
    """点の集まり → (x の中央値, y の中央値, ばらつき)。ばらつき＝中央値からの距離の中央値（1 点なら 0）。"""
    pts = list(pts)
    mx = statistics.median(p[0] for p in pts)
    my = statistics.median(p[1] for p in pts)
    spread = statistics.median(math.hypot(p[0] - mx, p[1] - my) for p in pts)
    return mx, my, spread


def trilaterate(points, dists) -> tuple[float, float] | None:
    """距離だけから位置を決める（三点測位の最小二乗。線形化して 2×2 を解く。numpy 不要）。

    points … アンテナの位置 [(x, y), …]、dists … そのときの水平距離 [m]。3 点以上で、一直線上でないこと。
    円の式 |b − p_i|² = d_i² を 0 番の式から引くと b について 1 次になる:
        2(x_i − x_0)·X + 2(y_i − y_0)·Y = (x_i² + y_i² − x_0² − y_0²) − (d_i² − d_0²)
    """
    pts, ds = list(points), list(dists)
    if len(pts) < 3 or len(pts) != len(ds):
        return None
    x0, y0, d0 = pts[0][0], pts[0][1], ds[0]
    a11 = a12 = a22 = b1 = b2 = 0.0
    for (x, y), d in zip(pts[1:], ds[1:]):
        ax, ay = 2.0 * (x - x0), 2.0 * (y - y0)
        c = (x * x + y * y - x0 * x0 - y0 * y0) - (d * d - d0 * d0)
        a11 += ax * ax
        a12 += ax * ay
        a22 += ay * ay
        b1 += ax * c
        b2 += ay * c
    det = a11 * a22 - a12 * a12
    scale = (a11 + a22) ** 2
    if scale <= 0 or det <= 1e-6 * scale:          # 一直線上（決まらない）
        return None
    return ((a22 * b1 - a12 * b2) / det, (a11 * b2 - a12 * b1) / det)


# =====================================================================
#  何回かの測定をまとめて 1 つの推定にする
# =====================================================================

@dataclass
class BeaconEstimate:
    x: float                 # ユニットの地図座標 [m]
    y: float
    n: int                   # 支持する測定の数
    spread_m: float          # ばらつき [m]（小さいほど確か）
    method: str              # "bearing"（方向から）／"range"（距離だけから）
    ambiguous: bool          # 前後がまだ決まっていない（ロボが回る／動くと決まる）
    distance_m: float        # 最新の水平距離
    bearing_deg: float       # 最新の方向（アンテナから。後ろと決まった回は鏡写しを戻した値。左が＋）
    resolved_by: str = ""    # 前後を何で決めたか: "turn"（回って）／"range"（距離だけの解）／"prior"（前回の場所）／""（未確定）

    def dist_from(self, pose) -> float:
        return math.hypot(self.x - pose[0], self.y - pose[1])


class BeaconEstimator:
    """測定をためて、ユニットの地図座標を推定する。呼出ごとに新しく作る（reset() でもよい）。"""

    def __init__(self, mount: Mount | None = None, beacon_height: float = P.BEACON_HEIGHT,
                 window: int = 15, min_fom: float | None = None, max_nlos: float | None = None,
                 max_range_m: float = 8.0, usable_deg: float = USABLE_BEARING_DEG,
                 cluster_m: float = CLUSTER_RADIUS_M, prior=None, prior_m: float = PRIOR_RADIUS_M):
        self.mount = mount or Mount()
        # 前回ロボが推定した場所 (x, y)（stop_points のビーコンの行）。止まったままで前後が決まらないとき、
        # 2 つの山のうち片方だけがこの近くにあれば、そちらを選ぶ（回って確かめる手間を省く）。None なら使わない
        self.prior = None if prior is None else (float(prior[0]), float(prior[1]))
        self.prior_m = prior_m
        self.dz = beacon_height - self.mount.height      # 相手とアンテナの高さの差
        self.min_fom, self.max_nlos, self.max_range_m = min_fom, max_nlos, max_range_m
        self.usable_deg, self.cluster_m = usable_deg, cluster_m
        self.obs: deque = deque(maxlen=window)            # (ant_pose, dh, b_deg, front_pt, back_pt | None)
        self.n_total = 0                                  # 受け取った測定（測れなかった回も含む）
        self.n_used = 0                                   # 位置の計算に使った測定

    def reset(self):
        self.obs.clear()
        self.n_total = self.n_used = 0

    # ---- 1 回の測定を足す ----

    def add(self, robot_pose, r: UwbReading) -> bool:
        """使える測定なら候補の点を作ってためる。使ったら True。"""
        self.n_total += 1
        if robot_pose is None or not is_usable(r, self.min_fom, self.max_nlos, self.max_range_m):
            return False
        ant = antenna_pose(robot_pose, self.mount)
        dh = horizontal_distance(r.distance_m, self.dz)
        b = r.bearing_deg
        if abs(b) <= self.usable_deg:                   # 方向が使える回: 前後 2 つの候補
            front, back = point_from(ant, dh, b), point_from(ant, dh, mirror_bearing(b))
        else:                                           # 横すぎる回: 距離だけ使う（候補なし）
            front = back = None
        self.obs.append((ant, dh, b, front, back))
        self.n_used += 1
        return True

    # ---- 推定 ----

    def _support(self, c, cands_per_obs) -> list:
        """点 c を支持する測定ごとに、その測定の近いほうの候補を返す（半径 cluster_m の中にあるもの）。"""
        out = []
        for cands in cands_per_obs:
            best = None
            for p in cands:
                d = math.hypot(p[0] - c[0], p[1] - c[1])
                if d <= self.cluster_m and (best is None or d < best[0]):
                    best = (d, p)
            if best is not None:
                out.append(best[1])
        return out

    def _alt_cluster(self, mx: float, my: float, cands_per_obs):
        """選んだ山 (mx, my) から離れた、もう 1 つの山（前後の取り違えの相手）。無ければ None。"""
        alt_sup = []
        for cands in cands_per_obs:
            for c in cands:
                if math.hypot(c[0] - mx, c[1] - my) > 2.0 * self.cluster_m:
                    sup = self._support(c, cands_per_obs)
                    if len(sup) > len(alt_sup):
                        alt_sup = sup
        if not alt_sup:
            return None
        ax_, ay_, asp = median_point(alt_sup)
        return ax_, ay_, asp, alt_sup

    def estimate(self) -> BeaconEstimate | None:
        if not self.obs:
            return None
        last_ant, last_dh, last_b, _, _ = self.obs[-1]
        cands_per_obs = [(o[3], o[4]) for o in self.obs if o[3] is not None]

        # ④ 距離だけの解（一直線でない 3 か所以上から測れていれば出る）
        tri = trilaterate([o[0][:2] for o in self.obs], [o[1] for o in self.obs]) if len(self.obs) >= 3 else None

        if not cands_per_obs:                           # 方向が一度も使えていない → 距離だけ
            if tri is None:
                return None
            return BeaconEstimate(tri[0], tri[1], len(self.obs), 0.0, "range", False,
                                  last_dh, math.degrees(wrap_angle(math.atan2(tri[1] - last_ant[1], tri[0] - last_ant[0]) - last_ant[2])),
                                  "range")

        # ② いちばん多くの測定に支持される候補を探す
        best_c, best_sup = None, []
        for cands in cands_per_obs:
            for c in cands:
                sup = self._support(c, cands_per_obs)
                if len(sup) > len(best_sup):
                    best_c, best_sup = c, sup
        mx, my, spread = median_point(best_sup)

        # 同じくらい支持される別の山があるか（止まったままだと前後の 2 つの山が同じ大きさ）
        second = 0
        for cands in cands_per_obs:
            for c in cands:
                if math.hypot(c[0] - mx, c[1] - my) > 2.0 * self.cluster_m:
                    second = max(second, len(self._support(c, cands_per_obs)))
        ambiguous = not (len(best_sup) >= 3 and len(best_sup) >= second + 2)
        resolved_by = "" if ambiguous else "turn"

        if ambiguous and tri is not None:               # 決め手 1: 距離だけの解に近い山を選ぶ
            alt = self._alt_cluster(mx, my, cands_per_obs)
            if alt is not None:
                ax_, ay_, asp, alt_sup = alt
                if math.hypot(ax_ - tri[0], ay_ - tri[1]) + 0.3 < math.hypot(mx - tri[0], my - tri[1]):
                    mx, my, spread, best_sup = ax_, ay_, asp, alt_sup
                ambiguous, resolved_by = False, "range"
        if ambiguous and self.prior is not None and len(best_sup) >= 3:
            # 決め手 2: 前回の場所。2 つの山のうち片方だけが近ければ、そちら（ユニットは動いていないとみなす）
            alt = self._alt_cluster(mx, my, cands_per_obs)
            if alt is not None:
                ax_, ay_, asp, alt_sup = alt
                px, py = self.prior
                near_best = math.hypot(mx - px, my - py) <= self.prior_m
                near_alt = math.hypot(ax_ - px, ay_ - py) <= self.prior_m
                if near_alt and not near_best and len(alt_sup) >= 3:
                    mx, my, spread, best_sup = ax_, ay_, asp, alt_sup
                    ambiguous, resolved_by = False, "prior"
                elif near_best and not near_alt:
                    ambiguous, resolved_by = False, "prior"
        # まだ決まらなければ、前の候補を仮に返す（支持が同数なら、先に見つかる＝前の候補が best になっている）

        # 最新の測定の方向: 選んだ山に近いほうの候補が「後ろ」なら鏡写しを戻す
        bearing = last_b
        if self.obs[-1][3] is not None and self.obs[-1][4] is not None:
            df = math.hypot(self.obs[-1][3][0] - mx, self.obs[-1][3][1] - my)
            db = math.hypot(self.obs[-1][4][0] - mx, self.obs[-1][4][1] - my)
            if db < df:
                bearing = mirror_bearing(last_b)
        else:
            bearing = math.degrees(wrap_angle(math.atan2(my - last_ant[1], mx - last_ant[0]) - last_ant[2]))
        return BeaconEstimate(mx, my, len(best_sup), spread, "bearing", ambiguous, last_dh, bearing, resolved_by)

    def last_raw_bearing(self) -> float | None:
        """最新の使えた測定の、基板が出したままの方向 [度]（鏡写しのまま。±60° の外の回も含む）。
        前後は分からないが、左右（符号）は鏡写しでも変わらないので、「どちらへ回れば相手が正面に寄るか」に使える。"""
        return self.obs[-1][2] if self.obs else None

    def latest_bearing(self) -> float | None:
        """最新の測定から見た相手の方向 [度]（前後の判定込み）。測定が無ければ None。"""
        e = self.estimate()
        return None if e is None else e.bearing_deg


# =====================================================================
#  ゴール（0.5 m 手前・ユニットのほうを向く）
# =====================================================================

def standoff_goal(e: BeaconEstimate, robot_pose, standoff: float = 0.5,
                  label: str = "ビーコン（UWB）") -> dict:
    """ロボからユニットへ向かう直線の上で、ユニットの standoff [m] 手前を Nav2 のゴールにする。
    向きはユニットのほう（機体の前＝本棚側を相手に向ける）。すでに手前より近ければその場で向きだけ合わせる。
    bookshelf_bridge.goal_from_stop_point() と同じ形の辞書を返す（beacon と standoff を足してある）。"""
    rx, ry, ryaw = robot_pose
    dx, dy = e.x - rx, e.y - ry
    dist = math.hypot(dx, dy)
    theta = math.atan2(dy, dx) if dist > 1e-6 else ryaw
    if dist <= standoff:
        gx, gy = rx, ry
    else:
        gx, gy = e.x - standoff * math.cos(theta), e.y - standoff * math.sin(theta)
    return {"frame_id": "map", "x": gx, "y": gy, "z": 0.0,
            "qz": math.sin(theta / 2.0), "qw": math.cos(theta / 2.0), "theta": theta,
            "label": label, "beacon": (e.x, e.y), "standoff": standoff}


# =====================================================================
#  逆向きの計算（シミュレータ・テスト用）: ユニットの位置 → 基板が出すはずの距離と方向
# =====================================================================

def simulate_reading(robot_pose, beacon_xy, mount: Mount | None = None,
                     beacon_height: float = P.BEACON_HEIGHT) -> tuple[float, float]:
    """ロボの位置とユニットの位置から、基板が出すはずの (斜め距離 d [m], 方向 b [度・左が＋]) を作る。
    本物の基板と同じく、後ろの相手は前に鏡写し（−90〜+90 の範囲）で返す。"""
    m = mount or Mount()
    ax, ay, ayaw = antenna_pose(robot_pose, m)
    dx, dy = beacon_xy[0] - ax, beacon_xy[1] - ay
    dh = math.hypot(dx, dy)
    dz = beacon_height - m.height
    d = math.sqrt(dh * dh + dz * dz)
    b = math.degrees(wrap_angle(math.atan2(dy, dx) - ayaw))
    if b > 90.0:
        b = 180.0 - b
    elif b < -90.0:
        b = -180.0 - b
    return d, b


# =====================================================================
#  コマンドラインから（計算の確かめ）
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description="UWB の距離と方向 → ユニットの地図座標（計算の確かめ）")
    ap.add_argument("--robot", nargs=3, type=float, metavar=("X", "Y", "YAW_DEG"), default=[0.0, 0.0, 0.0],
                    help="ロボの地図座標と向き [m, m, 度]")
    ap.add_argument("--d", type=float, required=True, help="基板の距離 [m]（D_cm ÷ 100）")
    ap.add_argument("--b", type=float, required=True, help="方向 [度]（bearing_deg。左が＋）")
    ap.add_argument("--beacon-height", type=float, default=P.BEACON_HEIGHT, help="席のユニットの床からの高さ [m]")
    args = ap.parse_args()
    pose = (args.robot[0], args.robot[1], math.radians(args.robot[2]))
    m = Mount()
    ant = antenna_pose(pose, m)
    dz = args.beacon_height - m.height
    dh = horizontal_distance(args.d, dz)
    f, bk = point_from(ant, dh, args.b), point_from(ant, dh, mirror_bearing(args.b))
    print(f"アンテナ: ({ant[0]:.3f}, {ant[1]:.3f}) 向き {math.degrees(ant[2]):.1f}°   取り付け x={m.x:+.2f} y={m.y:+.2f} yaw={math.degrees(m.yaw):.0f}° 高さ {m.height:.2f} m")
    print(f"高さの差 {dz:+.2f} m → 水平距離 {dh:.3f} m（斜め {args.d:.3f} m）")
    print(f"候補（前）: ({f[0]:.3f}, {f[1]:.3f})   方向 {args.b:+.1f}°")
    print(f"候補（後）: ({bk[0]:.3f}, {bk[1]:.3f})   方向 {mirror_bearing(args.b):+.1f}°   ← ロボが回ると、どちらかに決まる")


if __name__ == "__main__":
    main()
