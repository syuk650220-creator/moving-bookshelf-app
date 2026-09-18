"""
test_logic.py ― 実機も pyserial も無しで、計算だけを検証する

    python test_logic.py

pyserial と requests をスタブ（にせもの）に差し替えて import するので、
何もインストールしていない PC でも走ります。
★パケットの形・順運動学の符号・量子化のヒステリシスを変えたら、必ずこれを実行★
"""

import io
import math
import os
import re
import struct
import sys
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
        return [dict(body)]

    def states(self):
        return [b["state"] for t, b in self.log if t == "robot_status"]

    def call_states(self):
        return [b["status"] for t, b in self.log if t == "robot_calls"]


class FakeNav:
    """呼ばれた順番を記録するナビ。fail_go_at=n で n 回目の go を失敗させる。"""

    def __init__(self, fail_go_at=None, fail_localize=False, interrupt_go=False):
        self.events = []
        self.pose = (0.0, 0.0, 0.0)
        self.n_go = 0
        self.fail_go_at = fail_go_at
        self.fail_localize = fail_localize
        self.interrupt_go = interrupt_go

    def localize(self, init_goal):
        self.events.append(("localize", None if init_goal is None else init_goal["label"]))
        return not self.fail_localize

    def go(self, goal, on_progress=None):
        self.n_go += 1
        self.events.append(("go", goal["label"]))
        if self.interrupt_go:
            raise KeyboardInterrupt
        if on_progress:
            on_progress(1.23, (goal["x"] / 2, goal["y"] / 2, goal["theta"]))
        if self.fail_go_at == self.n_go:
            return False
        self.pose = (goal["x"], goal["y"], goal["theta"])
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

print("  --- 2 件目: 本棚に居るので再び初期位置を与える ---")
br.handle(call("c2", 1))
check("2 件目も localize(本棚の場所) から", n.events[3], ("localize", "本棚の場所"))
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


print()
print("=" * 46)
print("  すべて成功" if ok else "  ★失敗があります★")
print("=" * 46)
sys.exit(0 if ok else 1)
