"""
uwb_reader.py ― UWB 基板から「距離と方向」を読む  ★S1（UWB ビーコン拡張）★

席に置いた UWB ユニット（村田 Type 2AB 評価基板＝応える側）と、ロボに載せた同じ基板（測る側）が
電波で測り合い、測る側の基板が USB シリアルに 1 秒 5 回、1 行 1 JSON で結果を流します。
このファイルは、その「文字の行」を Python の「数値」に変える耳の役です。

    UWB 基板（J6 の USB）──/dev/uwb──> 【ここ】──UwbReading（距離 m・方向 deg）──> beacon_locator.py（S2）→ ブリッジ（S3）

────────────────────────────────────────────────────────────
基板が流す 1 行（デモ用 CLI ファームの書式。1 行＝1 回の測定）
────────────────────────────────────────────────────────────

  {"Block":12,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":166,"LPDoA_deg":-30.99,"LAoA_deg":-9.65,"LFoM":86,"RAoA_deg":15.52,"CFO_100ppm":415}]}

    Block     … 何回目の測定か（1 ずつ増える）
    Addr      … 相手（席のユニット）のアドレス
    Status    … "Ok" ならその回は測れた。それ以外の回の数値は使わない
    D_cm      … 距離 [cm]                 → distance_m（m に直す）
    LAoA_deg  … 相手の方向 [度]（−90〜+90） → bearing_deg  ★方向として使うのはこれ★
    LPDoA_deg … 2 本のアンテナの位相差の生の値 [度]（参考）
    LFoM      … 自分側の確からしさ（大きいほど良い）
    RAoA_deg  … 相手側が測った方向（参考）
    先に `diag 1` を打つと "RSSI_dBm"（受信の強さ）と "NLOS_%"（見通し無しの確率）も付く

  ・`JS00BE{...}` のように JS＋16 進 4 桁で始まる行は、命令への返事（設定の表示）。測定ではないので捨てる
  ・`ok` / `error` / 空行 も捨てる
  ・方向の符号（左が＋か右が＋か）は実測で決める → ANGLE_SIGN。bearing_deg は常に「左が＋」（ROS の決まり）に揃える

────────────────────────────────────────────────────────────
★電波法（技適）の約束 ― このファイルはそれをコードで固定している★
────────────────────────────────────────────────────────────
  このモジュール（LBUA5QJ2AB）の日本の認証は UWB チャネル 9（7.587〜8.4 GHz）だけです。
    ・チャネルは 9 に固定（`uwbcfg 9 …`）。9 以外の `uwbcfg` は check_command() が例外にして送らない
    ・送信の強さ（`txpower`）は絶対に送らない（較正値から変えると認証の条件から外れる）
    ・アンテナ型は `js1055-9`（付属の基板アンテナ）だけ。ほかの型は送らない
    ・出荷時の試験ファーム用の AT 命令（AT+…）も送らない
  基板に命令を送る道は UwbReader.send() の 1 本だけで、必ず check_command() を通ります。
  技適の表示（R 007-AM0109）はモジュール本体ではなく、モジュールの包装（防湿袋）のラベルと村田の公開データシート 4.1 にある
  （証明規則 第 20 条第 1 項第 1 号の方式。2026-11-07 に確認）。データシートの該当ページを基板と一緒に保管し、ロボにマークの図を自分で貼らない。
  --simulate は電波を出しません（基板にも触りません）。

────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────
  python3 uwb_reader.py                      # Pi: /dev/uwb を開いて測り始め、1 行ずつ表示（Ctrl-C で stop）
  python3 uwb_reader.py --port COM5          # PC（Windows）で基板を直接。pyserial が要る（pip install pyserial）
  python3 uwb_reader.py --simulate           # 基板なし。偽の基板が「1.5 m・左 20°」の相手を流す（電波は出ない）
  python3 uwb_reader.py --log 出力の見本_20261106.txt --csv uwb_20261106.csv --seconds 90
                                             # 生の行をそのまま保存（ロードマップ 6-1 ② の「出力の見本」）＋ 表計算用 CSV
  python3 uwb_reader.py --role respf         # 席側の基板を Pi/PC から「応える側」として起こす
  python3 uwb_reader.py --no-config          # save 済みの基板: uwbcfg / antenna を打たず initf だけ
  python3 uwb_reader.py --diag               # diag 1 を先に打って RSSI と NLOS も読む
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime


# =====================================================================
#  設定（基板まわりの決まりはここだけ見れば足りるように）
# =====================================================================

DEFAULT_PORT = "/dev/uwb"     # udev/99-uwb.rules が作る固定名（Pi）。PC なら COM5 など。"auto" で探す
BAUD = 115200                 # 115200 bps・8 bit・パリティ無し・ストップ 1（Tera Term と同じ）
EOL = "\r"                    # 命令の終わりは CR。Tera Term の「Transmit: CR」と同じ（LF だけだと命令が通らない）
RATE_HZ = 5.0                 # 基板は 200 ms ごとに 1 行（1 秒に 5 回）
UWB_VID, UWB_PID = 0x1915, 0x520F   # nRF52840 の USB CDC（Nordic）。udev ルールと同じ番号

CHANNEL = 9                   # ★日本で使えるのはチャネル 9 だけ★（7.9872 GHz。認証は 7.587〜8.4 GHz）
ANTENNA = "js1055-9"          # 付属の基板アンテナ（チャネル 9 用）
# uwbcfg の 13 個: チャネル 9・プリアンブル長 64・PAC 8・TX コード 9・RX コード 9・SFD 種別 3・6810 kbps・
#                 PHR モード 0・PHR レート 0・SFD タイムアウト 65・STS モード 0・STS 長 64・PDoA モード 1
UWBCFG_CH9 = "uwbcfg 9 64 8 9 9 3 6810 0 0 65 0 64 1"
ANTENNA_CMD = f"antenna {ANTENNA}"

# 方向の符号。基板の LAoA_deg が「相手が左にいるとき＋」なら +1、「右にいるとき＋」なら −1。
# ★実測（ロードマップ 6-1 ③）で決める。決まるまでは +1（＝基板の値をそのまま）★
# bearing_deg はこれを掛けたあとの値で、常に「左が＋・右が−」（ROS の z 軸まわりの向きの決まり）。
ANGLE_SIGN = +1


# =====================================================================
#  電波法のガード ― 送ってよい命令か、送る前に必ず確かめる
# =====================================================================

class IllegalCommand(ValueError):
    """電波法（技適）の条件から外れる命令。送らずに止める。"""


def check_command(cmd: str) -> str:
    """基板へ送ってよい命令か確かめ、空白を整えた文字列を返す。だめなら IllegalCommand。

    通すもの: stop / initf / respf / diag / save / restore / stat / version / help / dcfg、
              uwbcfg（引数なし＝設定の表示、または先頭がチャネル 9）、antenna（引数なし、または js1055-9）
    止めるもの: txpower、チャネル 9 以外の uwbcfg、js1055-9 以外の antenna、AT 命令
    """
    c = " ".join(cmd.strip().split())          # 改行や余分な空白を 1 つの空白に（命令は 1 行 1 つ）
    if not c:
        raise IllegalCommand("空の命令です")
    tokens = c.lower().split(" ")
    head, args = tokens[0], tokens[1:]
    # 先頭だけでなく行のどこにあっても止める（"stop\r\ntxpower 5" のように 2 つ詰めた文字列も、ここで 1 行に潰れている）
    if any(tk == "at" or tk.startswith("at+") for tk in tokens):
        raise IllegalCommand(f"AT 命令は送りません（出荷時の試験ファーム用。電波の試験モードに入る）: {c}")
    if "txpower" in tokens:
        raise IllegalCommand("txpower は送りません（送信の強さを較正値から変えると技適の条件から外れます）")
    if head == "uwbcfg" and args and args[0] != str(CHANNEL):
        raise IllegalCommand(f"チャネル {args[0]} は使えません（日本の認証はチャネル {CHANNEL} だけ）: {c}")
    if head == "antenna" and args and args[0] != ANTENNA:
        raise IllegalCommand(f"アンテナ型 {args[0]} は使えません（{ANTENNA} だけ）: {c}")
    return c


def start_commands(role: str = "initf", configure: bool = True, diag: bool = False) -> list[str]:
    """測り始めるまでに送る命令の列。stop →（uwbcfg → antenna）→（diag 1）→ initf / respf。

    role      … "initf"＝測る側（ロボ）、"respf"＝応える側（席）
    configure … False なら uwbcfg / antenna を打たない（save で設定を保存してある基板）
    """
    if role not in ("initf", "respf"):
        raise ValueError("role は initf（測る側＝ロボ）か respf（応える側＝席）")
    cmds = ["stop"]
    if configure:
        cmds += [UWBCFG_CH9, ANTENNA_CMD]
    if diag:
        cmds.append("diag 1")
    cmds.append(role)
    return [check_command(c) for c in cmds]     # 列そのものも検査を通す（＝法規に合う列しか作れない）


# =====================================================================
#  1 行 → 数値
# =====================================================================

@dataclass
class UwbReading:
    """1 回の測定（相手 1 台ぶん）。距離は m、角度は度。測れなかった回は ok=False で数値は None。"""
    block: int                      # 何回目の測定か
    addr: str                       # 相手のアドレス（例 "0x0001"）
    ok: bool                        # Status が "Ok" か
    distance_m: float | None        # D_cm ÷ 100
    bearing_deg: float | None       # 相手の方向。左が＋（ROS の決まり）。LAoA_deg × ANGLE_SIGN
    laoa_deg: float | None          # 基板が出した LAoA_deg そのまま（符号の検算用）
    pdoa_deg: float | None          # LPDoA_deg（位相差の生の値。参考）
    fom: float | None               # LFoM（確からしさ。大きいほど良い）
    raoa_deg: float | None          # RAoA_deg（相手側が測った方向。参考）
    rssi_dbm: float | None          # diag 1 のときだけ
    nlos_pct: float | None          # diag 1 のときだけ
    t: float = 0.0                  # 読んだ時刻（time.monotonic）

    @property
    def bearing_rad(self) -> float | None:
        return None if self.bearing_deg is None else math.radians(self.bearing_deg)


_JS_REPLY = re.compile(r"^JS[0-9A-Fa-f]{4}")    # 命令の返事（JS＋16 進 4 桁の長さ＋JSON）


def line_kind(line: str) -> str:
    """行の種類。measure（測定）／reply（命令の返事）／ack（ok・error）／empty／other"""
    s = line.strip()
    if not s:
        return "empty"
    if s.startswith("{") and '"Block"' in s:
        return "measure"
    if _JS_REPLY.match(s):
        return "reply"
    if s.lower() in ("ok", "error"):
        return "ack"
    return "other"


def _num(v) -> float | None:
    """数値か、"-61.3" のような文字列の数値を float に。無ければ None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().rstrip("%"))
    except ValueError:
        return None


