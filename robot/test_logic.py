"""
test_logic.py ― 実機も pyserial も無しで、計算だけを検証する

    python test_logic.py

pyserial と requests をスタブ（にせもの）に差し替えて import するので、
何もインストールしていない PC でも走ります。
★パケットの形・順運動学の符号・量子化のヒステリシスを変えたら、必ずこれを実行★
"""

import contextlib
import io
import math
import os
import re
import struct
import sys
import threading
import time
import types

# --- pyserial / requests をスタブ化（インストール不要で走らせるため）---
_serial = types.ModuleType("serial")
_serial.Serial = type("Serial", (), {})
_serial.SerialException = type("SerialException", (Exception,), {})
_tools = types.ModuleType("serial.tools")
_lp = types.ModuleType("serial.tools.list_ports")
_lp.comports = lambda: []
_tools.list_ports = _lp
_serial.tools = _tools
sys.modules.update({"serial": _serial, "serial.tools": _tools,
                    "serial.tools.list_ports": _lp})
_req = types.ModuleType("requests")
_req.RequestException = Exception
sys.modules.setdefault("requests", _req)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import robot_params as P          # noqa: E402
import mecanum_serial as M        # noqa: E402
import bookshelf_bridge as B      # noqa: E402
import pin_tool as PT             # noqa: E402
import manual_control as MCTL     # noqa: E402  （requests は上でスタブ化済み。rclpy は RosDriver.__init__ の中でしか import しない）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "nav2"))
import make_nav2_params as NP     # noqa: E402  （yaml は main() の中でしか import しない）
import map_check as MC            # noqa: E402
import uwb_reader as U            # noqa: E402  （pyserial は open_serial() の中でしか import しない）
import beacon_locator as BL       # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK ' if good else 'NG '}] {label}: {got!r}"
          + ("" if good else f"   ★期待={want!r}★"))


# =====================================================================
print("=== build_packet（指令パケット）===")
# =====================================================================
pkt = M.build_packet(1, M.FORWARD, 30)
check("seq=1 motion=1 rpm=30", pkt.hex(" ").upper(), "A5 5A 01 01 1E 00 44")
check("長さ7", len(pkt), 7)
check("検算 = seq^motion^lo^hi^0x5A", pkt[6], 1 ^ 1 ^ 30 ^ 0 ^ 0x5A)
# ★これが 0 になると、全0のデータが「有効な停止指令」として通って振動する★
check("全0のデータは検算に合わない", (0 ^ 0 ^ 0 ^ 0 ^ 0x5A) != 0, True)
check("rpm は上限で切り詰める", M.build_packet(0, 1, 999)[4:6], bytes([P.MAX_RPM_HW, 0]))
check("連番は 0xFF の次が 0x00", M.build_packet(256, 0, 0)[2], 0)


# =====================================================================
print("\n=== read_frame（テレメトリの同期）===")
# =====================================================================
class FakeSer:
    def __init__(self, data):
        self.buf = io.BytesIO(data)

    def read(self, n=1):
        return self.buf.read(n)


# 連番10(0x000A)・前輪rpm×10=2570(0x0A0A) → 本文に 0x0A（改行）が3個入る
# 末尾の 6 値は IMU（ax ay az [mg], gx gy gz [0.1dps]）。静止・水平なら az ≒ +1000
vals = (10, -100, 2570, 120, -560, 12, -8, 1000, 3, -2, 450)
body = struct.pack("<11h", *vals)
check("本文 22 バイト + ヘッダ 2 = TLM_LEN(24)", len(body) + 2, M.TLM_LEN)
check("検証データに 0x0A が含まれる", body.count(b"\x0A") >= 3, True)
stream = b"\x00\xff\x5A\x5A\xA5" + body        # ゴミ + 0x5A の連続 + 本文
check("ゴミと二重ヘッダを越えて同期できる", M.read_frame(FakeSer(stream)), vals)
# ★readline() を使ってはいけない理由★
check("readline なら 0x0A で切れてしまう",
      len(stream.split(b"\x0A")[0]) < len(stream), True)
check("本文が足りなければ None（タイムアウト扱い）", M.read_frame(FakeSer(stream[:-1])), None)

print("  --- v1（12バイト）の Arduino を検出する ---")
# v1 のフレーム2個ぶん。24バイトとして読むと、本文の 10 バイト目に次のヘッダ 5A A5 が現れる
v1 = (b"\x5A\xA5" + struct.pack("<5h", 1, 0, 0, 0, 0)
      + b"\x5A\xA5" + struct.pack("<5h", 2, 0, 0, 0, 0))
try:
    M.read_frame(FakeSer(v1))
    check("v1 のフレームは FrameMismatch になる", "例外なし", "FrameMismatch")
except M.FrameMismatch:
    check("v1 のフレームは FrameMismatch になる", "FrameMismatch", "FrameMismatch")

print("  --- telemetry_from_frame（単位の換算）---")
tl = M.telemetry_from_frame(0.0, vals)
check("rpm は 10 で割る", (tl.rpm_front, tl.rpm_rear), (257.0, -56.0))
check("u はそのまま", (tl.u_front, tl.u_rear), (-100, 120))
check("加速度は mg → g", tl.acc, (0.012, -0.008, 1.0))
check("角速度は 0.1dps → dps", tl.gyro, (0.3, -0.2, 45.0))
check("IMU を省くと 0（v1 形式の Telemetry も作れる）", M.Telemetry(0.0, 0, 0, 0.0, 0, 0.0).acc, (0.0, 0.0, 0.0))


# =====================================================================
print("\n=== Quantizer（cmd_vel → 7動作）===")
# =====================================================================
q = M.Quantizer(use_vy=False, hold_sec=0.2, hysteresis=1.5)   # v1: 横移動は封印
t = 1000.0
check("前進", q(0.2, 0, 0.0, now=t)[0], M.FORWARD)
t += 1.0
check("後退", q(-0.2, 0, 0.0, now=t)[0], M.BACKWARD)
t += 1.0
m, r = q(0.0, 0, 1.0, now=t)
check("旋回", m, M.TURN_LEFT)
check("旋回rpm は wz×L で換算", round(r, 1), round(P.mps_to_rpm(1.0 * P.WHEEL_GEOM_L), 1))
t += 1.0
check("MIN_RPM 未満は停止に丸める", q(0.01, 0, 0.0, now=t), (M.STOP, 0.0))
t += 1.0
check("MAX_RPM_NAV で頭打ち", q(5.0, 0, 0.0, now=t)[1], P.MAX_RPM_NAV)

print("  --- ヒステリシス（拮抗時のパタパタ防止）---")
q2 = M.Quantizer(use_vy=False, hold_sec=0.2, hysteresis=1.5)
t = 2000.0
q2(0.30, 0, 0.0, now=t)                     # まず前進を選ばせる
out = []
for _ in range(6):                          # 前進と旋回がほぼ拮抗する指令を連続で
    t += 0.02
    out.append(q2(0.30, 0, 1.60, now=t)[0])
check("拮抗しても動作が変わらない", len(set(out)), 1)
t += 1.0
check("差が開けば切り替わる", q2(0.05, 0, 2.0, now=t)[0], M.TURN_LEFT)

print("  --- 停止への切替は即時（安全側）---")
q3 = M.Quantizer(use_vy=False, hold_sec=5.0)
t = 3000.0
q3(0.30, 0, 0.0, now=t)
t += 0.01
check("hold 中でも停止は通る", q3(0.0, 0, 0.0, now=t), (M.STOP, 0.0))


# =====================================================================
print("\n=== body_velocity（順運動学。符号は動作表と一致すること）===")
# =====================================================================
link = M.MecanumLink.__new__(M.MecanumLink)


def tlm(front, rear):
    return M.Telemetry(0.0, 0, 0, front, 0, rear)


link.tlm_left, link.tlm_right = tlm(50, 50), tlm(50, 50)            # 前進
vx, vy, wz = link.body_velocity()
check("前進 vx = 車輪周速", round(vx, 4), round(P.rpm_to_mps(50), 4))
check("前進 vy = 0", round(vy, 6), 0.0)
check("前進 wz = 0", round(wz, 6), 0.0)

link.tlm_left, link.tlm_right = tlm(50, -50), tlm(-50, 50)          # 左横 FL+ RL- FR- RR+
vx, vy, wz = link.body_velocity()
check("左横 vx = 0", round(vx, 6), 0.0)
check("左横 vy > 0（左が正）", vy > 0, True)
check("左横 vy = 周速 × K_STRAFE", round(vy, 4), round(P.rpm_to_mps(50) * P.K_STRAFE, 4))

link.tlm_left, link.tlm_right = tlm(-50, -50), tlm(50, 50)          # 左回転 FL- RL- FR+ RR+
vx, vy, wz = link.body_velocity()
check("左回転 wz > 0（反時計が正）", wz > 0, True)
check("左回転 wz = 周速 / L", round(wz, 4), round(P.rpm_to_mps(50) / P.WHEEL_GEOM_L, 4))


# =====================================================================
print("\n=== stalled_wheels（暴走防止ラッチの検出。v2）===")
# =====================================================================
link2 = M.MecanumLink.__new__(M.MecanumLink)
link2._zero_frames = {"FL": 0, "RL": 0, "FR": 0, "RR": 0}
link2._moving_since = 100.0                          # t=100 から 0 でない指令を出している
zero = M.Telemetry(0.0, 0, 0, 0.0, 0, 0.0)           # u=0・rpm=0（止まったまま）
alive = M.Telemetry(0.0, 0, 120, 30.0, 118, 29.8)    # 回っている
for _ in range(M.STALL_FRAMES):
    link2._update_stall("left", zero)
    link2._update_stall("right", alive)
check("動き出し直後（猶予中）は疑わない", link2.stalled_wheels(now=100.1), [])
check("左の2輪だけが疑わしい", link2.stalled_wheels(now=101.0), ["FL", "RL"])
link2._update_stall("left", alive)
check("回り出したら解除", link2.stalled_wheels(now=101.0), [])
link2._moving_since = None                           # 停止指令中
for _ in range(M.STALL_FRAMES):
    link2._update_stall("left", zero)
check("停止指令中は u=0・rpm=0 で正常", link2.stalled_wheels(now=200.0), [])

print("  --- yaw_rate（IMU 角速度 Z の左右平均）---")
link2.tlm_left = M.Telemetry(0.0, 0, 0, 0.0, 0, 0.0, gyro=(0.0, 0.0, 30.0))
link2.tlm_right = M.Telemetry(0.0, 0, 0, 0.0, 0, 0.0, gyro=(0.0, 0.0, 34.0))
check("左右の平均を rad/s で返す", round(link2.yaw_rate(), 4), round(math.radians(32.0), 4))
link2.tlm_right = None
check("片方でも欠けたら None", link2.yaw_rate(), None)


# =====================================================================
print("\n=== goal_from_stop_point（席座標 → クォータニオン）===")
# =====================================================================
g = B.goal_from_stop_point({"label": "席A", "x": 1.85, "y": 0.42, "theta": math.pi / 2})
check("qz = sin(θ/2)", round(g["qz"], 4), 0.7071)
check("qw = cos(θ/2)", round(g["qw"], 4), 0.7071)
g0 = B.goal_from_stop_point({"label": "ホーム", "x": 0, "y": 0, "theta": 0})
check("θ=0 → (qz,qw)=(0,1)", (round(g0["qz"], 6), round(g0["qw"], 6)), (0.0, 1.0))
check("theta が NULL でも落ちない",
      B.goal_from_stop_point({"x": 0, "y": 0, "theta": None})["qw"], 1.0)
check("yaw_from_quat: (qz,qw)=(0.7071,0.7071) → π/2",
      round(B.yaw_from_quat(0, 0, 0.7071068, 0.7071068), 4), round(math.pi / 2, 4))


# =====================================================================
print("\n=== Bridge（段階3の状態機械。Supabase と Nav2 は偽物）===")
# =====================================================================
#   idle → localizing → moving → arrived →（本を取得した）→ returning → idle
#   robot_calls: queued → moving → arrived → done


class FakeSupa:
    """robot_status / robot_calls / stop_points を辞書で持つ Supabase のにせもの。"""

    def __init__(self, stop_points, v3=True):
        self.sp = {int(r["id"]): dict(r) for r in stop_points}
        self.v3 = v3                      # sql/03（kind・pose_x・detail）が入っているか
        self.calls = {}                   # call_id → status
        self.status = {"state": "idle", "current_call_id": None}
        self.log = []                     # patch の履歴 (table, body)

    def select(self, table, query=""):
        if table == "robot_status":
            if "pose_x" in query and not self.v3:
                raise RuntimeError("400 column robot_status.pose_x does not exist")
            return [dict(self.status)]
        if table == "stop_points":
            if "kind=eq.home" in query:
                if not self.v3:
                    raise RuntimeError("400 column stop_points.kind does not exist")
                return [dict(r) for r in self.sp.values() if r.get("kind") == "home"]
            i = int(re.search(r"id=eq\.(-?\d+)", query).group(1))
            return [dict(self.sp[i])] if i in self.sp else []
        if table == "robot_calls":
            m = re.search(r"id=eq\.([\w-]+)", query)
            if m:                          # 受取待ちの確認: arrived なら即 done（アプリが押した扱い）
                st = self.calls.get(m.group(1), "queued")
                if st == "arrived":
                    st = self.calls[m.group(1)] = "done"
                return [{"status": st}]
            return []
        raise AssertionError(table)

    def patch(self, table, query, body):
        self.log.append((table, dict(body)))
        if table == "robot_calls":
            self.calls[re.search(r"id=eq\.([\w-]+)", query).group(1)] = body["status"]
        elif table == "robot_status":
            self.status.update(body)
        elif table == "stop_points":
            i = int(re.search(r"id=eq\.(-?\d+)", query).group(1))
            self.sp.setdefault(i, {"id": i}).update(body)
        return [dict(body)]

    def states(self):
        return [b["state"] for t, b in self.log if t == "robot_status"]

    def call_states(self):
        return [b["status"] for t, b in self.log if t == "robot_calls"]


class FakeNav:
    """呼ばれた順番を記録するナビ。fail_go_at=n で n 回目の go を失敗させる。"""

    def __init__(self, fail_go_at=None, fail_localize=False, interrupt_go=False, interrupt_go_at=None):
        self.events = []
        self.goals = []                   # go に渡されたゴール (x, y, theta)
        self.pose = (0.0, 0.0, 0.0)
        self.n_go = 0
        self.fail_go_at = fail_go_at
        self.fail_localize = fail_localize
        self.interrupt_go = interrupt_go
        self.interrupt_go_at = interrupt_go_at   # n 回目の go で Ctrl-C（2 なら帰還中）

    def localize(self, init_goal):
        self.events.append(("localize", None if init_goal is None else init_goal["label"]))
        return not self.fail_localize

    def go(self, goal, on_progress=None):
        self.n_go += 1
        self.events.append(("go", goal["label"]))
        self.goals.append((goal["x"], goal["y"], goal["theta"]))
        if self.interrupt_go or self.n_go == self.interrupt_go_at:
            raise KeyboardInterrupt
        if on_progress:
            self.pose = (goal["x"] / 2, goal["y"] / 2, goal["theta"])      # 途中（ゴールの半分）まで来たことにする
            new_goal = on_progress(1.23, self.pose)
            if new_goal:                                                   # 走りながらゴールを差し替える（ビーコン）
                self.events.append(("retarget", round(new_goal["x"], 2), round(new_goal["y"], 2)))
                goal = new_goal
        fails = self.fail_go_at if isinstance(self.fail_go_at, (tuple, list, set)) else (self.fail_go_at,)
        if self.n_go in fails:
            return False
        self.pose = (goal["x"], goal["y"], goal["theta"])
        return True

    def turn(self, angle):
        x, y, yaw = self.pose
        self.pose = (x, y, math.atan2(math.sin(yaw + angle), math.cos(yaw + angle)))
        self.events.append(("turn", round(math.degrees(angle))))
        return True

    def current_pose(self):
        return self.pose

    def cancel(self):
        self.events.append(("cancel",))


PINS = [
    {"id": 0, "label": "本棚の場所", "x": 0.0, "y": 0.0, "theta": 0.0, "kind": "home"},
    {"id": 1, "label": "席1", "x": 1.0, "y": 0.5, "theta": 1.57, "kind": "seat"},
    {"id": 2, "label": "席2", "x": 2.0, "y": 0.0, "theta": 0.0, "kind": "seat"},
]
BEACON_PIN = {"id": 100, "label": "ビーコン（UWB）", "x": 0.0, "y": 0.0, "theta": 0.0, "kind": "beacon"}   # sql/05


def make_bridge(supa, nav, **kw):
    home = B.load_home(supa)
    opts = dict(live=True, arrive_hold=0.0, home=home, pose_interval=0.0)
    opts.update(kw)
    return B.Bridge(supa, nav, **opts)


def call(cid, seat):
    return {"id": cid, "book_id": "b" * 36, "seat_id": seat, "requested_by": "test"}


print("  --- 正常系: 自己位置推定 → 席2 → 受取 → 本棚へ帰還 ---")
s, n = FakeSupa(PINS), FakeNav()
br = make_bridge(s, n)
check("sql/03 適用済みと判定", br.v3, True)
check("ホームは kind='home' の行", br.home["id"], 0)
br.handle(call("c1", 2))
check("ナビ呼び出しの順番", n.events,
      [("localize", "本棚の場所"), ("go", "席2"), ("go", "本棚の場所")])
check("robot_status の遷移（moving / returning の 2 回目は走行中の進捗書き込み）",
      s.states(),
      ["localizing", "moving", "moving", "arrived", "returning", "returning", "idle"])
check("robot_calls の遷移", s.call_states(), ["moving", "arrived"])
check("受取後は done（アプリが進める）", s.calls["c1"], "done")
check("最後は idle・current_call_id なし",
      (s.status["state"], s.status["current_call_id"]), ("idle", None))
check("帰還後は本棚の場所に居る扱い", br.at_home, True)
check("処理が終わったら current は空", br.current, None)
moving_body = next(b for t, b in s.log if t == "robot_status" and b["state"] == "moving")
check("moving に現在地（pose_x）と detail を書く",
      ("pose_x" in moving_body, "席2" in (moving_body.get("detail") or "")), (True, True))
prog = [b for t, b in s.log if t == "robot_status" and "残り" in (b.get("detail") or "")]
check("走行中の進捗（残り x m）を書く", len(prog) >= 1 and "1.2" in prog[0]["detail"], True)
check("到着の detail に「本を取得した」の案内",
      any("本を取得した" in (b.get("detail") or "") for t, b in s.log if b.get("state") == "arrived"), True)

print("  --- 2 件目: Nav2 で帰ってきたあとは、ピンの座標で AMCL の推定を上書きしない ---")
# 帰り着いた位置はピンから「8 cm・30°」以内のどこか。ピンの座標を送り直すと、合っている推定をずらしてしまう
check("1 件目のあとは、ピンの座標を初期位置として信用しない", br.home_pose_trusted, False)
br.handle(call("c2", 1))
check("2 件目は初期位置を送らず、いまの推定から", n.events[3], ("localize", None))
check("2 件目の表示は「現在の推定位置から継続」",
      [b.get("detail") for t, b in s.log if t == "robot_status" and b.get("state") == "localizing"][-1],
      "自己位置推定中（現在の推定位置から継続）")
