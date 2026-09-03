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


print()
print("=" * 46)
print("  すべて成功" if ok else "  ★失敗があります★")
print("=" * 46)
sys.exit(0 if ok else 1)