def parse_line(line: str, angle_sign: int = ANGLE_SIGN, t: float | None = None) -> list[UwbReading]:
    """基板の 1 行を読む。測定の行なら UwbReading のリスト（相手が 1 台なら要素 1 つ）。それ以外は []。

    ★書式のことはこの関数 1 つにまとめてある★（実物の出力が違っていたら、直すのはここだけ）
    """
    if line_kind(line) != "measure":
        return []
    try:
        obj = json.loads(line.strip())
    except json.JSONDecodeError:
        return []                               # 途中で切れた行など
    results = obj.get("results") if isinstance(obj, dict) else None
    if not isinstance(results, list):
        return []
    block = obj.get("Block")
    block = int(block) if isinstance(block, (int, float)) and not isinstance(block, bool) else -1
    now = time.monotonic() if t is None else t
    out: list[UwbReading] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        ok = str(r.get("Status", "")).strip().lower() == "ok"
        d_cm = _num(r.get("D_cm")) if ok else None
        laoa = _num(r.get("LAoA_deg")) if ok else None
        out.append(UwbReading(
            block=block,
            addr=str(r.get("Addr", "")).strip(),
            ok=ok,
            distance_m=None if d_cm is None else d_cm / 100.0,
            bearing_deg=None if laoa is None else angle_sign * laoa,
            laoa_deg=laoa,
            pdoa_deg=_num(r.get("LPDoA_deg")) if ok else None,
            fom=_num(r.get("LFoM")),
            raoa_deg=_num(r.get("RAoA_deg")) if ok else None,
            rssi_dbm=_num(r.get("RSSI_dBm")),
            nlos_pct=_num(r.get("NLOS_%")),
            t=now,
        ))
    return out