check("同じ id は二度処理しない", (br.handle(call("c1", 1)), n.n_go), (None, 4))

print("  --- 走行失敗: canceled にして idle。本棚に居ないので次は初期位置を送らない ---")
s, n = FakeSupa(PINS), FakeNav(fail_go_at=1)
br = make_bridge(s, n)
br.handle(call("c3", 1))
check("失敗した呼出は canceled", s.calls["c3"], "canceled")
check("状態は idle・detail に「失敗」", (s.status["state"], "失敗" in s.status["detail"]), ("idle", True))
check("帰還はしない（go は 1 回だけ）", n.n_go, 1)
check("本棚に居ない扱い", br.at_home, False)
br.handle(call("c4", 2))
check("次の呼出は初期位置を送らず追跡を継続", n.events[2], ("localize", None))

print("  --- 帰還失敗 ---")
s, n = FakeSupa(PINS), FakeNav(fail_go_at=2)
br = make_bridge(s, n)
br.handle(call("c5", 2))
check("呼出自体は done のまま", s.calls["c5"], "done")
check("idle・detail に「帰還に失敗」", (s.status["state"], "帰還に失敗" in s.status["detail"]), ("idle", True))
check("本棚に居ない扱い", br.at_home, False)

print("  --- 自己位置推定の失敗 ---")
s, n = FakeSupa(PINS), FakeNav(fail_localize=True)
br = make_bridge(s, n)
br.handle(call("c6", 2))
check("走らずに canceled", (s.calls["c6"], n.n_go), ("canceled", 0))
check("detail に「自己位置推定に失敗」", "自己位置推定に失敗" in s.status["detail"], True)

print("  --- 呼出先の検査 ---")
s, n = FakeSupa(PINS), FakeNav()
br = make_bridge(s, n)
br.handle(call("c7", 0))
check("seat_id=0（本棚の場所）は canceled", (s.calls["c7"], n.events), ("canceled", []))
br.handle(call("c8", 99))
check("存在しない席は canceled", (s.calls["c8"], n.events), ("canceled", []))
s, n = FakeSupa(PINS + [BEACON_PIN]), FakeNav()
br = make_bridge(s, n)
br.handle(call("c8b", 100))
check("ビーコン（kind=beacon）は、UWB なし（--uwb 無し）なら走らず canceled", (s.calls["c8b"], n.events), ("canceled", []))
check("理由を detail に書く（アプリに出る）", "--uwb" in (s.status.get("detail") or ""), True)

print("  --- ホーム未登録 / 帰還なし / 自己位置推定なし ---")
s, n = FakeSupa([p for p in PINS if p["id"] != 0]), FakeNav()
br = make_bridge(s, n)
check("ホームが無ければ None", br.home, None)
br.handle(call("c9", 1))
check("初期位置は送らず、帰還もしない", n.events, [("localize", None), ("go", "席1")])
check("detail に「ホーム未登録」", "ホーム未登録" in s.status["detail"], True)
s, n = FakeSupa(PINS), FakeNav()
br = make_bridge(s, n, return_home=False, localize=False)
br.handle(call("c10", 1))
check("--no-localize / --no-return-home", n.events, [("go", "席1")])
check("localizing を書かない", "localizing" in s.states(), False)

print("  --- sql/03 未適用（旧スキーマ）でも動く ---")
s, n = FakeSupa(PINS, v3=False), FakeNav()
br = make_bridge(s, n)
check("v3 でないと判定", br.v3, False)
check("ホームは id=0 の行で代用", br.home["label"], "本棚の場所")
br.handle(call("c11", 2))
check("localizing は DB に書かない（CHECK 制約で弾かれるため）", "localizing" in s.states(), False)
check("pose_x / detail も書かない",
      any(("pose_x" in b or "detail" in b) for t, b in s.log if t == "robot_status"), False)
check("それ以外の遷移は同じ", s.states(), ["moving", "moving", "arrived", "returning", "returning", "idle"])

print("  --- Ctrl-C（走行中）→ shutdown で取り消して止める ---")
s, n = FakeSupa(PINS), FakeNav(interrupt_go=True)
br = make_bridge(s, n)
try:
    br.handle(call("c12", 1))
    check("KeyboardInterrupt は握りつぶさない", "例外なし", "KeyboardInterrupt")
except KeyboardInterrupt:
    check("KeyboardInterrupt は握りつぶさない", "KeyboardInterrupt", "KeyboardInterrupt")
check("中断中の呼出を覚えている", br.current["id"], "c12")
br.shutdown()
check("shutdown: ナビを止め、呼出を canceled、idle に戻す",
      (n.events[-1], s.calls["c12"], s.status["state"]), (("cancel",), "canceled", "idle"))

print("  --- Ctrl-C（受取後の帰還中）→ done の呼出は canceled に戻さない ---")
s, n = FakeSupa(PINS), FakeNav(interrupt_go_at=2)
br = make_bridge(s, n)
try:
    br.handle(call("c13", 1))
    check("KeyboardInterrupt は握りつぶさない", "例外なし", "KeyboardInterrupt")
except KeyboardInterrupt:
    check("KeyboardInterrupt は握りつぶさない", "KeyboardInterrupt", "KeyboardInterrupt")
check("中断は 2 回目の go（帰還）で起きた", n.events[-1], ("go", "本棚の場所"))
check("受取まで済んでいる（done）", s.calls["c13"], "done")
br.shutdown()
check("shutdown: ナビは止めるが、呼出は done のまま・idle に戻す",
      (n.events[-1], s.calls["c13"], s.status["state"]), (("cancel",), "done", "idle"))
check("detail に「帰還を中断」「完了済み」と書く",
      ("帰還を中断" in s.status["detail"], "完了済み" in s.status["detail"]), (True, True))
check("robot_calls への canceled 書き込みは無い", "canceled" in s.call_states(), False)

print("  --- 起動時の自己位置推定（--nav2 のとき main が呼ぶ）---")
s, n = FakeSupa(PINS), FakeNav()
br = make_bridge(s, n)
check("本棚の場所を初期位置として与える", (br.startup_localize(), n.events), (True, [("localize", "本棚の場所")]))
check("idle のまま detail に「自己位置推定済み」・現在地も書く",
      (s.status["state"], "自己位置推定済み" in s.status["detail"], "pose_x" in s.status), ("idle", True, True))
s, n = FakeSupa(PINS), FakeNav(fail_localize=True)
br = make_bridge(s, n)
check("失敗しても止まらず detail で知らせる", (br.startup_localize(), "失敗" in s.status["detail"]), (False, True))
s, n = FakeSupa([p for p in PINS if p["id"] != 0]), FakeNav()
check("ホーム未登録なら何もしない", (make_bridge(s, n).startup_localize(), n.events, s.log), (False, [], []))
s, n = FakeSupa(PINS), FakeNav()
check("--no-localize なら何もしない", (make_bridge(s, n, localize=False).startup_localize(), n.events), (False, []))

print("  --- 起動時の片付け: 前回の取り残し（moving / arrived のまま残った呼出）を canceled にする ---")


class StaleSupa(FakeSupa):
    """robot_calls を行として持つ版。PostgREST の status=in.(…) と、条件つきの patch をまねる。"""

    def __init__(self, calls, state="idle", current=None):
        super().__init__(PINS)
        self.rows = [dict(c) for c in calls]
        self.status = {"state": state, "current_call_id": current}
        self.race = set()                 # select のあと patch の前に、アプリが done にした呼出の id
        self.patch_queries = []

    def select(self, table, query=""):
        if table == "robot_calls" and "status=in." in query:
            want = re.search(r"status=in\.\(([^)]*)\)", query).group(1).split(",")
            return [dict(r) for r in self.rows if r["status"] in want]
        return super().select(table, query)

    def patch(self, table, query, body):
        if table != "robot_calls":
            return super().patch(table, query, body)
        self.log.append((table, dict(body)))
        self.patch_queries.append(query)
        cid = re.search(r"id=eq\.([\w-]+)", query).group(1)
        cond = re.search(r"status=in\.\(([^)]*)\)", query)
        out = []
        for r in self.rows:
            if r["id"] != cid:
                continue
            if cid in self.race:
                r["status"] = "done"
            if cond and r["status"] not in cond.group(1).split(","):
                continue                  # 条件に合わない行は更新されない（PostgREST は空の配列を返す）
            r.update(body)
            out.append(dict(r))
        return out

    def by_id(self):
        return {r["id"]: r["status"] for r in self.rows}


def stale_call(cid, status, who="guest"):
    return {"id": cid, "status": status, "seat_id": 1, "requested_by": who, "book_id": "b" * 36}


LEFT = [stale_call("m1", "moving", "A"), stale_call("a1", "arrived", "B"), stale_call("q1", "queued", "C"),
        stale_call("d1", "done", "D"), stale_call("x1", "canceled", "E")]

s, n = StaleSupa([stale_call("q1", "queued"), stale_call("d1", "done")]), FakeNav()
with contextlib.redirect_stdout(io.StringIO()) as _clog:
    got = make_bridge(s, n).cleanup_stale_calls()
check("取り残しが無ければ何もしない（書き込みなし・表示なし）", (got, s.log, _clog.getvalue()), (0, [], ""))

s, n = StaleSupa(LEFT, state="moving", current="m1"), FakeNav()
got = make_bridge(s, n).cleanup_stale_calls()
check("moving と arrived の 2 件を取り消す", got, 2)
check("moving / arrived → canceled。queued・done・canceled には触らない", s.by_id(),
      {"m1": "canceled", "a1": "canceled", "q1": "queued", "d1": "done", "x1": "canceled"})
check("書き込みは「いまも moving / arrived なら」の条件つき",
      all("status=in.(moving,arrived)" in q for q in s.patch_queries), True)
check("robot_status は idle・current_call_id なし・detail に件数",
      (s.status["state"], s.status["current_call_id"], "2 件" in (s.status.get("detail") or "")), ("idle", None, True))
check("ナビには何も送らない（走らせない）", n.events, [])

s, n = StaleSupa(LEFT, state="moving", current="m1"), FakeNav()
with contextlib.redirect_stdout(io.StringIO()) as _clog:
    got = make_bridge(s, n, live=False).cleanup_stale_calls()
check("dry-run: 件数は数えるが、DB は書き換えない", (got, s.by_id()["m1"], s.by_id()["a1"], s.status["state"], s.log),
      (2, "moving", "arrived", "moving", []))
check("dry-run: 書き込まないことを表示する", "dry-run なので書き込みません" in _clog.getvalue(), True)

s, n = StaleSupa([stale_call("a1", "arrived"), stale_call("m1", "moving")], state="arrived", current="a1"), FakeNav()
s.race.add("a1")                          # 片付けの直前に、アプリで「本を取得した」が押された
got = make_bridge(s, n).cleanup_stale_calls()
check("書く直前に done になった呼出は、取り消さない（届いた本を「届かなかった」にしない）",
      (got, s.by_id()), (1, {"a1": "done", "m1": "canceled"}))

s, n = StaleSupa([stale_call("d1", "done")], state="returning", current="d1"), FakeNav()
got = make_bridge(s, n).cleanup_stale_calls()
check("帰還中に止まっていた（呼出は done）: 呼出には触らず、robot_status だけ idle に戻す",
      (got, s.by_id(), s.status["state"], s.status["current_call_id"], "returning" in (s.status.get("detail") or "")),
      (0, {"d1": "done"}, "idle", None, True))

import inspect as _inspect     # noqa: E402
_main_src = _inspect.getsource(B.main)
check("main は、初期位置を送る前に取り残しを片付ける",
      0 < _main_src.find("bridge.cleanup_stale_calls()") < _main_src.find("bridge.startup_localize()"), True)

print("  --- LoggingNavigator（--simulate 0）---")
ln = B.LoggingNavigator(0.0, start_pose=(1.0, 1.0, 0.0))
check("localize は初期位置に飛ぶ", (ln.localize(B.goal_from_stop_point(PINS[0])), ln.pose), (True, (0.0, 0.0, 0.0)))
check("go でゴールに着いたことにする",
      (ln.go(B.goal_from_stop_point(PINS[2])), ln.current_pose()), (True, (2.0, 0.0, 0.0)))


# =====================================================================
print("\n=== pin_tool（ピン建ての計算）===")
# =====================================================================
check("yaw_from_quat: 90°", round(PT.yaw_from_quat(0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)), 4),
      round(math.pi / 2, 4))
check("normalize_angle: 4 rad → 負側へ畳む", round(PT.normalize_angle(4.0), 4), round(4.0 - 2 * math.pi, 4))
check("median: 偶数個は中央 2 つの平均", PT.median([3, 1, 2, 10]), 2.5)
mp = PT.median_pose([(1.0, 2.0, 3.10), (1.1, 2.1, -3.12), (0.9, 1.9, 3.14)])
check("median_pose: ±π をまたぐ向きでも壊れない", (mp[0], mp[1], abs(abs(mp[2]) - math.pi) < 0.05), (1.0, 2.0, True))
r = PT.pin_row(0, 0.1234, -0.5, 7.0)
check("pin_row: id=0 は home・theta は -π〜π に畳む・3 桁に丸める",
      (r["kind"], r["label"], r["x"], round(r["theta"], 4)), ("home", "本棚の場所", 0.123, round(7.0 - 2 * math.pi, 4)))
check("pin_row: id≥1 は seat・ラベル既定は 席N", (PT.pin_row(3, 0, 0, 0)["kind"], PT.pin_row(3, 0, 0, 0)["label"]),
      ("seat", "席3"))
try:
    PT.pin_row(-1, 0, 0, 0)
    check("pin_row: 負の id は拒否", "例外なし", "ValueError")
except ValueError:
    check("pin_row: 負の id は拒否", "ValueError", "ValueError")
sql = PT.sql_for_pins([PT.pin_row(1, 1.0, 2.0, 0.5, "A's seat")])
check("sql_for_pins: upsert 文で、クォートをエスケープ",
      ("on conflict (id) do update" in sql, "'A''s seat'" in sql, "'seat'" in sql), (True, True, True))


# =====================================================================
print("\n=== make_nav2_params（Nav2 設定の重ね合わせ）===")
# =====================================================================
base = {
    "amcl": {"ros__parameters": {"robot_model_type": "nav2_amcl::DifferentialMotionModel", "alpha1": 0.2,
                                 "base_frame_id": "base_footprint"}},
    "controller_server": {"ros__parameters": {
        "general_goal_checker": {"plugin": "nav2_controller::SimpleGoalChecker", "stateful": True,
                                 "xy_goal_tolerance": 0.25},
        "FollowPath": {"plugin": "nav2_mppi_controller::MPPIController", "vx_max": 0.5, "batch_size": 2000}}},
    "local_costmap": {"local_costmap": {"ros__parameters": {"robot_radius": 0.22}}},
    "global_costmap": {"global_costmap": {"ros__parameters": {"footprint": "[[0.1,0.1],[-0.1,-0.1]]"}}},
    "behavior_server": {"ros__parameters": {"behavior_plugins": ["spin", "backup", "wait"]}},
}
over = {
    "amcl": {"ros__parameters": {"robot_model_type": "nav2_amcl::OmniMotionModel", "alpha5": 0.3}},
    "controller_server": {"ros__parameters": {
        "general_goal_checker": {"plugin": "nav2_controller::SimpleGoalChecker", "xy_goal_tolerance": 0.08},
        "FollowPath": {"plugin": "nav2_rotation_shim_controller::RotationShimController",
                       "desired_linear_vel": 0.25}}},
}
merged, changes = NP.deep_merge(base, over)
amcl = merged["amcl"]["ros__parameters"]
check("同じキーは上書き・無いキーは追加・触らないキーは残る",
      (amcl["robot_model_type"], amcl["alpha5"], amcl["alpha1"], amcl["base_frame_id"]),
      ("nav2_amcl::OmniMotionModel", 0.3, 0.2, "base_footprint"))
gc = merged["controller_server"]["ros__parameters"]["general_goal_checker"]
check("plugin が同じ節は重ねる（stateful は標準の true のまま）", (gc["xy_goal_tolerance"], gc["stateful"]), (0.08, True))
fp = merged["controller_server"]["ros__parameters"]["FollowPath"]
check("plugin が変わる節は節ごと差し替え（MPPI のキーを残さない）",
      (fp["desired_linear_vel"], "vx_max" in fp, "batch_size" in fp), (0.25, False, False))
check("元の dict は書き換えない",
      base["controller_server"]["ros__parameters"]["FollowPath"]["plugin"], "nav2_mppi_controller::MPPIController")
check("差分に無い節はそのまま", merged["behavior_server"], base["behavior_server"])
check("変更点の一覧（値が同じものは数えない）", len(changes), 4)
check("追加されたキーの旧値は MISSING",
      [c for c in changes if c[0][-1] == "alpha5"][0][1] is NP.MISSING, True)
warns = NP.set_robot_radius(merged, 0.25, changes)
check("robot_radius を設定する", merged["local_costmap"]["local_costmap"]["ros__parameters"]["robot_radius"], 0.25)
check("footprint の節は触らず警告",
      (len(warns), "robot_radius" in merged["global_costmap"]["global_costmap"]["ros__parameters"]), (1, False))
check("null と空リストを見つける",
      NP.find_unsupported({"a": {"b": None, "c": [], "d": [1], "e": 0}}), [("a", "b"), ("a", "c")])
check("必須の値の検算に使うパスが読める",
      NP.get_path(merged, NP.MUST_HAVE[0][0]), NP.MUST_HAVE[0][1])
check("無いパスは MISSING", NP.get_path(merged, ("x", "y")) is NP.MISSING, True)


# =====================================================================
print("\n=== Nav2 の起動待ちの順番（amcl → 初期位置 → bt_navigator）===")
# =====================================================================
# 2026-09-20 実機: 「bt_navigator が active になってから初期位置を送る」順だと、Nav2 とお互いに待ち続けた。
# ROS 2 が無い PC でも確かめられるよう、BasicNavigator の代わりに呼ばれた順を記録する偽物を差し込む。
import inspect


class _FakeBasicNav:
    def __init__(self):
        self.waited = []

    def _waitForNodeToActivate(self, name):
        self.waited.append(name)


init_src = inspect.getsource(B.Nav2Navigator.__init__)
check("__init__ は amcl だけを待つ（bt_navigator を待つと Nav2 とお互いに待ち続ける）",
      ('_waitForNodeToActivate("amcl")' in init_src, '_waitForNodeToActivate("bt_navigator")' in init_src),
      (True, False))

nv = B.Nav2Navigator.__new__(B.Nav2Navigator)      # ROS を import する __init__ は通さない
nv.nav = _FakeBasicNav()
nv._nav_ready = False
nv.wait_navigation_ready()
check("wait_navigation_ready は bt_navigator を待つ", nv.nav.waited, ["bt_navigator"])
nv.wait_navigation_ready()
check("2 回目は待たない（一度 active を確認したら覚えておく）", nv.nav.waited, ["bt_navigator"])

