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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "nav2"))
import make_nav2_params as NP     # noqa: E402  （yaml は main() の中でしか import しない）
import map_check as MC            # noqa: E402

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
# 位置はきっちり、向きはゆるく（2026-09-21 PM 決定: 向きは ±30° でよい）
check("ゴールの許容: 位置 8 cm・向き 30°（0.52 rad）",
      (_yaml_num("xy_goal_tolerance"), round(math.degrees(_yaml_num("yaw_goal_tolerance")))), (0.08, 30))
# 回頭は 0.31 rad/s より遅くできない。的に入ってから止まるまでの行きすぎ（0.4 秒ぶん）が、的の幅に収まること
check("回頭の行きすぎ（回頭の速さ × 0.4 秒）が、向きの許容の幅（±）に収まる",
      _yaml_num("rotate_to_heading_angular_vel") * 0.4 < 2 * _yaml_num("yaw_goal_tolerance"), True)
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
check("向きの許容が 0.25 rad のまま（古い設定）も指摘する",
      ["向きの許容" in t for t in B.check_nav2_motion_params(dict(_new, **{"general_goal_checker.yaw_goal_tolerance": 0.25}))], [True])
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


print()
print("=" * 46)
print("  すべて成功" if ok else "  ★失敗があります★")
print("=" * 46)
sys.exit(0 if ok else 1)