def is_usable(r: UwbReading, min_fom: float | None = None, max_nlos: float | None = None,
              max_distance_m: float | None = None) -> bool:
    """その 1 回を位置の計算に使ってよいか。しきい値は実測（6-1 ③）で決めるまで None（＝見ない）。"""
    if not r.ok or r.distance_m is None or r.bearing_deg is None or r.distance_m <= 0:
        return False
    if max_distance_m is not None and r.distance_m > max_distance_m:
        return False
    if min_fom is not None and (r.fom is None or r.fom < min_fom):
        return False
    if max_nlos is not None and r.nlos_pct is not None and r.nlos_pct > max_nlos:
        return False
    return True


CSV_FIELDS = ["time", "block", "addr", "ok", "distance_m", "bearing_deg", "laoa_deg",
              "pdoa_deg", "fom", "raoa_deg", "rssi_dbm", "nlos_pct"]


def csv_row(r: UwbReading, when: datetime | None = None) -> list:
    """CSV の 1 行（CSV_FIELDS の順）。測れなかった回は数値の欄が空。"""
    when = when or datetime.now()

    def f(v):
        return "" if v is None else v

    return [when.isoformat(timespec="milliseconds"), r.block, r.addr, int(r.ok),
            f(r.distance_m), f(r.bearing_deg), f(r.laoa_deg), f(r.pdoa_deg), f(r.fom),
            f(r.raoa_deg), f(r.rssi_dbm), f(r.nlos_pct)]