go_src = inspect.getsource(B.Nav2Navigator.go)
check("go() はゴールを送る前に Nav2 の準備を確かめる",
      go_src.index("wait_navigation_ready()") < go_src.index("goToPose"), True)

main_src = inspect.getsource(B.main)
check("main は 初期位置（startup_localize）→ bt_navigator 待ち の順",
      main_src.index("startup_localize()") < main_src.index("wait_navigation_ready()"), True)


# =====================================================================
print("\n=== map_check（保存した地図とピンの点検）===")
# =====================================================================
# 20 × 12 マス（1 マス 0.1 m ＝ 2.0 × 1.2 m）の部屋を作る。外周が壁、真ん中に縦の仕切り（上に 0.3 m のすき間）。
#   画像の 1 行目が「上の端」（y が大きい側）。origin は左下の角。
import tempfile

MW, MH = 20, 12
rows_px = []
for r in range(MH):                     # r=0 が上の端
    line = []
    for c in range(MW):
        wall = r in (0, MH - 1) or c in (0, MW - 1)
        divider = c == 10 and r >= 4    # 仕切りは下から上へ。上の 3 マス（r=1..3）が通り道
        line.append(0 if (wall or divider) else 254)
    rows_px.append(line)
rows_px[5][15] = 205                    # 未知のマスを 1 つ
pgm = b"P5\n# comment line\n%d %d\n255\n" % (MW, MH) + bytes(v for line in rows_px for v in line)

w_, h_, maxval_, px_ = MC.parse_pgm(pgm)
check("PGM（P5・コメント行つき）の大きさと画素数", (w_, h_, maxval_, len(px_)), (MW, MH, 255, MW * MH))
w2, h2, _, px2 = MC.parse_pgm(b"P2 2 2 255 0 254 205 254")
check("PGM（P2・テキスト）も読める", (w2, h2, px2), (2, 2, [0, 254, 205, 254]))
try:
    MC.parse_pgm(b"P5 4 4 255 " + bytes(5))
    check("画素が足りない PGM はエラーにする", "例外なし", "ValueError")
except ValueError:
    check("画素が足りない PGM はエラーにする", "ValueError", "ValueError")

yaml_text = "image: room.pgm\nmode: trinary\nresolution: 0.1\norigin: [-1.0, -0.5, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n"
meta_ = MC.parse_map_yaml(yaml_text)
check("yaml: 解像度・原点・画像名", (meta_["resolution"], meta_["origin"], meta_["image"]), (0.1, [-1.0, -0.5, 0.0], "room.pgm"))

with tempfile.TemporaryDirectory() as td:
    with open(os.path.join(td, "room.pgm"), "wb") as f:
        f.write(pgm)
    with open(os.path.join(td, "room.yaml"), "w", encoding="utf-8") as f:
        f.write(yaml_text)
    gm = MC.load_map(os.path.join(td, "room.yaml"))

cnt = gm.counts()
check("分類: 未知は 1 マス、空き＋障害物＋未知＝全マス",
      (cnt[MC.UNKNOWN], cnt[MC.FREE] + cnt[MC.OCC] + cnt[MC.UNKNOWN]), (1, MW * MH))
check("範囲: x −1.0〜+1.0、y −0.5〜+0.7", tuple(round(v, 6) for v in gm.bounds()), (-1.0, 1.0, -0.5, 0.7))
check("左下の角のマスは（列 0, いちばん下の行）", gm.world_to_cell(-0.95, -0.45), (0, MH - 1))
check("左上の角のマスは（列 0, 行 0）", gm.world_to_cell(-0.95, 0.65), (0, 0))
check("地図の外は None", gm.world_to_cell(1.5, 0.0), None)
cx_, cy_ = gm.cell_center(0, MH - 1)
check("マスの中心 → 座標（左下）", (round(cx_, 6), round(cy_, 6)), (-0.95, -0.45))

# 左の部屋の真ん中 (-0.45, 0.05): 左の壁の中心 x=-0.95 まで 0.5 m、仕切り x=0.05 まで 0.5 m
check("障害物までの距離（部屋の真ん中）", round(gm.clearance(-0.45, 0.05), 6), 0.5)
check("判定: 真ん中は OK", MC.judge_point(gm, -0.45, 0.05, 0.21)[0], "OK")
check("判定: 壁から 0.15 m は NG（半径 0.21 より近い）", MC.judge_point(gm, -0.80, 0.05, 0.21)[0], "NG")
check("判定: 壁から 0.24 m は WARN（ぎりぎり）", MC.judge_point(gm, -0.71, 0.05, 0.21)[0], "WARN")
check("判定: 壁のマスの上は NG", MC.judge_point(gm, -0.95, 0.05, 0.21)[0], "NG")
check("判定: 地図の外は NG", MC.judge_point(gm, 3.0, 0.0, 0.21)[0], "NG")

# 道: 仕切りのすき間は 3 マス（0.3 m）。半径 0.10 なら通れる、0.21 なら通れない
left, right = (-0.45, 0.05), (0.55, 0.05)
check("道: 半径 0.10 m なら仕切りのすき間を通れる", gm.path_length(left, right, 0.10) is not None, True)
check("道: 半径 0.21 m では通れない（すき間が狭い）", gm.path_length(left, right, 0.21), None)
check("道: 同じ部屋の中なら半径 0.21 m でも道あり", gm.path_length(left, (-0.45, 0.25), 0.21) is not None, True)
check("道: 出発点が壁に近すぎると道なし", gm.path_length((-0.85, 0.05), left, 0.21), None)

# だめなピンへの提案: 左の壁（中心 x=-0.95）から 0.15 m の点 → 同じ行で、壁から 0.30 m のマス（x=-0.65）
sx_, sy_, sd_ = MC.suggest_spot(gm, -0.80, 0.05, 0.21)
check("提案: いちばん近い OK の位置（壁から 0.30 m 以上）", (round(sx_, 2), round(sy_, 2), round(sd_, 2)), (-0.65, 0.05, 0.3))
check("提案: すでに OK の点なら、その点のマス", tuple(round(v, 2) for v in MC.suggest_spot(gm, -0.45, 0.05, 0.21)[:2]), (-0.45, 0.05))
check("提案: 広さが足りなければ None", MC.suggest_spot(gm, -0.45, 0.05, 0.21, target=0.9), None)
check("ずれをロボから見た向きに直す（向き 0）", tuple(round(v, 3) for v in MC.offset_in_robot_frame(0.15, 0.05, 0.0)), (0.15, 0.05))
check("ずれをロボから見た向きに直す（向き 90°: 地図の +y が前）",
      tuple(round(v, 3) for v in MC.offset_in_robot_frame(0.0, 0.15, math.pi / 2)), (0.15, 0.0))
check("動かし方の文", MC.describe_move(0.14, -0.06), "前へ 14 cm・右へ 6 cm")
check("動かし方の文（ほぼ同じ）", MC.describe_move(0.004, 0.0), "ほぼ同じ位置")
near_art = MC.render_ascii(gm, {}, field=gm.distance_field(), keep_out=0.26)
check("文字の地図: 壁のとなりは : （置けない帯）、部屋の真ん中は .",
      (near_art.splitlines()[6][2:4], near_art.splitlines()[6][10:12]), ("::", ".."))

art = MC.render_ascii(gm, {gm.world_to_cell(-0.45, 0.05): "0"})
check("文字の地図: 行数と、1 行目は壁（1 マスを横 2 文字で描く）", (len(art.splitlines()), art.splitlines()[0]), (MH, "#" * (2 * MW)))
check("文字の地図: ピンの数字が入る", "00" in art.splitlines()[MH - 1 - 5], True)


# =====================================================================
print("\n=== ラジコンの指令 → cmd_vel → 量子化 で元に戻るか（地図づくりで mecanum_node を通す）===")
# =====================================================================
for mot in (M.FORWARD, M.BACKWARD, M.LEFT, M.RIGHT, M.TURN_LEFT, M.TURN_RIGHT):
    for rpm_in in (15, 30, 60):
        q = M.Quantizer(use_vy=True)
        mo, rp = q(*M.twist_for_motion(mot, rpm_in), now=100.0)
        check(f"{M.MOTION_NAME[mot]} {rpm_in} rpm → cmd_vel → 量子化", (mo, round(rp, 3)), (mot, float(rpm_in)))
check("停止は (0, 0, 0)", M.twist_for_motion(M.STOP, 30), (0.0, 0.0, 0.0))
vx_, vy_, wz_ = M.twist_for_motion(M.TURN_LEFT, 15)
check("左回転 15 rpm は約 0.47 rad/s（1 秒に約 27°）", (vx_, vy_, round(wz_, 2)), (0.0, 0.0, 0.47))


# =====================================================================
print("\n=== GyroYaw（旋回はジャイロ、静止の判定とゼロ点合わせは車輪）===")
# =====================================================================
gy = M.GyroYaw(calib_samples=5)
check("ゼロ点が決まる前に動いたら、車輪の wz を使う", gy.update(0.50, 0.40, True, stamp=1), 0.40)
for k in range(5):                      # 静止中: ジャイロは 0.02 rad/s ずれている
    out = gy.update(0.02, 0.0, False, stamp=10 + k)
check("静止中は wz = 0", out, 0.0)
check("静止中の平均がゼロ点になる", (gy.ready, round(gy.bias, 6)), (True, 0.02))
check("動いているときは ジャイロ − ゼロ点", round(gy.update(0.49, 0.60, True, stamp=20), 6), 0.47)
b0 = gy.bias
for _ in range(3):                      # オドメトリは 50 Hz、テレメトリは 10 Hz: 同じサンプルが何度も来る
    gy.update(0.03, 0.0, False, stamp=21)
check("同じテレメトリ（stamp が同じ）は 1 回しか数えない", round(gy.bias, 9), round(b0 + 0.02 * (0.03 - b0), 9))
b1 = gy.bias
gy.update(0.50, 0.0, False, stamp=22)   # 車輪は止まっているのに大きく回っている（手で回された）
check("静止中でも大きな値はゼロ点の更新に使わない", gy.bias, b1)
gy2 = M.GyroYaw(calib_samples=3)
for k in range(3):
    gy2.update(0.03, 0.0, False, stamp=k)
for k in range(200):                    # 温度でゼロ点がゆっくり動く → 追いかける
    gy2.update(0.05, 0.0, False, stamp=100 + k)
check("ゼロ点はゆっくり追従する", round(gy2.bias, 3), 0.05)


# =====================================================================
print("\n=== Nav2 のその場回転の指令が、量子化の下限（MIN_RPM）を超えるか ===")
# =====================================================================
# 2026-09-21 実機: max_angular_accel 1.0 × 制御周期 0.05 s = 0.05 rad/s が最初の指令になり、
# 10 rpm 未満（0.31 rad/s 未満）を停止に丸める量子化に消されて、その場回転が永久に始まらなかった。
_diff = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nav2", "nav2_params_差分.yaml"),
             encoding="utf-8").read()


def _yaml_num(key):
    m_ = re.search(r"^\s*" + re.escape(key) + r":\s*([0-9.]+)", _diff, flags=re.M)
    return float(m_.group(1)) if m_ else None


deadband_w = P.rpm_to_mps(P.MIN_RPM) / P.WHEEL_GEOM_L          # これ未満の回る速さは「停止」になる [rad/s]
first_cmd = _yaml_num("max_angular_accel") / _yaml_num("controller_frequency")
check("量子化の下限は約 0.31 rad/s", round(deadband_w, 2), 0.31)
check("止まった状態からの最初の回転指令（max_angular_accel ÷ controller_frequency）が下限を超える",
      first_cmd > deadband_w, True)
# 位置はきっちり、向きはゆるく（2026-09-21 PM 決定: 向きは ±30° でよい）。
# Nav2 には位置だけを合わせさせ（向きは 3.14＝どの向きでも可）、向きはブリッジが spin で合わせる（1 回。収まらなければもう 1 回）。
#   実機のログ: RPP はゴールから 8 cm 以内のあいだだけゴールの向きへ回る。回るあいだに位置の推定が数 cm ずれて
#   8 cm の外へ出ると「位置へ戻る」に切り替わり、これをくり返して席2 で 46〜72 秒かかった（走行は 20 秒ほど）
check("Nav2 のゴールの許容: 位置 8 cm・向きは問わない（3.14）",
      (_yaml_num("xy_goal_tolerance"), _yaml_num("yaw_goal_tolerance")), (0.08, 3.14))
_tol = math.radians(30)
check("向き合わせ: 許容（30°）の中なら回さない", B.heading_fix(1.0, 1.0 + math.radians(29), _tol), None)
check("向き合わせ: 席2 の例（着いた向き 34°・ピン 104°）は +70° 回す",
      round(math.degrees(B.heading_fix(math.radians(104), math.radians(34), _tol))), 70)
check("向き合わせ: ±180° をまたぐときは近いほうへ（+170° と −170° は 20° 差＝回さない。100° → −175° は +85°）",
      (B.heading_fix(math.radians(170), math.radians(-170), _tol),
       round(math.degrees(B.heading_fix(math.radians(-175), math.radians(100), _tol)))), (None, 85))
check("向き合わせ: 本棚へ帰ったとき（ほぼ逆向き）は約 180° 回す",
      round(abs(math.degrees(B.heading_fix(0.03, 0.03 + math.radians(179), _tol)))), 179)
_bridge_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookshelf_bridge.py"), encoding="utf-8").read()
check("向き合わせは「到着しました」の前に 1 か所で呼ぶ・失敗しても到着のまま",
      (_bridge_src.count("self.align_heading(goal, t0)"),
       _bridge_src.index("self.align_heading(goal, t0)") < _bridge_src.index('print("    [nav] 到着しました")')), (1, True))
check("--heading-tolerance-deg の既定は 30", 'add_argument("--heading-tolerance-deg", type=float, default=30.0' in _bridge_src, True)
check("回頭の速さ rotate_to_heading_angular_vel が下限を超える",
      _yaml_num("rotate_to_heading_angular_vel") > deadband_w, True)
mo_, rp_ = M.Quantizer(use_vy=False)(0.0, 0.0, first_cmd, now=500.0)
check("その最初の指令は、量子化で「左回転」になる（停止に丸められない）", mo_, M.TURN_LEFT)
check("最終進入の速さ min_approach_linear_velocity も下限（0.042 m/s）を超える",
      _yaml_num("min_approach_linear_velocity") > P.rpm_to_mps(P.MIN_RPM), True)


# =====================================================================
print("\n=== 横移動をさせない（動きは 前進・後退・その場回転 だけ）===")
# =====================================================================
# 2026-09-21 PM 決定: 前（本棚側）が重く、横移動すると一緒に回ってしまう。
# ① 経路追従が RPP（vx と ω しか出さない） ② velocity_smoother の vy の上限が 0 ③ mecanum_node の use_vy が既定 false
check("① 経路追従の本体は RPP（vy を作らない）",
      re.search(r"^\s*primary_controller:\s*\"([^\"]+)\"", _diff, flags=re.M).group(1),
      "nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController")
_vs = _diff[_diff.index("velocity_smoother:"):]
_vy = {k: [float(v) for v in re.search(r"^\s*" + k + r":\s*\[([^\]]+)\]", _vs, flags=re.M).group(1).split(",")][1]
       for k in ("max_velocity", "min_velocity", "max_accel", "max_decel")}
check("② velocity_smoother の vy は 4 つとも 0.0", _vy,
      {"max_velocity": 0.0, "min_velocity": 0.0, "max_accel": 0.0, "max_decel": 0.0})
check("経路追従では後退しない（allow_reversing: false。後退は立て直しの backup だけ）",
      re.search(r"^\s*allow_reversing:\s*(\w+)", _diff, flags=re.M).group(1), "false")
_node_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mecanum_node.py"), encoding="utf-8").read()
check("③ mecanum_node.py の use_vy は既定 false", 'declare_parameter("use_vy", False)' in _node_src, True)
check("③ Quantizer の use_vy も既定 false", M.Quantizer().use_vy, False)
_seen = set()
for _vx in (-0.25, -0.1, 0.0, 0.1, 0.25):
    for _vyv in (-0.25, -0.1, 0.0, 0.1, 0.25):
        for _wz in (-1.0, -0.4, 0.0, 0.4, 1.0):
            _seen.add(M.Quantizer()(_vx, _vyv, _wz, now=600.0)[0])
check("封印中は、どんな指令（vx・vy・ω の 125 通り）でも 左横・右横 にならない",
      sorted(_seen), sorted({M.STOP, M.FORWARD, M.BACKWARD, M.TURN_LEFT, M.TURN_RIGHT}))
check("封印中に横移動だけの指令が来たら停止", M.Quantizer()(0.0, 0.25, 0.0, now=700.0), (M.STOP, 0.0))


# =====================================================================
print("\n=== 古い Nav2 の設定に気づく（ブリッジの起動時の点検・make_nav2_params --check）===")
# =====================================================================
# 古い ~/nav2/nav2_params.yaml のまま走らせると「向きを変える場面で動けない」。ログだけでは気づきにくい。
_new = {"controller_frequency": 20.0, "FollowPath.max_angular_accel": _yaml_num("max_angular_accel"),
        "FollowPath.rotate_to_heading_angular_vel": _yaml_num("rotate_to_heading_angular_vel"),
        "FollowPath.min_approach_linear_velocity": _yaml_num("min_approach_linear_velocity"),
        "progress_checker.movement_time_allowance": _yaml_num("movement_time_allowance"),
        "general_goal_checker.yaw_goal_tolerance": _yaml_num("yaw_goal_tolerance")}
check("いまの差分ファイルの値なら、問題なし", B.check_nav2_motion_params(_new), [])
_old = dict(_new, **{"FollowPath.max_angular_accel": 1.0, "progress_checker.movement_time_allowance": 10.0})
_probs = B.check_nav2_motion_params(_old)
check("古い値（max_angular_accel 1.0・見張り 10 秒）は 2 つとも指摘する", len(_probs), 2)
check("指摘に「最初の指令 0.05 rad/s」と「下限 0.31 rad/s」が入る",
      ("0.05 rad/s" in _probs[0], "0.31 rad/s" in _probs[0]), (True, True))
for _y in (0.25, 0.52):
    check(f"Nav2 が向きまで合わせる設定（yaw_goal_tolerance {_y}＝古い設定）は指摘する",
          ["yaw_goal_tolerance" in t for t in B.check_nav2_motion_params(dict(_new, **{"general_goal_checker.yaw_goal_tolerance": _y}))], [True])
check("値が読めなかった項目（None）は指摘しない", B.check_nav2_motion_params({"controller_frequency": 20.0}), [])
check("Nav2 の標準値のまま（3.2 rad/s²）でも指摘する",
      len(B.check_nav2_motion_params({"controller_frequency": 20.0, "FollowPath.max_angular_accel": 3.2})), 1)

_base = {"controller_server": {"ros__parameters": {"FollowPath": {"max_angular_accel": 3.2}, "x": 1}}, "amcl": {"a": 1}}
_want, _ = NP.deep_merge(_base, {"controller_server": {"ros__parameters": {"FollowPath": {"max_angular_accel": 8.0}}}})
check("--check: 同じなら「最新」", NP.stale_items(_want, _want), [])
_st = NP.stale_items(_base, _want)
check("--check: 古いファイルは、ちがう所を 1 か所だけ挙げる",
      [(NP.fmt_path(p), o, n) for p, o, n in _st],
      [(NP.fmt_path(("controller_server", "ros__parameters", "FollowPath", "max_angular_accel")), 3.2, 8.0)])
check("--check: 読めないファイル（None）も「古い」あつかい", len(NP.stale_items(None, _want)) > 0, True)


# =====================================================================
print("\n=== manual_control --ros の指令途切れ（apply が 0.5 秒来なければ 0 を出す）===")
# =====================================================================
# 2026-09-24 文書の見直しで発覚: Pi 自身の Wi-Fi が切れると manual_poll() の問い合わせが最長 5 秒待たされ、
# そのあいだ RosDriver は最後の指令（前進など）を 20 Hz で出し続けていた。--serial の MecanumLink は
# cmd_timeout=0.5 で止まるのに、--ros だけ守りが弱かった。ROS 2 が無い PC でも確かめられるよう、
# rclpy を使う __init__ は通さず、publish を記録するだけの偽物を差し込む。
check("RosDriver.CMD_TIMEOUT_SEC は MecanumLink に渡している cmd_timeout と同じ 0.5 秒",
      (MCTL.RosDriver.CMD_TIMEOUT_SEC, "cmd_timeout=0.5" in inspect.getsource(MCTL.SerialDriver.__init__)), (0.5, True))
check("_loop は毎周期 _tick を呼ぶ（ここで検算しているのと同じ道を通る）",
      "self._tick(time.time())" in inspect.getsource(MCTL.RosDriver._loop), True)

_rd = MCTL.RosDriver.__new__(MCTL.RosDriver)      # ROS を import する __init__ は通さない
_rd._lock, _rd._cmd, _rd._last_apply, _rd._last_moving, _rd._stale_logged = threading.Lock(), (0, 0), 0.0, 0.0, False
_pubs = []
_rd._publish = lambda m, r: _pubs.append((m, r))

_rd.apply(M.FORWARD, 30, now=100.0)
_rd._tick(100.05); _rd._tick(100.45)
check("apply から 0.5 秒以内: 最後の指令（前進 30 rpm）を出し続ける", _pubs, [(M.FORWARD, 30), (M.FORWARD, 30)])
_pubs.clear()
with contextlib.redirect_stdout(io.StringIO()) as _log:
    _rd._tick(100.51)
check("apply が 0.5 秒途切れたら、前進ではなく停止 (0, 0) を出す", _pubs, [(0, 0)])
check("途切れたことをログに出す", "0.5 秒" in _log.getvalue(), True)
with contextlib.redirect_stdout(io.StringIO()) as _log2:
    _rd._tick(100.6)
check("その後も 0 を出し続ける（ZERO_TAIL_SEC のあいだ）・ログは一度だけ", (_pubs, _log2.getvalue()), ([(0, 0), (0, 0)], ""))
_rd._tick(101.6)                                   # 最後に動いた 100.45 から 1 秒過ぎた
check("停止から 1 秒過ぎたら何も出さない（Nav2 の指令を打ち消さない。従来どおり）", _pubs, [(0, 0), (0, 0)])

_pubs.clear()
_rd.apply(M.BACKWARD, 20, now=102.0)
_rd._tick(102.01)
check("指令がまた来れば、そのまま動く", _pubs, [(M.BACKWARD, 20)])
_pubs.clear()
_rd.apply(M.STOP, 0, now=102.1)                    # 明示的な停止（stop() と同じ）
_rd._tick(102.2); _rd._tick(102.9); _rd._tick(103.2)
check("明示的な停止は 1 秒だけ 0 を出して、その後は出さない（従来どおり）", _pubs, [(0, 0), (0, 0)])
_pubs.clear()
_rd.apply(M.FORWARD, 30, now=104.0)
_rd._tick(104.1)
with contextlib.redirect_stdout(io.StringIO()) as _log3:
    _rd._tick(104.7)
check("2 回目の途切れも、また止めてログを出す", (_pubs, "0.5 秒" in _log3.getvalue()), ([(M.FORWARD, 30), (0, 0)], True))


# =====================================================================
print("\n=== uwb_reader（UWB 基板の出力の読み取り。S1）===")
# =====================================================================
#   基板は 1 秒 5 回、1 行 1 JSON を流す。文字 → 数値（距離 m・方向 deg）に直し、
#   測れなかった回・命令の返事・途中で切れた行を捨てる。命令は電波法のガードを通してからしか送れない。

# （数値は説明用の作り物。実測値や資料の例の値は、このリポジトリに入れない）
SAMPLE = ('{"Block":0, "results":[{"Addr":"0x0001","Status":"Ok","D_cm":152,"LPDoA_deg":47.5,'
          '"LAoA_deg":19.8,"LFoM":200,"RAoA_deg":-15.8,"CFO_100ppm":120}]}')
rs = U.parse_line(SAMPLE, angle_sign=+1, t=10.0)
check("測定の行は 1 件（相手 1 台）", len(rs), 1)
r = rs[0]
check("Block / Addr / Ok", (r.block, r.addr, r.ok), (0, "0x0001", True))
check("D_cm 152 → 1.52 m", r.distance_m, 1.52)
check("LAoA_deg 19.8 → bearing（angle_sign=+1 なら同じ値）", (r.laoa_deg, r.bearing_deg), (19.8, 19.8))
_q = U.parse_line(SAMPLE, angle_sign=-1)[0]
check("angle_sign=−1 なら bearing の符号が反転（LAoA はそのまま残す）", (_q.laoa_deg, _q.bearing_deg), (19.8, -19.8))
check("PDoA / FoM / RAoA", (r.pdoa_deg, r.fom, r.raoa_deg), (47.5, 200.0, -15.8))
check("diag なしなら RSSI / NLOS は None", (r.rssi_dbm, r.nlos_pct), (None, None))
check("bearing_rad", round(r.bearing_rad, 4), round(math.radians(19.8), 4))
check("読んだ時刻 t", r.t, 10.0)

print("  --- 書式のゆれ・測れなかった回・ほかの行 ---")
check("Status の前後に空白があっても Ok",
      U.parse_line('{"Block":3,"results":[{"Addr":"0x0001","Status":" Ok ","D_cm":100,"LAoA_deg":5}]}')[0].ok, True)
_diag = ('{"Block":7,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":212,"LPDoA_deg":1.2,"LAoA_deg":0.4,'
         '"LFoM":180,"RAoA_deg":-1.0,"CFO_100ppm":100,"RSSI_dBm":"-61.3","NLOS_%":12}]}')
r7 = U.parse_line(_diag)[0]
check("diag 1 の RSSI（文字列）と NLOS を数値に", (r7.rssi_dbm, r7.nlos_pct), (-61.3, 12.0))
bad = U.parse_line('{"Block":8,"results":[{"Addr":"0x0001","Status":"Err","D_cm":0,"LAoA_deg":0,"LFoM":0}]}')[0]
check("Status が Ok 以外: ok=False・距離と方向は None（0 を値として使わない）",
      (bad.ok, bad.distance_m, bad.bearing_deg), (False, None, None))
two = U.parse_line('{"Block":9,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":100,"LAoA_deg":10},'
                   '{"Addr":"0x0002","Status":"Ok","D_cm":250,"LAoA_deg":-40}]}')
check("相手が 2 台（1 対多）なら 2 件", [(q.addr, q.distance_m, q.bearing_deg) for q in two],
      [("0x0001", 1.0, 10.0), ("0x0002", 2.5, -40.0)])
check("命令の返事 JS00BE{…} は測定ではない",
      (U.line_kind('JS00BE{"UWB PARAM":{"CHAN":9}}'), U.parse_line('JS00BE{"UWB PARAM":{"CHAN":9}}')), ("reply", []))
check("ok / error / 空行 / その他の行の種類",
      [U.line_kind(x) for x in ("ok", "ERROR", "", "   ", "CURRENT ANTENNA_TYPE:JS1055-9")],
      ["ack", "ack", "empty", "empty", "other"])
check("失敗の返事は理由が付いても「返事の終わり」", [U.line_kind(x) for x in ("error ", "error  busy")], ["ack", "ack"])
check("途中で切れた行は捨てる（例外にしない）", U.parse_line('{"Block":10,"results":[{"Addr":"0x0001","Sta'), [])
# 測定中に打った命令の文字が、測定の行の頭に混ざる（実物で確認した形。数値は作り物）
_echo = 's{"Block":12, "results":[{"Addr":"0x0001","Status":"Ok","D_cm":123,"LPDoA_deg":1.5,"LAoA_deg":0.5,"LFoM":200,"RAoA_deg":0.0,"CFO_100ppm":0}]}'
check("頭に打った文字が混ざった測定の行も読む（種類）", U.line_kind(_echo), "measure")
_e = U.parse_line(_echo, angle_sign=+1, t=0.0)
check("頭に打った文字が混ざった測定の行も読む（中身）",
      (len(_e), _e[0].block, _e[0].ok, _e[0].distance_m, _e[0].laoa_deg) if _e else None, (1, 12, True, 1.23, 0.5))
check("ok と測定の行がくっついても測定として読む", U.line_kind('ok{"Block":13, "results":[]}'), "measure")
check("results の無い JSON も捨てる", U.parse_line('{"Block":11}'), [])

print("  --- 使える回か（しきい値は実測で決めるまで None＝見ない）---")
check("Ok で距離 > 0 なら使える", U.is_usable(r), True)
check("測れなかった回は使えない", U.is_usable(bad), False)
check("FoM のしきい値", (U.is_usable(r, min_fom=150), U.is_usable(r, min_fom=250)), (True, False))
check("NLOS のしきい値（値が無い回は見ない）",
      (U.is_usable(r7, max_nlos=10), U.is_usable(r7, max_nlos=20), U.is_usable(r, max_nlos=10)), (False, True, True))
check("遠すぎる回", U.is_usable(r, max_distance_m=1.0), False)
check("CSV の 1 行は見出しと同じ数の欄", len(U.csv_row(r)), len(U.CSV_FIELDS))
check("CSV: 測れなかった回は数値の欄が空", U.csv_row(bad)[4:6], ["", ""])

print("  --- 電波法のガード（許可した命令しか通さない。チャネル 9 固定・txpower／連続送信の試験は禁止）---")
check("測り始める命令の列（既定）", U.start_commands(), ["stop", U.UWBCFG_CH9, "antenna js1055-9", "initf"])
check("応える側・diag あり", U.start_commands("respf", diag=True),
      ["stop", U.UWBCFG_CH9, "antenna js1055-9", "diag 1", "respf"])
check("save 済みなら設定を打たない", U.start_commands(configure=False), ["stop", "initf"])
check("uwbcfg の先頭はチャネル 9", (U.CHANNEL, U.UWBCFG_CH9.split()[1]), (9, "9"))
for _good in ("stop", "initf", "respf", "diag 1", "diag 0",
              "uwbcfg", "antenna", "antenna js1055-9", U.UWBCFG_CH9, "  STOP  "):
    try:
        U.check_command(_good)
    except U.IllegalCommand as e:
        check(f"通るべき命令が止められた: {_good!r}", str(e), "")
check("空白と改行を整える", U.check_command("  uwbcfg   9 64 8 9 9 3 6810 0 0 65 0 64 1 \r\n"), U.UWBCFG_CH9)
check("大文字で渡しても、送るのは小文字に整えた形", (U.check_command("  STOP "), U.check_command("Antenna JS1055-9")),
      ("stop", "antenna js1055-9"))
check("★許可リストの中身（ここに無い命令は基板へ行かない）★", sorted(U.ALLOWED_COMMANDS),
      sorted(["stop", "initf", "respf", "diag 0", "diag 1",
              "uwbcfg", "antenna", "uwbcfg 9 64 8 9 9 3 6810 0 0 65 0 64 1", "antenna js1055-9"]))


def _blocked(cmd):
    try:
        U.check_command(cmd)
        return False
    except U.IllegalCommand:
        return True


check("txpower は止める", _blocked("txpower 10"), True)
check("チャネル 5 の uwbcfg は止める", _blocked("uwbcfg 5 64 8 9 9 3 6810 0 0 65 0 64 1"), True)
check("チャネル 9 以外は全部止める（例 10）", _blocked("UWBCFG 10 64 8 9 9 3 6810 0 0 65 0 64 1"), True)
check("js1055-5（チャネル 5 用アンテナ）は止める", _blocked("antenna js1055-5"), True)
check("ほかのアンテナ型も止める", _blocked("antenna external-1"), True)
check("試験ファームの AT 命令は止める", [_blocked(x) for x in ("AT+UCW", "AT+UTX", "AT+UCHAN=5", "at")], [True] * 4)
check("空の命令は止める", _blocked("   "), True)
check("1 行に 2 つ詰めた txpower も止める", _blocked("stop\r\ntxpower 5"), True)
check("引数なしの txpower（表示）も送らない", _blocked("txpower"), True)
check("試験用の連続送信（tcwm／tcfm）は止める", [_blocked(x) for x in ("tcwm", "TCFM", "tcfm 1 2 3", "stop\rtcwm")], [True] * 4)
check("チャネル 9 でも、既定と違う値の uwbcfg は止める",
      [_blocked(x) for x in ("uwbcfg 9 1024 8 9 9 3 6810 0 0 65 0 64 1", "uwbcfg 9 64 8 9 9 3 850 0 0 65 0 64 1",
                             "uwbcfg 9", "uwbcfg 9 64 8 9 9 3 6810 0 0 65 0 64 1 0")], [True] * 4)
check("initf／respf に引数は付けられない", [_blocked(x) for x in ("initf 1 2400 200", "respf 0")], [True] * 2)
check("アンテナ型を 2 つ並べた形も止める", _blocked("antenna js1055-9 js1055-5"), True)
check("一覧に無い命令は、害が無さそうでも送らない",
      [_blocked(x) for x in ("listener", "listener2", "uart 1", "stskeyiv", "decaid", "thread", "deca$", "diag 2",
                             "help txpower", "antenna values", "hello", "save", "restore", "stat", "version", "help",
                             "?", "dcfg")], [True] * 18)
check("打ち消しの文字や区切りを混ぜても通らない（そのままの形でしか比べない）",
      [_blocked(x) for x in ("x\btxpower 5", "\x00txpower 5", "stop;txpower 5", "x\buwbcfg 5 64 8 9 9 3 6810 0 0 65 0 64 1",
                             "atz", "stop\ttcwm")], [True] * 6)
check("JSON の形の命令・全角の文字・文字でないものも止める",
      [_blocked(x) for x in ('{"cmd":"txpower"}', "ｓｔｏｐ", None, 9, b"stop")], [True] * 5)
_why = []
for _c in ("tcwm", "uwbcfg 5 64 8 9 9 3 6810 0 0 65 0 64 1", "initf 1", "listener"):
    try:
        U.check_command(_c)
    except U.IllegalCommand as e:
        _why.append(str(e)[:12])
check("止めた理由を付ける（連続送信／チャネル／引数／一覧に無い）", len(set(_why)), 4)

print("  --- 設定の返事を読む（チャネル・アンテナ型・uwbcfg の 13 個）---")
_rep = ["uwbcfg", 'JS00BE{"UWB PARAM":{', '"CHAN":9,', '"P02":64,', '"P03":8,', '"P04":9,', '"P05":9,', '"P06":3,',
        '"P07":6810,', '"P08":0,', '"P09":0,', '"P10":65,', '"P11":0,', '"P12":64,', '"P13":1}}', "ok",
        "antenna", "", "CURRENT ANTENNA_TYPE:JS1055-9", "ok"]
check("返事からチャネルとアンテナ型を読む", U.parse_config_reply(_rep), (9, "js1055-9"))
check("uwbcfg の 13 個を並び順で読む（既定と同じ）", U.uwbcfg_numbers(_rep) == U.UWBCFG_NUMBERS, True)
check("既定の 13 個は命令の文字から作る", U.UWBCFG_NUMBERS, [9, 64, 8, 9, 9, 3, 6810, 0, 0, 65, 0, 64, 1])
check("返事が無ければ None", (U.parse_config_reply(["stop", "ok"]), U.uwbcfg_numbers(["stop", "ok"])), ((None, None), None))
check("何度も出てくれば最後（新しい返事）の値",
      U.parse_config_reply(['"CHAN":5,', "CURRENT ANTENNA_TYPE:JS1055-5", '"CHAN":9,', "CURRENT ANTENNA_TYPE:JS1055-9"]),
      (9, "js1055-9"))
check("打った命令の文字（エコー）や「使えない型」の知らせは、設定として読まない",
      U.parse_config_reply(["uwbcfg 9 64 8 9 9 3 6810 0 0 65 0 64 1", "antenna js1055-9", "INVALID ANTENNA_TYPE: XX"]),
      (None, None))
check("1 行にまとまった返事でも読める", U.uwbcfg_numbers(['JS0030{"UWB PARAM":{"CHAN":9,"A":64,"B":1}}']), [9, 64, 1])

print("  --- UwbReader × FakeUwbBoard（基板のにせもの。命令は CR 終端・返事から設定を確かめる・1 秒 5 回）---")
_clk = {"t": 100.0}
board = U.FakeUwbBoard(distance_m=1.5, bearing_deg=20.0, clock=lambda: _clk["t"])
rd = U.UwbReader(board, angle_sign=+1)
check("initf の前は何も流れない", board.readline(), b"")
_sent = rd.start(sleep=lambda s: None)
check("送った命令の列", _sent, ["stop", U.UWBCFG_CH9, "antenna js1055-9", "initf"])
check("基板が受けた命令（CR で区切られて 4 つ）", board.received, _sent)
check("返事（エコー・JS…・ok）は測定に数えない。測定はまだ 0 回", (rd.n_lines > 0, rd.n_ok, rd.last), (True, 0, None))
check("★測り始める前に、基板の返事からチャネル 9・js1055-9 を確かめてある★", rd.config, (9, "js1055-9"))
check("200 ms 経つ前は何も来ない", rd.poll(), [])
_clk["t"] += 0.2
rs = rd.poll()
check("200 ms 後に 1 回（1.5 m・左 20°）", (len(rs), rs[0].ok, rs[0].distance_m, rs[0].bearing_deg), (1, True, 1.5, 20.0))
check("基板の生の値 LAoA_deg（angle_sign=+1 なら bearing と同じ）", rs[0].laoa_deg, 20.0)
check("latest(): 新しいうちは返す・古くなると None",
      (rd.latest(0.5, now=rs[0].t + 0.3) is rs[0], rd.latest(0.5, now=rs[0].t + 0.6)), (True, None))
board.set_target(0.8, -45.0)
_clk["t"] += 0.2
rs = rd.poll()
check("相手を動かすと次の回から変わる（0.8 m・右 45°）", (rs[0].distance_m, rs[0].bearing_deg), (0.8, -45.0))
_clk["t"] += 1.0
_cnt = 0
while rd.poll():
    _cnt += 1