# =====================================================================
#  基板とのやり取り
# =====================================================================

def find_port(prefer: str = DEFAULT_PORT) -> str | None:
    """ポートを探す。固定名があればそれ、無ければ USB の ID（1915:520f）で探す。見つからなければ None。"""
    if prefer and prefer != "auto" and os.path.exists(prefer):
        return prefer
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for p in list_ports.comports():
        if (p.vid, p.pid) == (UWB_VID, UWB_PID):
            return p.device
    return None


def open_serial(port: str, baud: int = BAUD, timeout: float = 0.3):
    """pyserial でポートを開く（Pi: /dev/uwb、PC: COM5 など）。"""
    import serial                               # ここで import: test_logic.py は pyserial 無しで走るため
    return serial.Serial(port, baud, timeout=timeout)


class UwbReader:
    """基板 1 枚とのやり取り。命令は send() の 1 本道で、必ず check_command() を通る。

    ser … pyserial の Serial か FakeUwbBoard（readline() / write() / close() があれば何でもよい）
    log … 生の行をそのまま書くファイル（開いた状態で渡す）。None なら書かない
    """

    def __init__(self, ser, angle_sign: int = ANGLE_SIGN, log=None, verbose: bool = False):
        self.ser = ser
        self.angle_sign = angle_sign
        self.log = log
        self.verbose = verbose
        self.sent: list[str] = []               # 送った命令（検算・ログ用）
        self.last: UwbReading | None = None     # 最後に測れた 1 回
        self.n_lines = 0                        # 読んだ行数（返事や ok も含む）
        self.n_ok = 0                           # 測れた回数
        self.n_fail = 0                         # 測れなかった回数（Status が Ok 以外）

    # ---- 送る ----

    def send(self, cmd: str) -> str:
        c = check_command(cmd)                  # ★ここを通らない命令は基板へ行かない★
        self.ser.write((c + EOL).encode("ascii"))
        self.sent.append(c)
        if self.log is not None:
            self.log.write(f"# > {c}\n")        # 「打った命令も一緒に」（出力の見本の決まり）
        if self.verbose:
            print(f"    [uwb] > {c}", flush=True)
        return c

    def start(self, role: str = "initf", configure: bool = True, diag: bool = False,
              gap: float = 0.3, sleep=time.sleep) -> list[str]:
        """測り始める。命令ごとに少し待って返事（JS…／ok）を読み捨てる。送った命令の列を返す。"""
        for c in start_commands(role, configure, diag):
            self.send(c)
            sleep(gap)
            self._read_replies()
        return list(self.sent)

    def stop(self):
        self.send("stop")

    def close(self):
        try:
            self.stop()
        finally:
            self.ser.close()

    # ---- 読む ----

    def read_line(self) -> str | None:
        """1 行読む（タイムアウトなら None）。生の行はログに残す。"""
        raw = self.ser.readline()
        if not raw:
            return None
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        self.n_lines += 1
        if self.log is not None:
            self.log.write(line + "\n")
        if self.verbose and line_kind(line) != "measure":
            print(f"    [uwb] < {line}", flush=True)
        return line

    def _read_replies(self, max_lines: int = 50):
        """命令の返事（JS…／ok）を読み捨てる。測定の行が来たら（＝もう測り始めている）普通に処理して抜ける。
        ★測り始めたあとの基板は黙らない（1 秒 5 行）ので、「静かになるまで読む」作りだと測定を捨て続けてしまう★"""
        for _ in range(max_lines):
            line = self.read_line()
            if line is None:
                return
            if line_kind(line) == "measure":
                self._take(parse_line(line, self.angle_sign))
                return

    def _take(self, rs: list[UwbReading]) -> list[UwbReading]:
        for r in rs:
            if r.ok:
                self.n_ok += 1
                self.last = r
            else:
                self.n_fail += 1
        return rs

    def poll(self) -> list[UwbReading]:
        """1 行ぶん処理する。測定の行なら UwbReading のリスト、それ以外（返事・タイムアウト）は []。"""
        line = self.read_line()
        if line is None:
            return []
        return self._take(parse_line(line, self.angle_sign))

    def latest(self, max_age: float = 0.5, now: float | None = None) -> UwbReading | None:
        """最後に測れた 1 回。max_age 秒より古ければ None（止まっている値を使わないため）。"""
        if self.last is None:
            return None
        now = time.monotonic() if now is None else now
        return self.last if now - self.last.t <= max_age else None