check("1 秒で 5 回（たまった行は続けて読める）", _cnt, 5)
board.set_target(1.0, 150.0)
_clk["t"] += 0.2
check("後ろ（150°）の相手は前に鏡写し（30°）で見える＝前後は UWB だけでは分からない", rd.poll()[0].bearing_deg, 30.0)
board.set_target(1.0, -120.0)
_clk["t"] += 0.2
check("右後ろ（−120°）→ −60°", rd.poll()[0].bearing_deg, -60.0)
rd.stop()
check("stop を送ると止まる", (rd.sent[-1], board.running), ("stop", False))
_tail = rd._settle()                 # 出かかっていた行を読み切る（本物も ok のあとにもう 1 行出てから止まる）
_clk["t"] += 1.0
check("止まったあとは、ok と止まった知らせが出たきり、もう流れない",
      ("ok" in _tail, len(_tail), rd.poll(), rd.poll()), (True, 2, [], []))
try:
    rd.send("txpower 3")
    check("txpower は send() で止まる", "送られてしまった", "IllegalCommand")
except U.IllegalCommand:
    check("txpower は send() で止まる（基板には届かない）", any("txpower" in c for c in board.received), False)

print("  --- 測り始めてすぐ返事を読み捨てる場面で、測定の行を捨てない ---")
_clk["t"] = 200.0
_b5 = U.FakeUwbBoard(1.0, 0.0, clock=lambda: _clk["t"])
_rd5 = U.UwbReader(_b5)


def _sleep_and_advance(sec):      # 本物の基板は、返事を待っている 0.3 秒のあいだにも測定の行を流し始める
    _clk["t"] += sec


_rd5.start(gap=0.3, sleep=_sleep_and_advance)
check("initf の返事のあとに届いた最初の測定は捨てずに数える", (_rd5.n_ok, _rd5.last is not None), (1, True))
_clk["t"] += 0.2
check("そのあとも普通に読める", len(_rd5.poll()), 1)

_b2 = U.FakeUwbBoard(1.0, 30.0, clock=lambda: _clk["t"], angle_sign=-1)
_rd2 = U.UwbReader(_b2, angle_sign=-1)
_rd2.start(sleep=lambda s: None)
_clk["t"] += 0.2
_q2 = _rd2.poll()[0]
check("基板が「右が＋」でも angle_sign=−1 で左が＋に揃う（LAoA −30 → bearing +30）", (_q2.laoa_deg, _q2.bearing_deg), (-30.0, 30.0))

_b3 = U.FakeUwbBoard(2.0, 0.0, drop_rate=1.0, clock=lambda: _clk["t"])
_rd3 = U.UwbReader(_b3)
_rd3.start(diag=True, sleep=lambda s: None)
_clk["t"] += 0.2
_q3 = _rd3.poll()[0]
check("測れなかった回は ok=False で数えられる", (_q3.ok, _rd3.n_ok, _rd3.n_fail), (False, 0, 1))
_b3.drop_rate = 0.0
_clk["t"] += 0.2
_q3 = _rd3.poll()[0]
check("diag 1 なら RSSI と NLOS が付く", (_q3.rssi_dbm is not None, _q3.nlos_pct), (True, 0.0))

_buf = io.StringIO()
_b4 = U.FakeUwbBoard(1.2, 10.0, clock=lambda: _clk["t"])
_rd4 = U.UwbReader(_b4, log=_buf)
_rd4.start(configure=False, sleep=lambda s: None)
_clk["t"] += 0.2
_rd4.poll()
_lines = _buf.getvalue().splitlines()
check("生のログ: 打った命令は「# > 」付き、基板の行（エコー・返事・測定）はそのまま",
      (_lines[0], _lines[1], _lines[2], "# > initf" in _lines, _lines[-1].startswith('{"Block"')),
      ("# > stop", "stop", "ok", True, True))
check("--no-config: 設定の命令は打たず、表示だけの命令（uwbcfg・antenna）で確かめてから initf",
      (_rd4.sent, _rd4.config), (["stop", "uwbcfg", "antenna", "initf"], (9, "js1055-9")))

print("  --- 設定を確かめられないときは電波を出さない（initf／respf を送らない）---")


def _start_fails(board, **kw):
    """start() が ConfigError になり、initf／respf が基板に届いていなければ True。"""
    rdr = U.UwbReader(board)
    try:
        rdr.start(sleep=lambda s: None, **kw)
    except U.ConfigError:
        return not any(c in ("initf", "respf") for c in board.received) and rdr.config is None
    return False


_bc = U.FakeUwbBoard(clock=lambda: _clk["t"])
_bc.cfg[0] = 5
_bc.lock_config = True               # チャネル 5 のまま、設定を受け付けない基板（ありえないはずの故障の真似）
check("基板がチャネル 5 のまま → ConfigError・initf は送らない", _start_fails(_bc), True)
_bc = U.FakeUwbBoard(clock=lambda: _clk["t"])
_bc.antenna_type = "JS1055-5"
_bc.lock_config = True
check("アンテナ型が js1055-9 でない → ConfigError・initf は送らない", _start_fails(_bc), True)
_bc = U.FakeUwbBoard(clock=lambda: _clk["t"])
_bc.cfg[6] = 850                     # 保存されていた値が既定と違う（--no-config では直されない）
check("--no-config で、保存されていた uwbcfg が既定と違う → ConfigError", _start_fails(_bc, configure=False), True)
_bc = U.FakeUwbBoard(clock=lambda: _clk["t"])
_bc.cfg[6] = 850
check("同じ基板でも、設定を打つ（既定）なら既定の値に直ってから測り始める",
      (U.UwbReader(_bc).start(sleep=lambda s: None)[-1], _bc.cfg == U.UWBCFG_NUMBERS), ("initf", True))


class _MuteBoard:                    # 返事をしない相手（別のファーム・別の機器）
    def __init__(self):
        self.received = []

    def write(self, data):
        self.received.append(data.decode().strip())
        return len(data)

    def readline(self):
        return b""

    def close(self):
        pass


check("返事が無い相手 → ConfigError・initf は送らない", _start_fails(_MuteBoard()), True)

print("  --- 測距中に start し直す（前の回が止まりきるのを待ってから設定する）---")
_clk["t"] = 300.0
_b6 = U.FakeUwbBoard(1.0, 0.0, clock=lambda: _clk["t"])
_rd6 = U.UwbReader(_b6)
_rd6.start(sleep=lambda s: None)
_clk["t"] += 1.0                     # 測距中（読まずに 1 秒 → 行がたまっている）
_n6 = len(_b6.received)
_rd6.start(sleep=lambda s: None)
check("止まってから設定の命令が通り、測り直しになる",
      (_b6.received[_n6:], _rd6.config, _b6.running, _rd6.last),
      (["stop", U.UWBCFG_CH9, "antenna js1055-9", "initf"], (9, "js1055-9"), True, None))
check("前の回の残りの測定は「測れた回数」に数えない", _rd6.n_ok, 0)

print("  --- 古い行（ロボが回っている間にたまった行）と、その捨て方 ---")
_clk["t"] = 400.0
_tgt = {"d": 1.0, "b": 10.0}
_b7 = U.FakeUwbBoard(clock=lambda: _clk["t"])
_b7.target_fn = lambda: (_tgt["d"], _tgt["b"])
_rd7 = U.UwbReader(_b7)
_rd7.start(sleep=lambda s: None)
_clk["t"] += 0.2
check("ふつうに読めば、いまの値（1.0 m・10°）", [(q.distance_m, q.bearing_deg) for q in _rd7.poll()], [(1.0, 10.0)])
_tgt.update(d=2.0, b=-30.0)          # ここでロボが回った（相手の見え方が変わった）。その間 2 秒読まなかった
_clk["t"] += 2.0
_old = _rd7.poll()
check("★読まずにたまった行は、回る前の値のまま出てくる（＝そのまま使うと位置がずれる）★",
      [(q.distance_m, q.bearing_deg) for q in _old], [(1.0, 10.0)])
_rd7.flush_input()
check("flush_input() でたまった行を捨てる", (_b7.n_flushed > 0, _rd7.poll()), (True, []))
_clk["t"] += 0.2
check("捨てたあとの最初の行は、いまの値（2.0 m・−30°）", [(q.distance_m, q.bearing_deg) for q in _rd7.poll()], [(2.0, -30.0)])


class _NoFlush(_MuteBoard):          # reset_input_buffer を持たない相手でも落ちない
    pass


check("捨てる手段が無い相手でも flush_input() は例外にしない", U.UwbReader(_NoFlush()).flush_input(), None)
check("ポート探し: 既定の固定名（/dev/uwb）が無く、USB にも無ければ None",
      None if os.path.exists(U.DEFAULT_PORT) else U.find_port(U.DEFAULT_PORT), None)
check("ポート探し: 名前を指定されたら、そのまま返す（COM6 のような名前は、ファイルの有無では確かめられない）",
      (U.find_port("COM6"), U.find_port("/dev/ttyACM1")), ("COM6", "/dev/ttyACM1"))
_P = type("P", (), {})
_two = []
for _dev in ("COM8", "COM6"):
    _p = _P()
    _p.vid, _p.pid, _p.device = U.UWB_VID, U.UWB_PID, _dev
    _two.append(_p)
_comports0 = _lp.comports
_lp.comports = lambda: _two                    # 基板を 2 枚つないだ PC（どちらも同じ USB の ID）
_buf2 = io.StringIO()
with contextlib.redirect_stdout(_buf2):
    _auto = U.find_port("auto")
check("基板が 2 枚あっても、指定した名前のほうを返す（USB の ID で探し直して別の基板を開かない）",
      (U.find_port("COM6"), _auto, "2 枚見つかりました" in _buf2.getvalue()), ("COM6", "COM8", True))
_lp.comports = _comports0


# =====================================================================
print("\n=== beacon_locator（UWB の距離と方向 → ユニットの地図座標。S2）===")
# =====================================================================
#   アンテナの地図上の位置 ＋ 水平距離 × 方向 → 点。候補は前後 2 つ。ロボが回ると本当の側に集まる。
M0 = BL.Mount(x=0.1, y=0.0, yaw=0.0, height=0.25)
_ap = BL.antenna_pose((1.0, 2.0, math.pi / 2), M0)
check("アンテナの位置: ロボ (1, 2) 向き 90°・取り付け x=0.1 → (1, 2.1) 向き 90°",
      (round(_ap[0], 6), round(_ap[1], 6), round(_ap[2], 6)), (1.0, 2.1, round(math.pi / 2, 6)))
check("後ろ向きの取り付け（yaw=π）は向きが反転",
      round(abs(BL.antenna_pose((0.0, 0.0, 0.0), BL.Mount(-0.1, 0.0, math.pi, 0.25))[2]), 6), round(math.pi, 6))
check("斜め距離 1.0・高さの差 0.6 → 水平 0.8", round(BL.horizontal_distance(1.0, 0.6), 6), 0.8)
check("高さの差より短ければ 0（真上）", BL.horizontal_distance(0.4, 0.6), 0.0)
check("鏡写し: 30 → 150、−30 → −150、0 → 180", [BL.mirror_bearing(b) for b in (30.0, -30.0, 0.0)], [150.0, -150.0, 180.0])
_pt = BL.point_from((0.1, 0.0, 0.0), 1.0, 0.0)
check("前 1 m → (1.1, 0)", (round(_pt[0], 6), round(_pt[1], 6)), (1.1, 0.0))
_pt = BL.point_from((0.1, 0.0, 0.0), 1.0, 90.0)
check("左 90° 1 m → (0.1, 1)", (round(_pt[0], 6), round(_pt[1], 6)), (0.1, 1.0))
_mx, _my, _sp = BL.median_point([(1.0, 1.0), (1.1, 0.9), (0.9, 1.1), (1.0, 1.0), (5.0, 5.0)])
check("中央値は外れ値 (5,5) に引きずられない（平均なら (1.8, 1.8) になる）", (round(_mx, 2), round(_my, 2), _sp < 0.5), (1.0, 1.0, True))
_tri = BL.trilaterate([(0.0, 0.0), (2.0, 0.0), (0.0, 2.0)], [math.sqrt(2.0), math.sqrt(2.0), math.sqrt(2.0)])
check("三点測位: 3 点から等距離 √2 → (1, 1)", (round(_tri[0], 6), round(_tri[1], 6)), (1.0, 1.0))
_tri = BL.trilaterate([(0.1, 0.0), (1.1, 0.0), (0.1, 1.0)], [math.hypot(0.4, 3.0), math.hypot(0.6, 3.0), math.hypot(0.4, 2.0)])
check("三点測位: 一般の位置 (0.5, 3.0)", (round(_tri[0], 6), round(_tri[1], 6)), (0.5, 3.0))
check("一直線上の 3 点では決まらない", BL.trilaterate([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)], [1.0, 1.0, 1.0]), None)
check("2 点では決まらない", BL.trilaterate([(0.0, 0.0), (1.0, 0.0)], [1.0, 1.0]), None)

print("  --- simulate_reading（逆計算: ユニットの位置 → 基板が出すはずの値）---")
_d, _b = BL.simulate_reading((0.0, 0.0, 0.0), (2.1, 0.0), M0, beacon_height=0.25)
check("正面 2 m（高さ同じ）→ d=2.0・b=0", (round(_d, 6), round(_b, 6)), (2.0, 0.0))
_d, _b = BL.simulate_reading((0.0, 0.0, 0.0), (0.1, 1.0), M0, beacon_height=0.85)
check("左 90°・高さの差 0.6 → 斜め距離 √1.36", (round(_d, 4), round(_b, 4)), (round(math.sqrt(1.36), 4), 90.0))
_d, _b = BL.simulate_reading((0.0, 0.0, 0.0), (-1.9, 0.0), M0, beacon_height=0.25)
check("真後ろは鏡写しで前（b=180 → 0）に見える", (round(_d, 6), round(abs(_b), 6)), (2.0, 0.0))


def _reading(d, b, ok=True):
    return U.UwbReading(block=1, addr="0x0001", ok=ok, distance_m=d, bearing_deg=b, laoa_deg=b,
                        pdoa_deg=None, fom=200.0, raoa_deg=None, rssi_dbm=None, nlos_pct=None, t=0.0)


def _feed(est, pose, beacon, n=5, h=0.75):
    for _ in range(n):
        d, b = BL.simulate_reading(pose, beacon, M0, h)
        est.add(pose, _reading(d, b))


print("  --- 何回かまとめる・前後の見分け・距離だけで決める ---")
est = BL.BeaconEstimator(mount=M0, beacon_height=0.75)
check("測定が無ければ None", est.estimate(), None)
check("測れなかった回は使わない", est.add((0.0, 0.0, 0.0), _reading(1.0, 0.0, ok=False)), False)
_feed(est, (0.0, 0.0, 0.0), (2.0, 1.0))
e = est.estimate()
check("止まったまま 5 回: 前の候補を仮に返す・前後は未確定", (round(e.x, 2), round(e.y, 2), e.ambiguous, e.n), (2.0, 1.0, True, 5))
_feed(est, (0.0, 0.0, math.radians(30)), (2.0, 1.0))
e = est.estimate()
check("30° 回って 5 回: 前後が決まる（前）・10 回が支持", (round(e.x, 2), round(e.y, 2), e.ambiguous, e.n, e.method),
      (2.0, 1.0, False, 10, "bearing"))
check("最新の方向はアンテナから見た値（30° 回ったぶん小さい）",
      round(e.bearing_deg, 3), round(BL.simulate_reading((0.0, 0.0, math.radians(30)), (2.0, 1.0), M0, 0.75)[1], 3))
check("ばらつきは 0（偽の値は正確）", round(e.spread_m, 6), 0.0)
est2 = BL.BeaconEstimator(mount=M0, beacon_height=0.75)
_feed(est2, (0.0, 0.0, 0.0), (-2.0, 0.5))
_feed(est2, (0.0, 0.0, math.radians(30)), (-2.0, 0.5))
e2 = est2.estimate()
check("後ろの相手（基板は鏡写しの角しか出せない）も、回れば正しい側に決まる",
      (round(e2.x, 2), round(e2.y, 2), e2.ambiguous), (-2.0, 0.5, False))
check("方向は鏡写しを戻した値（後ろ＝|b| > 90）", abs(e2.bearing_deg) > 90.0, True)
est3 = BL.BeaconEstimator(mount=M0, beacon_height=0.75)
for _pose in ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
    _feed(est3, _pose, (0.5, 3.0), n=1)
e3 = est3.estimate()
check("真横に近い相手（±60° の外）: 方向は使わず、3 か所の距離だけで決まる",
      (e3.method, round(e3.x, 2), round(e3.y, 2), e3.ambiguous), ("range", 0.5, 3.0, False))
est4 = BL.BeaconEstimator(mount=M0, beacon_height=0.75, window=6)
_feed(est4, (0.0, 0.0, 0.0), (2.0, 0.0), n=10)
check("窓（最近 N 回）より古い測定は捨てる", (est4.n_used, est4.estimate().n), (10, 6))
check("回って決めた推定は resolved_by='turn'", e.resolved_by, "turn")

print("  --- 前回の場所（prior）で前後を決める・取り付けの文字・アンテナの正面の範囲 ---")
_ep = BL.BeaconEstimator(mount=M0, beacon_height=0.75, prior=(-2.0, 0.5))
_feed(_ep, (0.0, 0.0, 0.0), (-2.0, 0.5))
_e = _ep.estimate()
check("止まったままでも、前回の場所に近い山（後ろ）に決まる",
      (round(_e.x, 2), round(_e.y, 2), _e.ambiguous, _e.resolved_by), (-2.0, 0.5, False, "prior"))
_ep = BL.BeaconEstimator(mount=M0, beacon_height=0.75, prior=(2.1, 0.6))
_feed(_ep, (0.0, 0.0, 0.0), (2.0, 0.5))
_e = _ep.estimate()
check("前回の場所が前の山に近ければ、前に決まる", (round(_e.x, 2), round(_e.y, 2), _e.ambiguous, _e.resolved_by),
      (2.0, 0.5, False, "prior"))
_ep = BL.BeaconEstimator(mount=M0, beacon_height=0.75, prior=(5.0, 5.0))
_feed(_ep, (0.0, 0.0, 0.0), (2.0, 0.5))
check("前回の場所がどちらの山からも遠い（ユニットが動いた）→ 未確定のまま", _ep.estimate().ambiguous, True)
_ep = BL.BeaconEstimator(mount=M0, beacon_height=0.75, prior=(-2.0, 0.5))
_feed(_ep, (0.0, 0.0, 0.0), (-2.0, 0.5), n=2)
check("測定が 3 回に満たなければ、前回の場所があっても決めない", _ep.estimate().ambiguous, True)
_m = BL.parse_mount("-0.15, 0, 180, 0.25")
check("取り付けの文字 → Mount（向きは度で書く）", (_m.x, _m.y, round(_m.yaw, 6), _m.height), (-0.15, 0.0, round(math.pi, 6), 0.25))
for _bad in ("1,2,3", "a,b,c,d", "0,0,0,2.0"):
    try:
        BL.parse_mount(_bad)
        _r = "通った"
    except ValueError:
        _r = "ValueError"
    check(f"取り付けの文字が正しくない（{_bad}）→ ValueError", _r, "ValueError")
MR = BL.Mount(x=-0.15, y=0.0, yaw=math.pi, height=0.25)      # 後ろ向き（本棚の後ろの台座に立てる）
check("後ろ向き: 機体の前 2 m の点はアンテナの正面の範囲の外（機体の陰）", BL.in_antenna_cone((2.0, 0.0), (0.0, 0.0, 0.0), MR), False)
check("後ろ向き: 機体の後ろ 2 m の点は正面（0°）", (BL.in_antenna_cone((-2.0, 0.0), (0.0, 0.0, 0.0), MR),
                                              round(BL.bearing_from_antenna((-2.0, 0.0), (0.0, 0.0, 0.0), MR), 6)), (True, 0.0))
check("前向き: 機体の前 2 m・左 1 m の点は正面の範囲の内", BL.in_antenna_cone((2.0, 1.0), (0.0, 0.0, 0.0), M0), True)

print("  --- 手前のゴール ---")
_e = BL.BeaconEstimate(2.0, 0.0, 5, 0.0, "bearing", False, 2.0, 0.0)
g = BL.standoff_goal(_e, (0.0, 0.0, 0.0), 0.5)
check("手前 0.5 m のゴール: (1.5, 0) 向き 0°", (round(g["x"], 6), round(g["y"], 6), round(g["theta"], 6)), (1.5, 0.0, 0.0))
g = BL.standoff_goal(_e, (1.8, 0.0, 1.0), 0.5)
check("もう手前より近ければ、その場で向きだけユニットへ", (round(g["x"], 6), round(g["y"], 6), round(g["theta"], 6)), (1.8, 0.0, 0.0))
check("席のゴールと同じ形（frame_id・qz・qw・label）＋ beacon・standoff",
      (g["frame_id"], round(g["qz"], 6), round(g["qw"], 6), g["label"], g["beacon"], g["standoff"]),
      ("map", 0.0, 1.0, "ビーコン（UWB）", (2.0, 0.0), 0.5))


# =====================================================================
print("\n=== Bridge: ビーコン（UWB）への走行（S3。偽の基板がロボの位置に合わせた値を出す）===")
# =====================================================================
#   探す → 前後を決める（30°）→ 相手のほうを向く → 0.5 m 手前へ → 受取 → 帰還。時間は手で進める。
_clk = {"t": 1000.0}


def _mk_world(beacon_xy, start=(0.0, 0.0, 0.0), mount=None, shadow=False, hint=None, nav=None, turn_sec=2.0,
              **board_kw):
    """偽の Supabase・偽の Nav・偽の UWB 基板（ロボの位置からユニットまでを逆計算して流す）を組んだブリッジ。
    mount: アンテナの取り付け（既定 M0＝前向き）。shadow=True なら、ユニットがアンテナの裏側（機体の陰）にある回は
    35 % 測れず、距離 +45 cm・方向 +20° で出る（いつも同じずれ＝厳しめ）。hint: ビーコンの行の x, y（前回の推定）。

    ★時間に忠実★ その場回転には turn_sec 秒かかり（その間、基板は読まれずに行をためる。たまった行は回る前の値）、
    基板は次の行が来るまで待つ（読むたびに時計が進む）。本物と同じく、回ったあとに古い行を捨てないと位置がずれる。"""
    mount = mount or M0
    pin = dict(BEACON_PIN)
    if hint is not None:
        pin["x"], pin["y"] = hint
    s, n = FakeSupa(PINS + [pin]), nav or FakeNav()
    n.pose = start
    board = U.FakeUwbBoard(clock=lambda: _clk["t"], **board_kw)
    base_drop = board.drop_rate

    def target():
        d, b = BL.simulate_reading(n.pose, beacon_xy, mount)      # b は基板と同じく鏡写し済み（±90°）
        if shadow:
            behind = abs(BL.bearing_from_antenna(beacon_xy, n.pose, mount)) > 90.0   # 本当の方向で陰か決める
            board.drop_rate = 0.35 if behind else base_drop
            if behind:
                d, b = d + 0.45, b + 20.0
        return d, b

    board.target_fn = target
    uwb = U.UwbReader(board)
    br = make_bridge(s, n, uwb=uwb, beacon_search_sec=1.0, uwb_mount=mount, cancel_hold=0.0)
    br._clock = lambda: _clk["t"]
    br._sleep = lambda sec: _clk.__setitem__("t", _clk["t"] + sec)
    board.wait = br._sleep                # 次の行が来るまで待つ＝テストの時計を進める
    _turn0 = n.turn

    def _turn(angle):
        _clk["t"] += turn_sec             # 回るのに時間がかかる（その間に基板の行がたまる）
        return _turn0(angle)

    n.turn = _turn
    return s, n, br, uwb, board


def _turns_before_go(events):
    out = []
    for ev in events:
        if ev[0] == "go":
            break
        if ev[0] == "turn":
            out.append(ev[1])
    return out


def _expected_standoff(beacon_xy, from_xy=(0.0, 0.0), standoff=0.5):
    th = math.atan2(beacon_xy[1] - from_xy[1], beacon_xy[0] - from_xy[0])
    return (beacon_xy[0] - standoff * math.cos(th), beacon_xy[1] - standoff * math.sin(th), th)


print("  --- 正常系: 前方のユニット (2, 1) → 探す（前後を決める 30°）→ 手前 0.5 m → 受取 → 帰還 ---")
s, n, br, uwb, board = _mk_world((2.0, 1.0))
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cb1", 100))
check("呼出は done まで進む", s.calls["cb1"], "done")
_seq = [st for st in s.states() if st in ("arrived", "returning")] + [s.status["state"]]
_seq = [st for i, st in enumerate(_seq) if i == 0 or st != _seq[i - 1]]          # 連続の重複（帰還中の現在地書き込み）はまとめる
check("robot_status: arrived → returning → idle の順を含む", _seq, ["arrived", "returning", "idle"])
_ev = [ev[0] if ev[0] == "turn" else ev for ev in n.events]
check("出来事: 自己位置推定 → 相手の方向へ向く → 30° 回って前後を決める → ビーコンへ → 本棚へ",
      _ev, [("localize", "本棚の場所"), "turn", "turn", ("go", "ビーコン（UWB）"), ("go", "本棚の場所")])
_tb = _turns_before_go(n.events)
check("向く角度は約 28°（前方 (2, 1) の方向）・確かめは ±30°", (20 <= _tb[0] <= 35, abs(_tb[1])), (True, 30))
_g = n.goals[0]
_x, _y, _th = _expected_standoff((2.0, 1.0))
check("ゴールはユニットの 0.5 m 手前（誤差 5 cm 以内）", math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, True)
check("ゴールの向きはユニットのほう", abs(_g[2] - _th) < math.radians(3), True)
check("基板には stop → uwbcfg 9 … → antenna → initf → … → stop を送った（電波法の列のまま）",
      (uwb.sent[:4], uwb.sent[-1]), (["stop", U.UWBCFG_CH9, "antenna js1055-9", "initf"], "stop"))
check("走行中の detail に「ビーコン」", any("ビーコン" in (b.get("detail") or "") for t, b in s.log if t == "robot_status"), True)

print("  --- 後ろのユニット (−2, 0.5): 鏡写しに見えるが、回って見分け、そちらを向いてから走る ---")
s, n, br, uwb, board = _mk_world((-2.0, 0.5))
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cb2", 100))
check("done まで進む", s.calls["cb2"], "done")
_turns = [ev for ev in n.events if ev[0] == "turn"]
check("30° 回って前後を決めたあと、相手のほう（約 136°）へ向く",
      (len(_turns) >= 2, _turns[0], 120 <= _turns[1][1] <= 150), (True, ("turn", 30), True))
_g = n.goals[0]
_x, _y, _th = _expected_standoff((-2.0, 0.5))
check("ゴールは後ろのユニットの 0.5 m 手前", math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, True)

print("  --- ばらつき・測れない回があっても着く ---")
s, n, br, uwb, board = _mk_world((1.5, -1.0), noise_m=0.05, noise_deg=4.0, drop_rate=0.1, seed=3)
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cb3", 100))
_g = n.goals[0]
_x, _y, _th = _expected_standoff((1.5, -1.0))
check("done・ゴールは 0.5 m 手前から 15 cm 以内", (s.calls["cb3"], math.hypot(_g[0] - _x, _g[1] - _y) < 0.15), ("done", True))

print("  --- 見つからない: 何も測れない → 60° ずつ 1 周 → canceled ---")
s, n, br, uwb, board = _mk_world((2.0, 0.0), drop_rate=1.0)
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cb4", 100))
check("canceled・走らない", (s.calls["cb4"], [ev for ev in n.events if ev[0] == "go"]), ("canceled", []))
check("60° × 6 回探した", [ev for ev in n.events if ev[0] == "turn"], [("turn", 60)] * 6)
check("理由「見つかりません」を detail に", "見つかりません" in (s.status.get("detail") or ""), True)
check("基板は stop してある", uwb.sent[-1], "stop")

print("  --- 走行の失敗: 手前を 0.2 m 広げてもう一度 → それでもだめなら canceled ---")
s, n, br, uwb, board = _mk_world((2.0, 0.0))
n.fail_go_at = 1
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cb5", 100))
check("1 回目失敗 → 2 回目（手前 0.7 m）は成功 → done", (s.calls["cb5"], len(n.goals)), ("done", 3))
check("2 回目のゴールは 0.7 m 手前", round(n.goals[1][0], 2), round(2.0 - 0.7, 2))
s, n, br, uwb, board = _mk_world((2.0, 0.0))
n.fail_go_at = (1, 2)
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cb6", 100))
check("2 回とも失敗 → canceled・帰還しない", (s.calls["cb6"], len(n.goals), s.status["state"]), ("canceled", 2, "idle"))
check("理由「走行に失敗」", "走行に失敗" in (s.status.get("detail") or ""), True)

print("  --- 走りながら測り直し: 推定が 0.3 m 以上動いたらゴールを差し替える（上限つき）---")
s, n, br, uwb, board = _mk_world((2.0, 1.0))
uwb.start(sleep=br._sleep)
est = BL.BeaconEstimator(mount=M0)
for _yaw in (0.0, 0.5):                       # 2 つの向きから測ってある（前後は決まっている）
    n.pose = (0.0, 0.0, _yaw)
    _clk["t"] += 1.2
    uwb.flush_input()                         # 向きを変えている間にたまった行は捨てる
    for _ in range(6):
        for r in uwb.poll():
            est.add(n.pose, r)
_old = BL.BeaconEstimate(1.4, 1.0, 5, 0.0, "bearing", False, 1.4, 0.0)   # 探したときの推定（0.6 m ずれていた、とする）
br._beacon_goal = BL.standoff_goal(_old, (0.0, 0.0, 0.0), 0.5)
br._beacon_resend = 0
cb = br._beacon_progress(est, "ビーコン（UWB）")
_clk["t"] += 1.0
with contextlib.redirect_stdout(io.StringIO()):
    ng = cb(1.0, (0.0, 0.0, 0.5))
check("推定が 0.6 m 動いた → 新しいゴール（ユニット ≈ (2, 1) の手前）",
      (ng is not None, round(ng["beacon"][0], 1), round(ng["beacon"][1], 1), br._beacon_resend), (True, 2.0, 1.0, 1))
_clk["t"] += 1.0
check("動いていなければ None", cb(0.8, (0.0, 0.0, 0.5)), None)
br._beacon_resend = br.beacon_max_resend
br._beacon_goal = BL.standoff_goal(_old, (0.0, 0.0, 0.0), 0.5)
_clk["t"] += 1.0
check("差し替えの上限に達していれば、動いていても None", cb(0.5, (0.0, 0.0, 0.5)), None)

class FakeNavT(FakeNav):
    """走っている途中で 3 秒たつナビ（その間に基板の行が 15 本ほどたまる。走行中の測り直しを試すため）。"""

    def go(self, goal, on_progress=None):
        def op(rem, pose):
            _clk["t"] += 3.0
            return on_progress(rem, pose)
        return super().go(goal, op if on_progress else None)


def _yaw_before_go(events, start_yaw=0.0):
    return math.radians(sum(_turns_before_go(events))) + start_yaw


print("  --- 既定の取り付け（学会ver の機体: 後ろの端の台座・アンテナの面は前）: 機体の陰あり ---")
MD = BL.Mount()
check("既定の取り付けは、回転の中心から後ろへ 0.13 m・左右の中央・前向き・床から 0.28 m",
      (MD.x, MD.y, MD.yaw, MD.height), (-0.13, 0.0, 0.0, 0.28))
check("既定: 機体の前 2 m の点はアンテナの正面の範囲の内、後ろ 2 m の点は外（機体の陰）",
      (BL.in_antenna_cone((2.0, 0.0), (0.0, 0.0, 0.0), MD), BL.in_antenna_cone((-2.0, 0.0), (0.0, 0.0, 0.0), MD)), (True, False))
for _bxy, _cid in (((2.0, 1.0), "cd1"), ((3.5, 0.0), "cd2"), ((0.5, 2.0), "cd3"), ((-2.0, 0.5), "cd4"), ((1.2, -2.5), "cd5")):
    s, n, br, uwb, board = _mk_world(_bxy, mount=MD, shadow=True, nav=FakeNavT())
    with contextlib.redirect_stdout(io.StringIO()):
        br.handle(call(_cid, 100))
    _g = n.goals[0]
    _x, _y, _th = _expected_standoff(_bxy)
    check(f"既定の取り付け・ユニット {_bxy}: done・ゴールは 0.5 m 手前（誤差 5 cm 以内）・向きはユニットのほう",
          (s.calls[_cid], math.hypot(_g[0] - _x, _g[1] - _y) < 0.05,
           abs(math.atan2(math.sin(_g[2] - _th), math.cos(_g[2] - _th))) < math.radians(3)),
          ("done", True, True))
    check(f"既定の取り付け・ユニット {_bxy}: 推定した場所を書き戻す（誤差 10 cm 以内）",
          math.hypot(s.sp[100]["x"] - _bxy[0], s.sp[100]["y"] - _bxy[1]) < 0.10, True)

print("  --- 後ろ向きの取り付け（本棚の後ろの台座・アンテナの面は後ろ）: 前方のユニット (2, 1)・機体の陰あり ---")
MR = BL.Mount(x=-0.15, y=0.0, yaw=math.pi, height=0.25)
s, n, br, uwb, board = _mk_world((2.0, 1.0), mount=MR, shadow=True, nav=FakeNavT())
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cr1", 100))
check("done まで進む", s.calls["cr1"], "done")
_g = n.goals[0]
_x, _y, _th = _expected_standoff((2.0, 1.0))
check("ゴールはユニットの 0.5 m 手前（誤差 5 cm 以内）・向きはユニットのほう（出し入れ口を向ける）",
      (math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, abs(_g[2] - _th) < math.radians(3)), (True, True))
_ant_dir = _yaw_before_go(n.events) + math.pi
_bdir = math.atan2(1.0 - 0.0, 2.0 - (-0.15))
check("走る前に、後ろのアンテナをユニットへ向けて測っていた（35° 以内）",
      abs(math.degrees(math.atan2(math.sin(_ant_dir - _bdir), math.cos(_ant_dir - _bdir)))) < 35.0, True)
check("前へ走る間の機体ごしの測定は読み捨てる → ゴールを差し替えない", any(ev[0] == "retarget" for ev in n.events), False)
_sp = s.sp[100]
check("推定した場所をビーコンの行（stop_points）に書き戻す（誤差 10 cm 以内）",
      math.hypot(_sp["x"] - 2.0, _sp["y"] - 1.0) < 0.10, True)
check("書き戻しは stop_points の patch", any(t == "stop_points" for t, b in s.log), True)

print("  --- 後ろ向き: 2 回目の呼出は、まず前回の場所へアンテナを向けて測る（最初から陰にならない）---")
n.events.clear()
n.goals.clear()
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cr2", 100))
check("2 回目も done", s.calls["cr2"], "done")
_t2 = _turns_before_go(n.events)
check("最初に前回の場所へ約 −155° 向き、30° 回って前後を確かめる（正面に向き直す 1 回まで）",
      (len(_t2) in (2, 3), -165 <= _t2[0] <= -145, _t2[1], all(abs(t) <= 35 for t in _t2[2:])), (True, True, 30, True))
_g = n.goals[0]
check("2 回目のゴールも 0.5 m 手前（誤差 5 cm 以内）", math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, True)

print("  --- --beacon-hint-resolve（ユニットを動かさない運用）: 前回の場所で前後も決める → 回るのは 1 回だけ ---")
n.events.clear()
n.goals.clear()
br.beacon_prior_resolve = True
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cr2b", 100))
_t2 = _turns_before_go(n.events)
check("その場回転は前回の場所へ向く 1 回だけ", (len(_t2), -165 <= _t2[0] <= -145), (1, True))
_g = n.goals[0]
check("ゴールは 0.5 m 手前（誤差 5 cm 以内）", math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, True)

print("  --- 後ろ向き・機体の陰あり: 横に近い／前方右／後方斜めのユニットも、陰のない向きで測り直して着く ---")
# 2026-11-09 に見つけた失敗の再発防止: 陰の測定が混ざったまま前後を決めると、反対側へ走ることがあった
#   （横に近いと、向け直しても正面の範囲の端に残る → 確かめの 30° を逆へ回ると範囲の外に出る、など）
for _bxy, _cid in (((0.5, 2.0), "cr7"), ((2.5, -0.5), "cr8"), ((-1.2, -1.5), "cr9"), ((1.2, -2.5), "cr10")):
    s, n, br, uwb, board = _mk_world(_bxy, mount=MR, shadow=True, nav=FakeNavT())
    with contextlib.redirect_stdout(io.StringIO()):
        br.handle(call(_cid, 100))
    _g = n.goals[0]
    _x, _y, _th = _expected_standoff(_bxy)
    check(f"ユニット {_bxy}: done・ゴールは 0.5 m 手前（誤差 5 cm 以内）・差し替えなし",
          (s.calls[_cid], math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, any(ev[0] == "retarget" for ev in n.events)),
          ("done", True, False))

print("  --- 後ろ向き: 後方のユニット (−2, 0.5)（最初からアンテナの正面）---")
s, n, br, uwb, board = _mk_world((-2.0, 0.5), mount=MR, shadow=True)
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cr3", 100))
check("done まで進む", s.calls["cr3"], "done")
_g = n.goals[0]
_x, _y, _th = _expected_standoff((-2.0, 0.5))
check("ゴールは 0.5 m 手前（誤差 5 cm 以内）・向きはユニットのほう",
      (math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, abs(math.atan2(math.sin(_g[2] - _th), math.cos(_g[2] - _th))) < math.radians(3)),
      (True, True))