# =====================================================================
#  基板のにせもの（--simulate と test_logic.py 用。電波は出ない）
# =====================================================================

class FakeUwbBoard:
    """pyserial の Serial と同じ readline() / write() / close() を持つ、基板のにせもの。

    相手の本当の位置を set_target(distance_m, bearing_deg) で与えると、initf のあと 1 秒 5 回、
    基板と同じ書式の行を返す。bearing_deg は「左が＋」。基板の LAoA_deg には angle_sign を掛け戻した値を
    入れるので、parse_line() を通すと元の bearing に戻る（ANGLE_SIGN の検算）。
    |bearing| > 90（相手が後ろ）のときは、本物の基板と同じく前後を見分けられず、鏡写しの角度（180−|b|）を返す。
    clock を差し替えると時間を手で進められる（テスト用）。realtime=True なら本当に 200 ms 待つ。
    """

    def __init__(self, distance_m: float = 1.5, bearing_deg: float = 20.0,
                 noise_m: float = 0.0, noise_deg: float = 0.0, drop_rate: float = 0.0,
                 seed: int = 1, clock=None, realtime: bool = False, angle_sign: int = ANGLE_SIGN):
        self.d, self.b = distance_m, bearing_deg
        self.noise_m, self.noise_deg, self.drop_rate = noise_m, noise_deg, drop_rate
        self.rng = random.Random(seed)
        self.clock = clock or time.monotonic
        self.realtime = realtime
        self.angle_sign = angle_sign
        self.running = False
        self.diag = False
        self.block = 0
        self.next_t = 0.0
        self.received: list[str] = []           # 受け取った命令
        self.outq: list[str] = []               # これから返す行
        # 相手の位置を毎回計算で決めたいとき（ロボが動くシミュレーション）: () -> (distance_m, bearing_deg) を入れる
        # （beacon_locator.simulate_reading と組み合わせると、ロボの位置に合わせた値を流せる）
        self.target_fn = None

    def set_target(self, distance_m: float, bearing_deg: float):
        self.d, self.b = distance_m, bearing_deg

    # ---- Serial と同じ顔 ----

    def write(self, data: bytes) -> int:
        text = data.decode("ascii", errors="replace")
        for cmd in text.replace("\n", "\r").split("\r"):
            cmd = cmd.strip()
            if cmd:
                self._command(cmd)
        return len(data)

    def readline(self) -> bytes:
        if self.outq:
            return (self.outq.pop(0) + "\r\n").encode()
        if not self.running:
            return b""
        now = self.clock()
        if now < self.next_t - 1e-9:            # 1e-9 は 0.2 を足し続けたときの丸め誤差ぶん
            if not self.realtime:
                return b""
            time.sleep(self.next_t - now)
        self.next_t += 1.0 / RATE_HZ            # 読むのが遅れていたら、本物の受信バッファと同じくたまった行を続けて返す
        return (self._measure_line() + "\r\n").encode()

    def close(self):
        self.running = False

    # ---- 中身 ----

    def _command(self, cmd: str):
        self.received.append(cmd)
        head, *args = cmd.lower().split()
        if head == "txpower":                   # 本物なら送信の強さが変わってしまう。ここに届いたら作りが壊れている
            raise AssertionError("★txpower が基板に届きました。check_command() を通っていない送り方があります★")
        if head == "stop":
            self.running = False
            self.outq.append("ok")
        elif head == "uwbcfg":
            self.outq += ['JS0040{"UWB PARAM":{"CHAN":9,"PRF":64,"DATARATE":6810,"PDOAMODE":1}}', "ok"]
        elif head == "antenna":
            self.outq += ["CURRENT ANTENNA_TYPE:JS1055-9", "ok"]
        elif head == "diag":
            self.diag = bool(args and args[0] == "1")
            self.outq.append("ok")
        elif head in ("initf", "respf"):
            self.running = True
            self.next_t = self.clock() + 1.0 / RATE_HZ      # 最初の測定は 200 ms 後
            self.outq += ['JS0030{"F PARAMS":{"SLOT, rstu":2400,"BLOCK, ms":200}}', "ok"]
        else:
            self.outq.append("ok")

    def _measure_line(self) -> str:
        self.block += 1
        if self.target_fn is not None:
            self.d, self.b = self.target_fn()
        addr = "0x0001"
        if self.rng.random() < self.drop_rate:
            r = {"Addr": addr, "Status": "Err", "D_cm": 0, "LPDoA_deg": 0.0, "LAoA_deg": 0.0,
                 "LFoM": 0, "RAoA_deg": 0.0, "CFO_100ppm": 0}
            return json.dumps({"Block": self.block, "results": [r]}, separators=(",", ":"))
        d_cm = int(round((self.d + self.rng.gauss(0, self.noise_m)) * 100))
        b = ((self.b + self.rng.gauss(0, self.noise_deg) + 180) % 360) - 180     # −180〜180 に
        if b > 90:                              # 後ろの相手は前に鏡写しで見える（前後の見分けがつかない）
            b = 180 - b
        elif b < -90:
            b = -180 - b
        laoa = round(self.angle_sign * b, 2)
        r = {"Addr": addr, "Status": "Ok", "D_cm": d_cm, "LPDoA_deg": round(laoa * 2.4, 2),
             "LAoA_deg": laoa, "LFoM": 86, "RAoA_deg": round(-laoa * 0.8, 2), "CFO_100ppm": 415}
        if self.diag:
            r["RSSI_dBm"] = f"{-60.0 - 6.0 * self.d:.1f}"
            r["NLOS_%"] = 0
        return json.dumps({"Block": self.block, "results": [r]}, separators=(",", ":"))


# =====================================================================
#  コマンドラインから
# =====================================================================

LEGAL_NOTICE = (
    "★電波を出します（UWB チャネル 9 固定・送信の強さは較正値のまま）。\n"
    "  技適の表示は村田データシート 4.1 とモジュール包装のラベル（R 007-AM0109）。認証の条件のまま使います★"
)


def _fmt(r: UwbReading) -> str:
    if not r.ok:
        return f"#{r.block:<5d} {r.addr:7s}  ×  （Status が Ok ではない）"
    s = f"#{r.block:<5d} {r.addr:7s}  {r.distance_m:5.2f} m  方向 {r.bearing_deg:+6.1f}°"
    if r.fom is not None:
        s += f"  FoM {r.fom:.0f}"
    if r.rssi_dbm is not None:
        s += f"  RSSI {r.rssi_dbm:.1f} dBm"
    if r.nlos_pct is not None:
        s += f"  NLOS {r.nlos_pct:.0f}%"
    return s


def main():
    ap = argparse.ArgumentParser(description="UWB 基板（村田 Type 2AB・デモ用 CLI ファーム）から距離と方向を読む")
    ap.add_argument("--port", default=DEFAULT_PORT,
                    help=f"シリアルポート（既定 {DEFAULT_PORT}。無ければ USB の ID で探す。PC なら COM5 など）")
    ap.add_argument("--simulate", action="store_true", help="基板なし。偽の基板の値を流す（電波は出ない）")
    ap.add_argument("--sim-distance", type=float, default=1.5, help="--simulate の相手の距離 [m]")
    ap.add_argument("--sim-bearing", type=float, default=20.0, help="--simulate の相手の方向 [度]（左が＋）")
    ap.add_argument("--role", choices=["initf", "respf"], default="initf",
                    help="initf＝測る側（ロボ。既定）／respf＝応える側（席）")
    ap.add_argument("--no-config", action="store_true", help="uwbcfg / antenna を打たない（save 済みの基板）")
    ap.add_argument("--diag", action="store_true", help="diag 1 を先に打って RSSI と NLOS も読む")
    ap.add_argument("--log", help="生の行をそのまま保存するファイル（「出力の見本」用）")
    ap.add_argument("--csv", help="読み取った値を CSV に保存するファイル")
    ap.add_argument("--seconds", type=float, default=0.0, help="この秒数で止める（0＝Ctrl-C まで）")
    ap.add_argument("--angle-sign", type=int, choices=[1, -1], default=ANGLE_SIGN,
                    help=f"LAoA_deg の符号（実測で決める。既定 {ANGLE_SIGN:+d}）")
    ap.add_argument("--quiet", action="store_true", help="1 行ごとの表示を出さない（終わりの集計だけ）")
    args = ap.parse_args()

    if args.simulate:
        ser = FakeUwbBoard(args.sim_distance, args.sim_bearing, noise_m=0.03, noise_deg=2.0,
                           drop_rate=0.05, realtime=True, angle_sign=args.angle_sign)
        port = "simulate"
        print(f"モード : シミュレータ（電波は出ません）相手 = {args.sim_distance:.2f} m・{args.sim_bearing:+.0f}°")
    else:
        port = find_port(args.port)
        if port is None:
            sys.exit(f"UWB 基板が見つかりません（{args.port}）。\n"
                     "  ・基板の J6（USB）を Pi/PC につないでいるか。電源のジャンパは J6 側か\n"
                     "  ・Pi なら udev の固定名 /dev/uwb（robot/udev/99-uwb.rules → pi_setup.sh）か ls /dev/ttyACM*\n"
                     "  ・PC なら --port COM5 のように指定（デバイスマネージャーの USB Serial Device）")
        try:
            ser = open_serial(port)
        except ImportError:
            sys.exit("pyserial がありません。Pi: sudo apt install python3-serial ／ PC: pip install pyserial")
        except Exception as e:                  # serial.SerialException など
            sys.exit(f"{port} を開けません: {e}")
        print(f"ポート : {port}  {BAUD} bps")
        print(LEGAL_NOTICE)

    log_f = open(args.log, "a", encoding="utf-8") if args.log else None
    csv_f = open(args.csv, "a", newline="", encoding="utf-8") if args.csv else None
    wr = None
    if csv_f is not None:
        wr = csv.writer(csv_f)
        if csv_f.tell() == 0:
            wr.writerow(CSV_FIELDS)
    if log_f is not None:
        log_f.write(f"# {datetime.now().isoformat(timespec='seconds')}  port={port}"
                    f"  role={args.role}  angle_sign={args.angle_sign:+d}\n")

    reader = UwbReader(ser, angle_sign=args.angle_sign, log=log_f, verbose=not args.quiet)
    t0 = time.monotonic()
    try:
        reader.start(args.role, configure=not args.no_config, diag=args.diag)
        t_meas = time.monotonic()               # --seconds は測り始めてからの秒数
        print(f"測り始めました（{args.role}）。Ctrl-C で止めます。")
        while True:
            for r in reader.poll():
                if not args.quiet:
                    print(_fmt(r), flush=True)
                if wr is not None:
                    wr.writerow(csv_row(r))
            if args.seconds and time.monotonic() - t_meas >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            reader.close()
        except Exception as e:
            print(f"    [warn] stop を送れませんでした: {e}")
        for f in (log_f, csv_f):
            if f is not None:
                f.close()
        n = reader.n_ok + reader.n_fail
        rate = (100.0 * reader.n_ok / n) if n else 0.0
        print(f"\n集計: 測定 {n} 回（測れた {reader.n_ok}・測れなかった {reader.n_fail}・成功率 {rate:.0f}%）"
              f"  読んだ行 {reader.n_lines}  経過 {time.monotonic() - t0:.0f} 秒")
        if reader.last is not None:
            print(f"最後の値: {_fmt(reader.last)}")
        if args.log:
            print(f"生の行 → {args.log}")
        if args.csv:
            print(f"CSV    → {args.csv}")


if __name__ == "__main__":
    main()