print("  --- 前回の場所が古い（ユニットを動かした）: 手がかりは外れるが、ふつうに探して見つける ---")
s, n, br, uwb, board = _mk_world((2.0, 1.0), mount=MR, shadow=True, hint=(-2.0, -1.5))
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cr4", 100))
check("done まで進む", s.calls["cr4"], "done")
_g = n.goals[0]
_x, _y, _th = _expected_standoff((2.0, 1.0))
check("ゴールは本当のユニットの 0.5 m 手前（誤差 5 cm 以内）", math.hypot(_g[0] - _x, _g[1] - _y) < 0.05, True)
check("書き戻しも本当の場所に直る（誤差 10 cm 以内）", math.hypot(s.sp[100]["x"] - 2.0, s.sp[100]["y"] - 1.0) < 0.10, True)

print("  --- 手がかりを使わない設定（--no-beacon-hint）・dry-run では書き戻さない ---")
s, n, br, uwb, board = _mk_world((2.0, 1.0), mount=MR, hint=(2.0, 1.0))
br.beacon_hint = False
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cr5", 100))
check("手がかりなし: 前回の場所（約 −155°）へは向かず、ふつうに探す", -165 <= _turns_before_go(n.events)[0] <= -145, False)
check("手がかりなしでも、ゴールは 0.5 m 手前（誤差 5 cm 以内）",
      math.hypot(n.goals[0][0] - _expected_standoff((2.0, 1.0))[0], n.goals[0][1] - _expected_standoff((2.0, 1.0))[1]) < 0.05, True)
s, n, br, uwb, board = _mk_world((2.0, 1.0), mount=MR)
br.live = False
with contextlib.redirect_stdout(io.StringIO()):
    br._process_beacon(call("cr6", 100), dict(s.sp[100]))
check("dry-run: stop_points には書かない", any(t == "stop_points" for t, b in s.log), False)
check("beacon_hint_from_row: 0,0（まだ推定していない）は None・それ以外は (x, y)",
      (B.beacon_hint_from_row({"x": 0, "y": 0}), B.beacon_hint_from_row({"x": 1.5, "y": -0.5}), B.beacon_hint_from_row(None)),
      (None, (1.5, -0.5), None))

print("  --- LoggingNavigator（--simulate）: turn と、走りながらのゴール差し替え ---")
_ln = B.LoggingNavigator(1.0, start_pose=(0.0, 0.0, 0.0))
with contextlib.redirect_stdout(io.StringIO()):
    _ln.turn(math.radians(90))
check("turn で向きが変わる", round(_ln.pose[2], 4), round(math.pi / 2, 4))
_g1 = B.goal_from_stop_point({"label": "a", "x": 2.0, "y": 0.0, "theta": 0.0})
_g2 = B.goal_from_stop_point({"label": "b", "x": 2.0, "y": 2.0, "theta": 0.0})
_calls = []
with contextlib.redirect_stdout(io.StringIO()):
    _ln.go(_g1, on_progress=lambda rem, pose: (_calls.append(round(rem, 2)) or _g2) if not _calls else None)
check("1 歩目のあとでゴールを差し替え → 最後は新しいゴールに着く",
      (len(_calls), tuple(round(v, 3) for v in _ln.pose[:2])), (1, (2.0, 2.0)))


# =====================================================================
print("\n=== 点検（2026-11-12）で直したところ: 取り消しの順番・途中で止まった呼出の後始末 ===")
# =====================================================================


def _idx(log, pred):
    return next((i for i, (t, b) in enumerate(log) if pred(t, b)), None)


print("  --- ロボ側の取り消しは「理由（robot_status）→ canceled（robot_calls）」の順で書く ---")
s, n = FakeSupa(PINS), FakeNav(fail_go_at=1)
br = make_bridge(s, n, cancel_hold=3.0)
_t_before = time.time()
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("ca1", 2))
_i_why = _idx(s.log, lambda t, b: t == "robot_status" and "走行に失敗" in (b.get("detail") or ""))
_i_can = _idx(s.log, lambda t, b: t == "robot_calls" and b.get("status") == "canceled")
check("走行の失敗: 理由を書いてから canceled（アプリが前の表示を理由として読まない）",
      (_i_why is not None, _i_can is not None, _i_why < _i_can), (True, True, True))
check("取り消したあと、次の呼出を始めるまで少し待つ（理由がアプリに届く時間）",
      _t_before + 2.5 <= br.hold_until <= time.time() + 3.5, True)
s, n = FakeSupa(PINS), FakeNav(fail_localize=True)
br = make_bridge(s, n, cancel_hold=0.0)
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("ca2", 2))
_i_why = _idx(s.log, lambda t, b: t == "robot_status" and "自己位置推定に失敗" in (b.get("detail") or ""))
_i_can = _idx(s.log, lambda t, b: t == "robot_calls" and b.get("status") == "canceled")
check("自己位置推定の失敗も同じ順", (_i_why is not None and _i_can is not None and _i_why < _i_can, s.status["state"]),
      (True, "idle"))
for _seat, _word, _cid in ((0, "届け先にできません", "ca3"), (9, "登録されていません", "ca4")):
    s, n = FakeSupa(PINS), FakeNav()
    br = make_bridge(s, n, cancel_hold=0.0)
    with contextlib.redirect_stdout(io.StringIO()):
        br.handle(call(_cid, _seat))
    check(f"届け先が使えない（seat={_seat}）: canceled・理由「{_word}」・走らない",
          (s.calls[_cid], _word in (s.status.get("detail") or ""), n.events), ("canceled", True, []))

print("  --- 例外で処理が途中になった呼出: 走行を止めて取り消す（取り残さない）---")


class BoomNav(FakeNav):
    """走っている途中で思わぬ例外が出るナビ。"""

    def go(self, goal, on_progress=None):
        self.events.append(("go", goal["label"]))
        raise RuntimeError("boom")


s, n = FakeSupa(PINS), BoomNav()
br = make_bridge(s, n, cancel_hold=0.0)
_raised = False
with contextlib.redirect_stdout(io.StringIO()):
    try:
        br.handle(call("cx1", 2))
    except RuntimeError:
        _raised = True
check("例外は main へ伝える（警告して次のポーリングへ）", _raised, True)
check("★Nav2 を止める（止めないと、ロボは走り続ける）★", ("cancel",) in n.events, True)
check("呼出は canceled・状態は idle・理由にエラーの名前", (s.calls["cx1"], s.status["state"], "RuntimeError" in (s.status.get("detail") or "")),
      ("canceled", "idle", True))
check("ブリッジは次の呼出を受けられる", (br.busy, br.current, br._pending), (False, None, {}))


class FlakySupa(FakeSupa):
    """down=True の間、書き込みが通信エラーになる Supabase。queued の行は rows から返す。"""

    def __init__(self, stop_points, rows=()):
        super().__init__(stop_points)
        self.rows = [dict(r) for r in rows]
        self.down = False
        self.n_fail = 0

    def select(self, table, query=""):
        if table == "robot_calls" and "status=eq.queued" in query:
            return [dict(r) for r in self.rows if self.calls.get(r["id"], "queued") == "queued"]
        return super().select(table, query)

    def patch(self, table, query, body):
        if self.down:
            self.n_fail += 1
            raise OSError("network unreachable")
        return super().patch(table, query, body)


class BoomDownNav(BoomNav):
    """走っている途中で例外が出て、同時に通信も切れる。"""

    def __init__(self, supa):
        super().__init__()
        self.supa = supa

    def go(self, goal, on_progress=None):
        self.supa.down = True
        return super().go(goal, on_progress)


s = FlakySupa(PINS, rows=[call("cy1", 2), call("cy2", 1)])
n = BoomDownNav(s)
br = make_bridge(s, n, cancel_hold=0.0)
with contextlib.redirect_stdout(io.StringIO()):
    try:
        br.poll_once()
    except Exception:
        pass
check("通信が切れていて後始末が書けない → 覚えておく（呼出は DB では moving のまま）",
      (list(br._pending), s.calls.get("cy1")), (["cy1"], "moving"))
with contextlib.redirect_stdout(io.StringIO()):
    br.poll_once()
check("通信が戻るまで、次の呼出には進まない", (list(br._pending), "cy2" in br.seen), (["cy1"], False))
s.down = False
n2 = FakeNav()
br.nav = n2
with contextlib.redirect_stdout(io.StringIO()):
    br.poll_once()
check("通信が戻ったら、まず後始末（canceled・idle）", (s.calls["cy1"], br._pending), ("canceled", {}))
check("そのあと、後ろの呼出（cy2）を処理する", (s.calls.get("cy2"), ("go", "席1") in n2.events), ("done", True))

print("  --- 処理済みの行が queued のまま残っても、後ろの呼出を止めない（dry-run は DB を書き換えない）---")
s = FlakySupa(PINS, rows=[call("cq1", 2), call("cq2", 1)])
n = FakeNav()
br = make_bridge(s, n, live=False, cancel_hold=0.0)
with contextlib.redirect_stdout(io.StringIO()):
    br.poll_once()
    br.poll_once()
    br.poll_once()
check("1 件目のあと、2 件目も処理する（3 回目は何もしない）",
      [ev for ev in n.events if ev[0] == "go" and ev[1].startswith("席")], [("go", "席2"), ("go", "席1")])

print("  --- set_call: 通信の一時的な失敗は 2 回までやり直す ---")


class OnceFailSupa(FakeSupa):
    def __init__(self, stop_points, fail_n):
        super().__init__(stop_points)
        self.fail_n = fail_n

    def patch(self, table, query, body):
        if table == "robot_calls" and self.fail_n > 0:
            self.fail_n -= 1
            raise OSError("timeout")
        return super().patch(table, query, body)


s, n = OnceFailSupa(PINS, 2), FakeNav()
br = make_bridge(s, n, cancel_hold=0.0)
br._sleep = lambda sec: None
with contextlib.redirect_stdout(io.StringIO()):
    br.set_call("cz1", "arrived")
check("2 回失敗しても 3 回目で書ける", s.calls.get("cz1"), "arrived")
s = OnceFailSupa(PINS, 3)
br = make_bridge(s, FakeNav(), cancel_hold=0.0)
br._sleep = lambda sec: None
_raised = False
with contextlib.redirect_stdout(io.StringIO()):
    try:
        br.set_call("cz2", "arrived")
    except OSError:
        _raised = True
check("3 回とも失敗したら例外（handle が後始末する）", _raised, True)


# =====================================================================
print("\n=== 点検で直したところ: ビーコンの探索（古い測定・回れない・前後が決まらない）===")
# =====================================================================

print("  --- ★古い測定を捨てないと間違う★（この検算の世界は、回るのに 2 秒かかり、その間の行がたまる）---")
_spots = ((2.0, 1.0), (0.5, 2.0), (2.5, -0.5), (-1.2, -1.5), (1.2, -2.5), (-2.0, 0.5))
_bad_noflush, _bad_flush, _flushed = 0, 0, 0
for _k, _bxy in enumerate(_spots):
    for _disable in (True, False):
        s, n, br, uwb, board = _mk_world(_bxy, mount=MR, shadow=True)
        if _disable:
            uwb.flush_input = lambda: None                     # 直す前の作り（捨てない）を真似る
        with contextlib.redirect_stdout(io.StringIO()):
            br.handle(call(f"cs{_k}{int(_disable)}", 100))
        _x, _y, _ = _expected_standoff(_bxy)
        _wrong = bool(n.goals) and math.hypot(n.goals[0][0] - _x, n.goals[0][1] - _y) > 0.3
        if _disable:
            _bad_noflush += _wrong
        else:
            _bad_flush += _wrong or s.calls[f"cs{_k}0"] != "done"
            _flushed += board.n_flushed
check("捨てない作りだと、違う場所へ走る場面が出る（＝この検算は古い行の問題を見つけられる）", _bad_noflush >= 1, True)
check("捨てる作り（いまのコード）では、6 か所とも正しい場所へ着く・行を実際に捨てている", (_bad_flush, _flushed > 0), (0, True))


class NoTurnNav(FakeNav):
    """その場回転ができないナビ（受け付けない・回らない）。"""

    def turn(self, angle):
        self.events.append(("turn", round(math.degrees(angle))))
        return False


class LieTurnNav(FakeNav):
    """「回れた」と返すが、実際には回っていないナビ（位置の推定が変わらない）。"""

    def turn(self, angle):
        self.events.append(("turn", round(math.degrees(angle))))
        return True


class HalfTurnNav(FakeNav):
    """途中で止まるナビ（6 割回って「失敗」を返す）。"""

    def turn(self, angle):
        x, y, yaw = self.pose
        self.pose = (x, y, math.atan2(math.sin(yaw + 0.6 * angle), math.cos(yaw + 0.6 * angle)))
        self.events.append(("turn", round(math.degrees(angle))))
        return False


print("  --- その場で回れない → 走らずに取り消す（理由つき）---")
s, n, br, uwb, board = _mk_world((2.0, 1.0), nav=NoTurnNav())
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("ct1", 100))
check("canceled・走らない・理由「回れません」",
      (s.calls["ct1"], [ev for ev in n.events if ev[0] == "go"], "回れません" in (s.status.get("detail") or "")),
      ("canceled", [], True))
check("1 回やり直してからやめる（同じ角度を 2 回）", [ev for ev in n.events if ev[0] == "turn"][:2] == [n.events[1]] * 2
      and len([ev for ev in n.events if ev[0] == "turn"]) == 2, True)
check("基板は stop してある・理由を書いてから canceled",
      (uwb.sent[-1], _idx(s.log, lambda t, b: t == "robot_status" and "回れません" in (b.get("detail") or ""))
       < _idx(s.log, lambda t, b: t == "robot_calls" and b.get("status") == "canceled")), ("stop", True))

print("  --- 「回れた」と言うが回っていない → 前後が決まらない → 走らずに取り消す ---")
s, n, br, uwb, board = _mk_world((2.0, 1.0), nav=LieTurnNav())
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("ct2", 100))
check("canceled・走らない・理由「方向を決められません」",
      (s.calls["ct2"], [ev for ev in n.events if ev[0] == "go"], "方向を決められません" in (s.status.get("detail") or "")),
      ("canceled", [], True))
check("前後が決まっていない推定は、地図（stop_points）にも書かない", any(t == "stop_points" for t, b in s.log), False)

print("  --- 途中で止まる回転（6 割）でも、実際の向きで計算して着く ---")
s, n, br, uwb, board = _mk_world((2.0, 1.0), nav=HalfTurnNav())
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("ct3", 100))
_x, _y, _ = _expected_standoff((2.0, 1.0))
check("done・ゴールは 0.5 m 手前（誤差 5 cm 以内）",
      (s.calls["ct3"], bool(n.goals) and math.hypot(n.goals[0][0] - _x, n.goals[0][1] - _y) < 0.05), ("done", True))

print("  --- 回ったあとの位置: ナビの refresh_pose → だめなら「実際に回った角度」→ どちらも無ければ今の推定 ---")


class StaleNav(FakeNav):
    """current_pose() が古い（回る前のまま）ナビ。本当の向きは true_yaw。refresh か last_turn_rad でしか分からない。"""

    def __init__(self, mode):
        super().__init__()
        self.mode = mode                 # "refresh" / "odom" / "none"
        self.true_yaw = 0.0
        self.n_refresh = 0

    def turn(self, angle):
        self.true_yaw = math.atan2(math.sin(self.true_yaw + angle), math.cos(self.true_yaw + angle))
        self.last_turn_rad = angle if self.mode == "odom" else None
        self.events.append(("turn", round(math.degrees(angle))))
        return True                      # self.pose（AMCL の推定）は更新しない＝古いまま

    def refresh_pose(self):
        self.n_refresh += 1
        if self.mode != "refresh":
            return None
        self.pose = (self.pose[0], self.pose[1], self.true_yaw)
        return self.pose


for _mode, _want in (("refresh", 90.0), ("odom", 90.0), ("none", 0.0)):
    _sn = StaleNav(_mode)
    s, n, br, uwb, board = _mk_world((2.0, 1.0), nav=_sn)
    with contextlib.redirect_stdout(io.StringIO()):
        _turned = br._beacon_turn(90.0)
    check(f"{_mode}: 回ったあとの向きは {_want:.0f}°（止まっている間はこの値を使う）",
          (round(math.degrees(br._beacon_pose()[2]), 1), None if _turned is None else round(_turned, 1)), (_want, _want))

print("  --- 探している間も robot_status に進み具合を書く（アプリの「更新が止まっています」を防ぐ）---")
s, n, br, uwb, board = _mk_world((-2.0, 0.5), mount=MR, shadow=True, hint=(2.0, 1.0))   # 手がかりが外れていて、長く探す場面
_stamps = []
_patch0 = s.patch


def _patch_t(table, query, body):
    if table == "robot_status":
        _stamps.append((_clk["t"], body.get("detail") or ""))
    return _patch0(table, query, body)


s.patch = _patch_t
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cn1", 100))
_search = [d for t, d in _stamps if "探しています" in d]
_gaps = [b[0] - a[0] for a, b in zip(_stamps, _stamps[1:])]
check("探している間に 3 回以上書く・書き込みの間隔は 60 秒より十分短い",
      (s.calls["cn1"], len(_search) >= 3, max(_gaps) < 30.0), ("done", True, True))

print("  --- 基板の設定を確かめられない → 測り始めない・走らない ---")
s, n, br, uwb, board = _mk_world((2.0, 1.0))
board.cfg[0] = 5
board.lock_config = True
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cc1", 100))
check("canceled・理由に「チャネル」・initf は送らない・走らない",
      (s.calls["cc1"], "チャネル" in (s.status.get("detail") or ""), "initf" in board.received,
       [ev for ev in n.events if ev[0] in ("go", "turn")]), ("canceled", True, False, []))

print("  --- 基板との通信エラー: 探している途中なら Nav2 を止めて取り消す／走行中なら測り直しだけやめて走りきる ---")
s, n, br, uwb, board = _mk_world((2.0, 1.0))
_rl0 = board.readline
_cnt = {"n": 0}


def _rl_fail_later():
    _cnt["n"] += 1
    if _cnt["n"] > 30:                       # 測り始めてすぐ USB が抜けた（設定の返事を読み終えたあたり）
        raise OSError("device disconnected")
    return _rl0()


board.readline = _rl_fail_later
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("ce1", 100))
check("探している途中のエラー: canceled・Nav2 に止める指示・理由「UWB の読み取りに失敗」",
      (s.calls["ce1"], ("cancel",) in n.events, "UWB の読み取りに失敗" in (s.status.get("detail") or "")),
      ("canceled", True, True))


class BreakOnGoNav(FakeNav):
    """ビーコンへ走り出したところで、基板が読めなくなる。"""

    def go(self, goal, on_progress=None):
        if goal.get("beacon") and self.board is not None:
            def _dead():
                raise OSError("device disconnected")
            self.board.readline = _dead
        return super().go(goal, on_progress)


_bn = BreakOnGoNav()
_bn.board = None
s, n, br, uwb, board = _mk_world((2.0, 1.0), nav=_bn)
_bn.board = board
_raised = False
with contextlib.redirect_stdout(io.StringIO()):
    try:
        br.handle(call("ce2", 100))
    except Exception:
        _raised = True
check("走行中のエラー: 例外を外へ出さず（Nav2 を置き去りにしない）、探したときのゴールへ走りきって done",
      (_raised, s.calls["ce2"], len([ev for ev in n.events if ev[0] == "go"])), (False, "done", 2))

print("  --- 着いたあとの確認: UWB の距離が計算と大きく違えば、到着の表示に注意書きを足す（取り消しはしない）---")
_bx = [2.0, 1.0]                              # ユニットの本当の場所（途中で動かせるようにリスト）


class MoveUnitNav(FakeNav):
    """ビーコンへ走っている間に、ユニットを遠くへ動かす（＝推定した場所が違っていた、の真似）。"""

    def go(self, goal, on_progress=None):
        ok = super().go(goal, on_progress)
        if goal.get("beacon"):
            _bx[0], _bx[1] = -3.0, -2.0
        return ok


s, n, br, uwb, board = _mk_world(_bx, nav=MoveUnitNav())
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cv1", 100))
_arr = [b.get("detail") or "" for t, b in s.log if t == "robot_status" and b.get("state") == "arrived"]
check("距離が合わない: arrived のまま・表示に「場所が違うかもしれません」",
      (s.calls["cv1"], bool(_arr) and "場所が違うかもしれません" in _arr[0]), ("done", True))
s, n, br, uwb, board = _mk_world((2.0, 1.0))
with contextlib.redirect_stdout(io.StringIO()):
    br.handle(call("cv2", 100))
_arr = [b.get("detail") or "" for t, b in s.log if t == "robot_status" and b.get("state") == "arrived"]
check("距離が合っている: 注意書きなし", (s.calls["cv2"], bool(_arr) and "場所が違う" not in _arr[0]), ("done", True))

print("  --- 距離だけの解（三点測位）は、測った場所が十分に広がっているときだけ使う ---")
check("一直線に並んだ点の広がりは 0", round(BL.position_spread([(0, 0), (1, 0), (2, 0)]), 6), 0.0)
_circ = [(0.15 * math.cos(math.radians(a)), 0.15 * math.sin(math.radians(a))) for a in range(0, 360, 30)]
check("半径 15 cm の円（その場で回っただけ）の広がりは約 10.6 cm → 足りない",
      (round(BL.position_spread(_circ), 3), BL.position_spread(_circ) < BL.TRI_MIN_SPREAD_M), (0.106, True))
check("1 m 四方に散った点は足りる", BL.position_spread([(0, 0), (1, 0), (1, 1), (0, 1)]) >= BL.TRI_MIN_SPREAD_M, True)
_est = BL.BeaconEstimator(mount=MR)
for _a in (0, 30, 60, 90, 120, 150):         # その場で回りながら、方向が使えない回（真横）だけを 6 回
    _pose = (0.0, 0.0, math.radians(_a))
    _d, _ = BL.simulate_reading(_pose, (2.0, 1.0), MR)
    _est.add(_pose, _reading(_d + 0.05, 80.0))
check("その場で回っただけ・方向が使えない回だけ → 推定を出さない（小さな足場から位置を作らない）", _est.estimate(), None)
_est = BL.BeaconEstimator(mount=MR)
for _p in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.5), (0.0, 1.2), (-0.8, 0.5)):   # 広く動きながら
    _pose = (_p[0], _p[1], 0.0)
    _d, _ = BL.simulate_reading(_pose, (2.0, 1.0), MR)
    _est.add(_pose, _reading(_d, 80.0))
_e = _est.estimate()
check("広く動いて測れば、距離だけで位置が出る（誤差 5 cm 以内）",
      (_e is not None and _e.method, _e is not None and math.hypot(_e.x - 2.0, _e.y - 1.0) < 0.05), ("range", True))


# =====================================================================
print("\n=== 点検で直したところ: Nav2 の呼び方（ROS の無い PC で、偽の BasicNavigator を差し込んで実際に動かす）===")
# =====================================================================


class _FakeTime:
    """bookshelf_bridge の time を差し替える（待ち時間を本当には待たない）。"""

    def __init__(self):
        self.t = 5000.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, sec):
        self.t += sec


class _Obj:
    """属性を好きに足せる入れ物（メッセージの代わり）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _PoseMsg:
    def __init__(self):
        self.header = _Obj(frame_id="", stamp=None)
        self.pose = _Obj(position=_Obj(x=0.0, y=0.0), orientation=_Obj(z=0.0, w=1.0))


class _FakeSpinClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.asked = 0

    def wait_for_server(self, timeout_sec=None):
        self.asked += 1
        return self.ready


class _FakeSrvClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = 0

    def wait_for_service(self, timeout_sec=None):
        return self.ready

    def service_is_ready(self):
        return self.ready

    def call_async(self, req):
        self.calls += 1


class _FakeNav2(_FakeBasicNav):
    """BasicNavigator の代わり。呼ばれた順を記録し、決めた回数だけ「まだ走行中」と答える。"""

    def __init__(self, accept=(True,), ticks=2, result="SUCCEEDED", spin_ready=True, traveled=None, srv_ready=True):
        super().__init__()
        self.calls = []
        self.accept = list(accept)        # goToPose／spin が受け付けられるか（呼ぶたびに 1 つずつ使う。最後の値を使い続ける）
        self.ticks = ticks
        self.left = 0
        self.result = result
        self.prev_result = "SUCCEEDED"    # 前の動作の結果（受け付けられなかったとき、getResult はこれを返す）
        self.active = False
        self.spin_client = _FakeSpinClient(spin_ready)
        self.traveled = traveled
        self.feedback = "old"
        self.srv = _FakeSrvClient(srv_ready)

    def _accept(self):
        return self.accept.pop(0) if len(self.accept) > 1 else self.accept[0]

    def get_clock(self):
        return _Obj(now=lambda: _Obj(to_msg=lambda: None))

    def goToPose(self, pose):
        ok = self._accept()
        self.calls.append(("goToPose", round(pose.pose.position.x, 2), round(pose.pose.position.y, 2), ok))
        if ok:
            self.active, self.left = True, self.ticks
        return ok

    def spin(self, spin_dist=0.0, time_allowance=10):
        ok = self._accept()
        self.calls.append(("spin", round(math.degrees(spin_dist)), time_allowance, ok))
        if ok:
            self.active, self.left = True, self.ticks
            if self.traveled is not None:
                self.feedback = _Obj(angular_distance_traveled=self.traveled)
        return ok

    def isTaskComplete(self):
        if not self.active:
            return True                   # 受け付けられていない → 前の動作の「完了」が見える
        self.left -= 1
        if self.left <= 0:
            self.active = False
            self.prev_result = self.result
            return True
        return False

    def getResult(self):
        return self.prev_result

    def getFeedback(self):
        return self.feedback

    def cancelTask(self):
        self.calls.append(("cancelTask",))
        self.active = False

    def create_client(self, srv_type, name):
        self.calls.append(("create_client", name))
        return self.srv


def _mk_nav2(fake, pose=(0.0, 0.0, 0.0)):
    nv2 = B.Nav2Navigator.__new__(B.Nav2Navigator)      # ROS を import する __init__ は通さない
    nv2.nav = fake
    nv2._nav_ready = True
    nv2._PoseStamped = _PoseMsg
    nv2._TaskResult = _Obj(SUCCEEDED="SUCCEEDED")
    nv2.timeout, nv2.verbose, nv2.heading_tolerance = 30.0, False, 0.0
    nv2._pose, nv2._pose_count = pose, 1
    nv2._rclpy = _Obj(spin_once=lambda node, timeout_sec=0.0: B.time.sleep(timeout_sec))
    return nv2


_GOAL = B.goal_from_stop_point({"label": "席X", "x": 1.0, "y": 2.0, "theta": 0.0})
_GOAL2 = B.goal_from_stop_point({"label": "席Y", "x": 3.0, "y": 0.5, "theta": 0.0})
_time0 = B.time
B.time = _FakeTime()
_std0 = {k: sys.modules.get(k) for k in ("std_srvs", "std_srvs.srv")}
_srv = types.ModuleType("std_srvs.srv")
_srv.Empty = type("Empty", (), {"Request": type("Request", (), {})})
_pkg = types.ModuleType("std_srvs")
_pkg.srv = _srv
sys.modules.update({"std_srvs": _pkg, "std_srvs.srv": _srv})
try:
    print("  --- go(): ふつうに着く／受け付けてもらえない／失敗 ---")
    _f = _FakeNav2()
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _mk_nav2(_f).go(_GOAL)
    check("受け付け → 走行 → 成功で True（ゴールは 1 回だけ送る）", (_ok, _f.calls), (True, [("goToPose", 1.0, 2.0, True)]))
    _f = _FakeNav2(accept=(False,))
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _mk_nav2(_f).go(_GOAL)
    check("★受け付けてもらえなければ False（前の動作の「成功」を到着と読み違えない）★", _ok, False)
    _f = _FakeNav2(result="ABORTED")
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _mk_nav2(_f).go(_GOAL)
    check("走行の結果が成功でなければ False", _ok, False)

    print("  --- go(): 走りながらのゴールの差し替えは、取り消さずに送り直すだけ ---")
    _f = _FakeNav2(ticks=4)
    _given = []
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _mk_nav2(_f).go(_GOAL, on_progress=lambda rem, pose: (_given.append(1) or _GOAL2) if not _given else None)
    check("新しいゴールを送る・cancelTask は呼ばない・着いたら True",
          (_ok, [c[:3] for c in _f.calls if c[0] == "goToPose"], ("cancelTask",) in _f.calls),
          (True, [("goToPose", 1.0, 2.0), ("goToPose", 3.0, 0.5)], False))
    _f = _FakeNav2(accept=(True, False), ticks=4)
    _given = []
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _mk_nav2(_f).go(_GOAL, on_progress=lambda rem, pose: (_given.append(1) or _GOAL2) if not _given else None)
    check("差し替えを受け付けてもらえなければ、走行を止めて False", (_ok, _f.calls[-1]), (False, ("cancelTask",)))

    print("  --- go(): 時間切れ ---")
    _f = _FakeNav2(ticks=10 ** 9)
    _nv = _mk_nav2(_f)
    _nv.timeout = 5.0
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _nv.go(_GOAL)
    check("timeout 秒を超えたら cancelTask して False", (_ok, _f.calls[-1]), (False, ("cancelTask",)))

    print("  --- turn(): Nav2 の spin を呼ぶ ---")
    _f = _FakeNav2(traveled=0.51)
    _nv = _mk_nav2(_f)
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _nv.turn(math.radians(30.0))
    check("受け付け → 完了 → 成功で True。角度は度に直して 30、制限時間は整数",
          (_ok, _f.calls), (True, [("spin", 30, 15, True)]))
    check("実際に回った角度（spin の報告）を控える", _nv.last_turn_rad, 0.51)
    _f = _FakeNav2(spin_ready=False)
    _nv = _mk_nav2(_f)
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _nv.turn(math.radians(30.0))
    check("★spin のサーバが無ければ、呼ばずに False（永久に待たない）★", (_ok, _f.calls, _f.spin_client.asked), (False, [], 1))
    _f = _FakeNav2(accept=(False,))
    _nv = _mk_nav2(_f)
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _nv.turn(math.radians(-45.0))
    check("受け付けてもらえなければ False・回った角度は None", (_ok, _nv.last_turn_rad), (False, None))
    _f = _FakeNav2(result="ABORTED", traveled=0.2)
    _nv = _mk_nav2(_f)
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _nv.turn(math.radians(60.0))
    check("途中で終わった（ABORTED）なら False。回ったぶんの角度は残す", (_ok, _nv.last_turn_rad), (False, 0.2))
    _f = _FakeNav2(ticks=10 ** 9)
    _nv = _mk_nav2(_f)
    with contextlib.redirect_stdout(io.StringIO()):
        _ok = _nv.turn(math.radians(60.0), time_allowance=3)
    check("時間切れなら cancelTask して False", (_ok, _f.calls[-1]), (False, ("cancelTask",)))
    _f = _FakeNav2()                          # 前の動作の報告（feedback="old"）が残っている
    _nv = _mk_nav2(_f)
    with contextlib.redirect_stdout(io.StringIO()):
        _nv.turn(math.radians(30.0))
    check("今回の報告が来なければ、前の動作の報告を使わない（None）", _nv.last_turn_rad, None)

    print("  --- refresh_pose(): AMCL に 1 回更新してもらう ---")
    _f = _FakeNav2()
    _nv = _mk_nav2(_f)

    def _spin_with_pose(node, timeout_sec=0.0):            # 待っている間に、新しい /amcl_pose が届く
        B.time.sleep(timeout_sec)
        _nv._pose, _nv._pose_count = (0.1, 0.2, 0.52), _nv._pose_count + 1

    _nv._rclpy = _Obj(spin_once=_spin_with_pose)
    with contextlib.redirect_stdout(io.StringIO()):
        _p = _nv.refresh_pose()
    check("サービスを呼び、新しい推定を受け取って返す", (_p, _f.srv.calls, ("create_client", "request_nomotion_update") in _f.calls),
          ((0.1, 0.2, 0.52), 1, True))
    _nv = _mk_nav2(_FakeNav2())
    with contextlib.redirect_stdout(io.StringIO()):
        _p = _nv.refresh_pose(timeout=1.0)
    check("新しい推定が来なければ None（呼ぶ側が回った角度の報告で補う）", _p, None)
    _f = _FakeNav2(srv_ready=False)
    _nv = _mk_nav2(_f)
    with contextlib.redirect_stdout(io.StringIO()):
        _p = _nv.refresh_pose()
    check("サービスが無ければ、呼ばずに None", (_p, _f.srv.calls), (None, 0))

    print("  --- --turn-test: その場回転と、回ったあとの向きの読み取りを確かめる ---")
    _f = _FakeNav2(traveled=0.52)
    _nv = _mk_nav2(_f)
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _good = B.turn_test(_nv, 30.0)
    check("回れて、回った角度の報告があれば OK", (_good, "OK" in _buf.getvalue()), (True, True))
    _f = _FakeNav2(spin_ready=False)
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _good = B.turn_test(_mk_nav2(_f), 30.0)
    check("回れなければ「要確認」", (_good, "要確認" in _buf.getvalue()), (False, True))
finally:
    B.time = _time0
    for _k, _v in _std0.items():
        if _v is None:
            sys.modules.pop(_k, None)
        else:
            sys.modules[_k] = _v


# =====================================================================
print("\n=== 点検で足した道具: 方向の符号の確認（uwb_reader.py --sign-check）===")
# =====================================================================
check("左へ動かして LAoA が増えた → +1", U.sign_check_verdict(2.0, 28.0)[0], +1)
check("左へ動かして LAoA が減った → −1", U.sign_check_verdict(2.0, -25.0)[0], -1)
check("変化が小さい → 決めない", U.sign_check_verdict(2.0, 6.0)[0], None)
check("読めていない → 決めない", (U.sign_check_verdict(None, 6.0)[0], U.sign_check_verdict(1.0, None)[0]), (None, None))
for _sgn in (+1, -1):
    _clk["t"] = 7000.0
    _bs = U.FakeUwbBoard(1.0, 0.0, clock=lambda: _clk["t"], angle_sign=_sgn)
    _bs.wait = lambda sec: _clk.__setitem__("t", _clk["t"] + sec)
    _rs = U.UwbReader(_bs, angle_sign=_sgn)
    _rs.start(sleep=lambda sec: None)
    with contextlib.redirect_stdout(io.StringIO()):
        _got = U.sign_check(_rs, ask=lambda prompt: "", seconds=2.0, clock=lambda: _clk["t"],
                            on_moved=lambda: _bs.set_target(1.1, 27.0))
    check(f"基板の符号が {_sgn:+d} なら、手順どおりに動かすと {_sgn:+d} と出る", _got, _sgn)


# =====================================================================
print("\n=== 起動の引数・ビーコンの行の扱い（2026-11-13）===")
# =====================================================================
check("--uwb-mount の値がマイナスで始まるとき、= でつなぐ形に直す",
      B.normalize_argv(["--live", "--uwb", "--uwb-mount", "-0.15,0,180,0.25", "--beacon-height", "0.45"]),
      ["--live", "--uwb", "--uwb-mount=-0.15,0,180,0.25", "--beacon-height", "0.45"])
check("= でつないだ書き方は、そのまま", B.normalize_argv(["--uwb-mount=-0.15,0,180,0.25"]), ["--uwb-mount=-0.15,0,180,0.25"])
check("プラスの値は、そのまま", B.normalize_argv(["--uwb-mount", "0.10,0,0,0.25"]), ["--uwb-mount", "0.10,0,0,0.25"])
check("次が別のオプションなら、つながない", B.normalize_argv(["--uwb-mount", "--live"]), ["--uwb-mount", "--live"])
check("値が無い（末尾）なら、そのまま", B.normalize_argv(["--uwb-mount"]), ["--uwb-mount"])
check("ほかのオプションのマイナスの値は触らない", B.normalize_argv(["--uwb-angle-sign", "-1"]), ["--uwb-angle-sign", "-1"])
_ap = __import__("argparse").ArgumentParser()
_ap.add_argument("--uwb-mount", default=None)
_ap.add_argument("--uwb-angle-sign", type=int, choices=[1, -1], default=1)
_ns = _ap.parse_args(B.normalize_argv(["--uwb-mount", "-0.15,0,180,0.25", "--uwb-angle-sign", "-1"]))
check("直したあとは argparse が読める（取り付けと符号）", (_ns.uwb_mount, _ns.uwb_angle_sign), ("-0.15,0,180,0.25", -1))
check("読めた取り付けの文字は parse_mount を通る", round(BL.parse_mount(_ns.uwb_mount).x, 2), -0.15)
_rows = [{"id": 0, "kind": "home", "x": 0, "y": 0}, {"id": 1, "kind": "seat", "x": 1, "y": 0},
         {"id": 100, "kind": "beacon", "label": "ビーコン（UWB）", "x": 1.5, "y": 0.3}, {"id": 2, "x": 1, "y": 1}]
_pins, _beacons = MC.split_pin_rows(_rows)
check("地図の点検: ビーコンの行はピンに数えない", ([r["id"] for r in _pins], [r["id"] for r in _beacons]), ([0, 1, 2], [100]))
check("地図の点検: 行が無い（None）でも落ちない", MC.split_pin_rows(None), ([], []))
check("ピン建て: ビーコンの行を見分ける", (PT.is_beacon(_rows[2]), PT.is_beacon(_rows[1]), PT.is_beacon(_rows[3]), PT.is_beacon(None)),
      (True, False, False, False))
check("ピン建て: 一覧ではビーコンを席と書かない", "[UWB ]" in PT.format_pin(_rows[2]) and "[席 ]" in PT.format_pin({"id": 1, "kind": "seat", "x": 0, "y": 0}), True)
check("ピン建て: 断りの文に番号が入る", "id=100" in PT.BEACON_REFUSE.format(id=100), True)


print()
print("=" * 46)
print("  すべて成功" if ok else "  ★失敗があります★")
print("=" * 46)
sys.exit(0 if ok else 1)
