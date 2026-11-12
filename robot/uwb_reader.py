"""
uwb_reader.py ― UWB 基板から「距離と方向」を読む  ★S1（UWB ビーコン拡張）★

席に置いた UWB ユニット（村田 Type 2AB 評価基板＝応える側）と、ロボに載せた同じ基板（測る側）が
電波で測り合い、測る側の基板が USB シリアルに 1 秒 5 回、1 行 1 JSON で結果を流します。
このファイルは、その「文字の行」を Python の「数値」に変える耳の役です。

    UWB 基板（J6 の USB）──/dev/uwb──> 【ここ】──UwbReading（距離 m・方向 deg）──> beacon_locator.py（S2）→ ブリッジ（S3）

────────────────────────────────────────────────────────────
基板が流す 1 行（デモ用 CLI ファームの書式。1 行＝1 回の測定）
────────────────────────────────────────────────────────────

  {"Block":12,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":152,"LPDoA_deg":47.5,"LAoA_deg":19.8,"LFoM":200,"RAoA_deg":-15.8,"CFO_100ppm":120}]}
  （上の数値は説明用の作り物）

    Block     … 何回目の測定か（1 ずつ増える）
    Addr      … 相手（席のユニット）のアドレス
    Status    … "Ok" ならその回は測れた。それ以外の回の数値は使わない
    D_cm      … 距離 [cm]                 → distance_m（m に直す）
    LAoA_deg  … 相手の方向 [度]（−90〜+90） → bearing_deg  ★方向として使うのはこれ★
    LPDoA_deg … 2 本のアンテナの位相差の生の値 [度]（参考）
    LFoM      … 自分側の確からしさ（大きいほど良い）
    RAoA_deg  … 相手側が測った方向（参考）
    先に `diag 1` を打つと "RSSI_dBm"（受信の強さ）と "NLOS_%"（見通し無しの確率）も付く

  ・`JS00BE{...` のように JS＋16 進 4 桁で始まる行は、命令への返事（設定の表示）。何行かに分かれて届き、最後に `ok`。
    測定ではないので数えない（測り始める前に、ここからチャネルとアンテナ型を読んで確かめる）
  ・打った命令の文字は基板がそのまま返す（エコー）。測定中に打つと、測定の行の頭に混ざる
  ・`ok` / `error` / 空行 は捨てる
  ・方向の符号（左が＋か右が＋か）は実測で決める → ANGLE_SIGN。bearing_deg は常に「左が＋」（ROS の決まり）に揃える

────────────────────────────────────────────────────────────
★電波法（技適）の約束 ― このファイルはそれをコードで固定している★
────────────────────────────────────────────────────────────
  このモジュール（LBUA5QJ2AB）の日本の認証は UWB チャネル 9（7.587〜8.4 GHz）だけです。
    ・送れる命令は「許可した形」だけ（check_command() の許可リスト。一覧に無い命令は、理由を付けて送らずに止める）
    ・チャネルは 9 に固定。`uwbcfg` は既定の 13 個の値そのもの（UWBCFG_CH9）か、引数なし（表示）だけ
    ・送信の強さ（`txpower`）は絶対に送らない（較正値から変えると認証の条件から外れる）
    ・アンテナ型は `js1055-9`（付属の基板アンテナ）だけ。ほかの型は送らない
    ・試験用の連続送信（`tcwm`・`tcfm`）と、出荷時の試験ファーム用の AT 命令（AT+…）は送らない
    ・`initf`／`respf` に引数は付けない（測距の間隔などを既定から変えない）
    ・測り始める前に、基板の返事からチャネル 9・JS1055-9（と uwbcfg の 13 個の値）を読んで確かめる。
      確かめられなければ電波を出さない（ConfigError）
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
  python3 uwb_reader.py --sign-check         # ★方向の符号（左が＋か）を確かめる★ 相手を正面 → 左へ動かして、どちらに変わるかを見る
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
# ★符号が逆だと、ロボは必ずユニットの反対側へ走る★（前後の取り違えと見分けがつかず、ソフトでは気づけない）。
#   ロボに付けるのと同じ向きに基板を立てて、`python3 uwb_reader.py --sign-check` で確かめること。
ANGLE_SIGN = +1


# =====================================================================
#  電波法のガード ― 送ってよい命令か、送る前に必ず確かめる
# =====================================================================

class IllegalCommand(ValueError):
    """電波法（技適）の条件から外れる命令、または許可した一覧に無い命令。送らずに止める。"""


class ConfigError(RuntimeError):
    """基板の設定がチャネル 9・js1055-9 だと確かめられなかった。電波を出す命令（initf / respf）は送らない。"""


# ★送ってよい命令は、ここにある形だけ（許可リスト）★ 空白を 1 つに整えて小文字にした文字列で比べる。
#   ・このプログラムが実際に使う命令だけを載せる（save・restore などは、人が Tera Term で打つ）
#   ・電波の出し方を変えられる命令（uwbcfg・antenna）は、認証の条件どおりの 1 通りと「引数なし＝表示」だけ
#   ・initf / respf は引数なしだけ（引数で測距の間隔などを変えられるため）
#   ・一覧に無いものは、害の無さそうな命令でも送らない（ファームには試験用の連続送信の命令もある）
ALLOWED_COMMANDS = frozenset({
    "stop",                     # 測距を止める（電波を止める）
    "initf", "respf",           # 測り始める（測る側／応える側）。引数なし
    "diag 0", "diag 1",         # 各行に受信の強さ・見通し無しの確率を足す／足さない
    "uwbcfg", "antenna",        # 引数なし＝いまの設定を表示するだけ（測り始める前の確認に使う）
    UWBCFG_CH9, ANTENNA_CMD,    # 認証の条件どおりの設定（チャネル 9・js1055-9）
})


def check_command(cmd: str) -> str:
    """基板へ送ってよい命令か確かめ、整えた文字列（空白 1 つ・小文字）を返す。だめなら IllegalCommand。

    通すのは ALLOWED_COMMANDS にある形だけ。よくある「だめな命令」には、なぜだめかを付けて止める。
    """
    if not isinstance(cmd, str):
        raise IllegalCommand(f"命令は 1 行の文字で渡してください: {cmd!r}")
    c = " ".join(cmd.strip().split()).lower()  # 改行や余分な空白を 1 つの空白に（命令は 1 行 1 つ）
    if not c:
        raise IllegalCommand("空の命令です")
    if c in ALLOWED_COMMANDS:
        return c
    tokens = c.split(" ")
    head, args = tokens[0], tokens[1:]
    # 先頭だけでなく行のどこにあっても止める（"stop\r\ntxpower 5" のように 2 つ詰めた文字列も、ここで 1 行に潰れている）
    if any(tk == "at" or tk.startswith("at+") for tk in tokens):
        raise IllegalCommand(f"AT 命令は送りません（出荷時の試験ファーム用。電波の試験モードに入る）: {c}")
    if "txpower" in tokens:
        raise IllegalCommand("txpower は送りません（送信の強さを較正値から変えると技適の条件から外れます）")
    if any(tk in ("tcwm", "tcfm") for tk in tokens):
        raise IllegalCommand(f"試験用の連続送信（tcwm／tcfm）は送りません（認証された使い方ではありません）: {c}")
    if head == "uwbcfg":
        if args and args[0] != str(CHANNEL):
            raise IllegalCommand(f"チャネル {args[0]} は使えません（日本の認証はチャネル {CHANNEL} だけ）: {c}")
        raise IllegalCommand(f"uwbcfg は既定の値（{UWBCFG_CH9}）か、引数なし（表示）だけ送ります: {c}")
    if head == "antenna":
        raise IllegalCommand(f"アンテナ型は {ANTENNA} だけです（ほかの型は認証の条件から外れます）: {c}")
    if head in ("initf", "respf"):
        raise IllegalCommand(f"{head} に引数は付けません（測距の間隔などを既定から変えないため）: {c}")
    raise IllegalCommand(f"許可した命令の一覧にありません（送りません）: {c}")


_RE_CHAN = re.compile(r'"CHAN"\s*:\s*(-?\d+)')
_RE_ANTENNA = re.compile(r"CURRENT ANTENNA_TYPE\s*:\s*([A-Za-z0-9_\-]+)")
_RE_FIELD_INT = re.compile(r'"[^"]*"\s*:\s*(-?\d+)\s*(?=[,}]|$)')
UWBCFG_NUMBERS = [int(x) for x in UWBCFG_CH9.split()[1:]]     # 既定の 13 個（基板の返事と 1 つずつ比べる）


def parse_config_reply(lines) -> tuple[int | None, str | None]:
    """命令の返事（何行かの文字）から、チャネルとアンテナ型を読む。書いていなければ None。
    何度も出てくれば、最後（＝いちばん新しい返事）の値。

    基板の返事の形（実物で確認）:
        JS00BE{"UWB PARAM":{          ← uwbcfg（引数あり・なしとも）の返事。何行かに分かれ、設定したあとの値が並ぶ
        "CHAN":9,
        …}}
        ok
        CURRENT ANTENNA_TYPE:JS1055-9  ← antenna（引数あり・なしとも）の返事
        ok
    """
    text = "\n".join(lines)
    m, a = _RE_CHAN.findall(text), _RE_ANTENNA.findall(text)
    return (int(m[-1]) if m else None, a[-1].lower() if a else None)


def uwbcfg_numbers(lines) -> list[int] | None:
    """uwbcfg の返事（`{"UWB PARAM":{` から `}}` まで）に並ぶ数値を、出てきた順に。返事が無ければ None。
    返事が何度かあれば最後の 1 つ。並びは uwbcfg の引数と同じ（チャネルが先頭）なので、UWBCFG_NUMBERS と比べられる。"""
    out = cur = None
    for line in lines:
        if '"UWB PARAM"' in line:
            cur = []
            rest = line.split('"UWB PARAM"', 1)[1]
        elif cur is None:
            continue
        else:
            rest = line
        cur += [int(x) for x in _RE_FIELD_INT.findall(rest)]
        if "}}" in rest:
            out, cur = cur, None
    return out


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


def _measure_part(line: str) -> str | None:
    """測定の行なら、その JSON の部分（'{"Block"' から後ろ）。違えば None。

    ★測定中に命令を打つと、基板は打った文字をそのまま返すので、測定の行の頭に混ざる★
    （実物で確認: 測定中に stop と打つと 's{"Block":…' 't{"Block":…' のような行になる）。
    頭の余計な文字は捨てて、JSON の部分だけを読む。
    """
    s = line.strip()
    i = s.find('{"Block"')
    return s[i:] if i >= 0 else None


def line_kind(line: str) -> str:
    """行の種類。measure（測定）／reply（命令の返事）／ack（ok・error）／empty／other"""
    s = line.strip()
    if not s:
        return "empty"
    if _measure_part(s) is not None:
        return "measure"
    if _JS_REPLY.match(s):
        return "reply"
    if s.lower() == "ok" or s.lower().startswith("error"):   # 失敗は "error" か "error  理由" の形で返る
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
    part = _measure_part(line)
    if part is None:
        return []
    try:
        obj = json.loads(part)
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
        self.config: tuple[int, str] | None = None   # start() で確かめた（チャネル, アンテナ型）
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
              gap: float = 0.3, sleep=time.sleep, verify: bool = True) -> list[str]:
        """測り始める。送った命令の列を返す。

        stop →（止まりきるのを待つ）→（uwbcfg 9 … → antenna js1055-9）→（diag 1）→ ★設定の確認★ → initf / respf。
        verify=True（既定）なら、基板の返事からチャネル・アンテナ型・uwbcfg の 13 個の値を読み、
        チャネル 9・js1055-9・既定の値でなければ ConfigError にして initf / respf を送らない
        （＝確かめられないときは電波を出さない）。
        """
        cmds = start_commands(role, configure, diag)
        self.config = None
        self.flush_input()                      # 前の回の読み残しを、今回の返事と取り違えない
        seen: list[str] = []                    # 今回の返事の行（確認に使う）
        self.send(cmds[0])                      # stop
        sleep(gap)
        seen += self._read_reply()
        # ★stop の ok は「止め始めた」合図で、測距が止まりきるのは少しあと（実物で確認: ok → 測定 1 行 → 止まった知らせ）★
        # 設定の命令は止まっている間しか通らないので、静かになるまで読む
        seen += self._settle()
        for c in cmds[1:-1]:                    # 最後の initf / respf の手前まで
            self.send(c)
            sleep(gap)
            seen += self._read_reply()
        if verify:
            self.config = self._verify_config(seen, gap, sleep)
            if self.verbose:
                print(f"    [uwb] 設定を確かめました: チャネル {self.config[0]}・アンテナ型 {self.config[1]}"
                      f" → {cmds[-1]} を送ります", flush=True)
        self.last = None                        # 前の回の測定を「いまの値」として使わない
        self.send(cmds[-1])
        sleep(gap)
        self._read_replies()
        return list(self.sent)

    def _verify_config(self, seen: list[str], gap: float, sleep) -> tuple[int, str]:
        """基板の返事から設定を確かめる。合っていれば（チャネル, アンテナ型）。だめなら ConfigError（電波は出さない）。"""
        chan, ant = parse_config_reply(seen)
        for _ in range(2):                      # 返事から読めていない分は、表示だけの命令で聞く（--no-config のときなど）
            if chan is not None and ant is not None:
                break
            sleep(max(gap, 0.5))
            if chan is None:
                self.send("uwbcfg")
                sleep(gap)
                seen += self._read_reply()
            if ant is None:
                self.send("antenna")
                sleep(gap)
                seen += self._read_reply()
            chan, ant = parse_config_reply(seen)
        if chan is None or ant is None:
            raise ConfigError("基板からチャネルとアンテナ型の返事を読めませんでした。電波は出していません"
                              "（デモ用の CLI ファームか・J6 につないでいるか・ほかの端末が同じポートを開いていないか）。"
                              f"読めた行: {seen[-12:]!r}")
        if chan != CHANNEL or ant != ANTENNA:
            raise ConfigError(f"基板の設定がチャネル {chan}・アンテナ型 {ant} です（使えるのはチャネル {CHANNEL}・{ANTENNA} だけ）。"
                              "電波は出していません。Tera Term で restore を打って出荷時の設定に戻してください")
        nums = uwbcfg_numbers(seen)
        if nums is not None and len(nums) == len(UWBCFG_NUMBERS) and nums != UWBCFG_NUMBERS:
            raise ConfigError(f"基板の uwbcfg の値が既定と違います（基板 {nums}／既定 {UWBCFG_NUMBERS}）。"
                              "電波は出していません。Tera Term で restore を打って出荷時の設定に戻してください")
        return (chan, ant)

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

    def flush_input(self) -> None:
        """受信バッファにたまった行を捨てる。★ロボが回った・動いたあとは、測る前に必ず呼ぶ★
        基板は 1 秒に 5 行出し続ける。回っている間にたまった行は「回る前・回っている途中」の測定なので、
        あとから読んで「いまの位置での測定」として使うと、ユニットの位置がずれる。"""
        f = getattr(self.ser, "reset_input_buffer", None)
        if callable(f):
            try:
                f()
            except Exception:                   # 捨てられなくても、読み取りは続ける
                pass

    def _read_reply(self, max_empty: int = 5, max_lines: int = 80) -> list[str]:
        """命令の返事を、行 ok / error が来るまで読んで返す（測定の行は数えて捨てる）。
        返事は何行かに分かれて届く。来なければ max_empty 回の読み取り待ち（1 回 0.3 秒）であきらめる。"""
        lines: list[str] = []
        empty = 0
        while empty < max_empty and len(lines) < max_lines:
            line = self.read_line()
            if line is None:
                empty += 1
                continue
            kind = line_kind(line)
            if kind == "measure":               # stop が効く前の測定の行（前の回の残り）。数えない
                continue
            lines.append(line)
            if kind == "ack":
                break
        return lines

    def _settle(self, quiet: int = 2, max_lines: int = 40) -> list[str]:
        """基板が静かになるまで読む（読み取り待ちが quiet 回続けて空になるまで。1 回 0.3 秒）。測定の行は数えない。"""
        lines: list[str] = []
        empty = 0
        while empty < quiet and len(lines) < max_lines:
            line = self.read_line()
            if line is None:
                empty += 1
                continue
            empty = 0
            if line_kind(line) != "measure":
                lines.append(line)
        return lines

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

    本物に合わせてあること:
      ・打った命令はそのまま返す（エコー）。設定の返事は何行かに分かれ、最後に ok
      ・読むのが遅れると行がたまる。★たまった行は「たまり始めたときの相手の位置」の測定★（ロボが回っている間の行は
        回る前の値）。reset_input_buffer() でたまった行を捨てられる（pyserial と同じ名前）
      ・stop の返事は「ok → 測定がもう 1 行 → 止まった知らせ」の順（止まりきるのは ok の少しあと）
      ・cfg（uwbcfg の 13 個。先頭がチャネル）/ antenna_type を書き換えると「別の設定で保存されていた基板」を
        真似できる（lock_config=True なら uwbcfg / antenna の設定を受け付けない）
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
        self.cfg = list(UWBCFG_NUMBERS)         # いまの uwbcfg の 13 個（先頭がチャネル。uwbcfg で変わる）
        self.antenna_type = ANTENNA.upper()     # いまのアンテナ型（antenna で変わる）
        self.lock_config = False                # True なら uwbcfg / antenna の設定を受け付けない（表示はする）
        self.echo = True                        # 打った命令をそのまま返す
        self.n_flushed = 0                      # reset_input_buffer で捨てた測定の行の数
        # テスト用: wait(sec) を入れると、本物のポートと同じく「次の行が来るまで（最大 timeout 秒）待つ」。
        # 待つかわりに wait を呼ぶ（テストの時計を進める関数を渡す）。None なら待たずに空を返す
        self.wait = None
        self.timeout = 0.3                      # 読み取りの待ち時間（本物の open_serial と同じ）

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
            if self.wait is not None:
                self.wait(self.timeout)         # 何も来ない → 待ち時間いっぱい待って空を返す
            return b""
        now = self.clock()
        if now < self.next_t - 1e-9:            # 1e-9 は 0.2 を足し続けたときの丸め誤差ぶん
            if self.realtime:
                time.sleep(self.next_t - now)
            elif self.wait is not None and self.next_t - now <= self.timeout + 1e-9:
                self.wait(self.next_t - now)    # 次の行が来るまで待つ（テストの時計が進む）
                now = self.clock()
            else:
                return b""
        # 読むのが遅れていたら、本物の受信バッファと同じくたまった行を続けて返す。
        # 1 周期より前に出たはずの行は「古い行」: 相手の位置は、最後に読まれたときのまま（いまの位置で計算し直さない）
        stale = (now - self.next_t) >= (1.0 / RATE_HZ) - 1e-9
        self.next_t += 1.0 / RATE_HZ
        return (self._measure_line(stale=stale) + "\r\n").encode()

    @property
    def chan(self) -> int:
        return self.cfg[0]

    def reset_input_buffer(self):
        """たまった行を捨てる（pyserial の Serial と同じ名前）。返事の残りも捨てる。"""
        self.outq.clear()
        if self.running:
            now = self.clock()
            while self.next_t <= now + 1e-9:
                self.next_t += 1.0 / RATE_HZ
                self.n_flushed += 1

    def close(self):
        self.running = False

    # ---- 中身 ----

    def _command(self, cmd: str):
        self.received.append(cmd)
        head, *args = cmd.lower().split()
        if head == "txpower":                   # 本物なら送信の強さが変わってしまう。ここに届いたら作りが壊れている
            raise AssertionError("★txpower が基板に届きました。check_command() を通っていない送り方があります★")
        was_running = self.running
        if self.echo and not was_running:
            self.outq.append(cmd)               # 本物は打った文字をそのまま返す
        if head == "stop":
            self.running = False
            if was_running:                     # 測距中: 打った文字は測定の行の頭に混ざり、ok のあとにもう 1 行出てから止まる
                self.outq += [(cmd if self.echo else "") + self._measure_line(stale=True), "ok",
                              self._measure_line(stale=True), '{"Info":"session stopped"}']
            else:
                self.outq.append("ok")
        elif was_running and head in ("uwbcfg", "antenna", "diag", "initf", "respf"):
            self.outq.append("error  busy")    # 設定の命令は止まっている間しか通らない
        elif head == "uwbcfg":
            if args and len(args) != len(self.cfg):
                self.outq.append("error")
                return
            if args and not self.lock_config:
                self.cfg = [int(a) for a in args]
            # 返事は何行かに分かれる。項目の名前は先頭（チャネル）以外は仮の名前（並びは uwbcfg の引数と同じ）
            body = [f'"CHAN":{self.cfg[0]},'] + [f'"P{k:02d}":{v},' for k, v in enumerate(self.cfg[1:-1], 2)]
            self.outq += ['JS00BE{"UWB PARAM":{'] + body + [f'"P{len(self.cfg):02d}":{self.cfg[-1]}}}}}', "ok"]
        elif head == "antenna":
            if args and not self.lock_config:
                self.antenna_type = args[0].upper()
            self.outq += ["", f"CURRENT ANTENNA_TYPE:{self.antenna_type}", "ok"]
        elif head == "diag":
            self.diag = bool(args and args[0] == "1")
            self.outq.append("ok")
        elif head in ("initf", "respf"):
            self.running = True
            self.next_t = self.clock() + 1.0 / RATE_HZ      # 最初の測定は 200 ms 後
            self.outq += ['JS0010{"F PARAMS":{', "}}", "ok"]
        else:
            self.outq.append("ok")

    def _measure_line(self, stale: bool = False) -> str:
        self.block += 1
        if self.target_fn is not None and not stale:
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
             "LAoA_deg": laoa, "LFoM": 200, "RAoA_deg": round(-laoa * 0.8, 2), "CFO_100ppm": 120}
        if self.diag:
            r["RSSI_dBm"] = f"{-60.0 - 6.0 * self.d:.1f}"
            r["NLOS_%"] = 0
        return json.dumps({"Block": self.block, "results": [r]}, separators=(",", ":"))


# =====================================================================
#  方向の符号を確かめる（--sign-check）
# =====================================================================

def sign_check_verdict(laoa_center: float | None, laoa_left: float | None,
                       min_change: float = 8.0) -> tuple[int | None, str]:
    """相手を「アンテナから見て左」へ動かしたときの LAoA_deg の変わり方から、符号を決める。

    laoa_center … 相手を正面に置いたときの LAoA_deg の中央値（基板の値そのまま）
    laoa_left   … 左へ動かしたあとの中央値
    戻り値 (ANGLE_SIGN にする値, 説明)。変化が小さい・読めていないときは (None, 説明)。
    """
    if laoa_center is None or laoa_left is None:
        return None, "測定が足りません（相手の電源・向き・距離を確かめて、やり直してください）"
    d = laoa_left - laoa_center
    if abs(d) < min_change:
        return None, (f"方向の変化が小さすぎます（{d:+.1f}°）。相手を、もっと大きく（1 m 先なら 50 cm 以上）左へ動かして、"
                      "やり直してください")
    if d > 0:
        return +1, (f"左へ動かすと LAoA_deg が増えました（{d:+.1f}°）→ 符号は +1（左が＋）。"
                    "--angle-sign／--uwb-angle-sign は +1（付けなければ uwb_reader.ANGLE_SIGN の値）")
    return -1, (f"左へ動かすと LAoA_deg が減りました（{d:+.1f}°）→ 符号は −1（右が＋）。"
                "★uwb_reader.py には --angle-sign -1、ブリッジには --uwb-angle-sign -1 を付けてください★")


def median_laoa(reader: "UwbReader", seconds: float = 3.0, clock=time.monotonic) -> tuple[float | None, int]:
    """seconds 秒のあいだ読んだ LAoA_deg（基板の値そのまま）の中央値と、読めた回数。3 回未満なら (None, 回数)。"""
    reader.flush_input()                        # 相手を動かしている間にたまった行は捨てる
    t0 = clock()
    vals: list[float] = []
    while clock() - t0 < seconds:
        for r in reader.poll():
            if r.ok and r.laoa_deg is not None:
                vals.append(r.laoa_deg)
    if len(vals) < 3:
        return None, len(vals)
    vals.sort()
    return vals[len(vals) // 2], len(vals)


SIGN_CHECK_HOWTO = """
★方向の符号の確認★ ロボに付けるのと同じ向きに、測る側の基板（この基板）を立ててください。
  「アンテナから見て左」とは …… この基板の後ろに立ち、アンテナの面が向いているのと同じ方向を見たときの、自分の左手側。
    （ロボに後ろ向きに付けた状態なら、ロボの後ろへ顔を向けたときの左手側 ＝ ロボの右側）
  相手（応える側の基板）は、アンテナの面の正面 約 1 m に、同じ高さで置きます。
"""


def sign_check(reader: "UwbReader", ask=input, seconds: float = 3.0, clock=time.monotonic,
               on_moved=None) -> int | None:
    """相手を正面 → 左へ動かしてもらい、符号を決めて知らせる。決まれば +1 / −1、決まらなければ None。
    on_moved … ②の前に呼ぶ関数（シミュレータで相手を左へ動かすのに使う）。"""
    print(SIGN_CHECK_HOWTO)
    ask("  ① 相手をアンテナの正面（約 1 m）に置いたら Enter: ")
    c, nc = median_laoa(reader, seconds, clock)
    print("     正面: " + ("読めません" if c is None else f"LAoA_deg の中央値 {c:+.1f}°") + f"（{nc} 回）")
    ask("  ② 相手を「アンテナから見て左」へ 50 cm ほど動かしたら Enter: ")
    if on_moved is not None:
        on_moved()
    lf, nl = median_laoa(reader, seconds, clock)
    print("     左  : " + ("読めません" if lf is None else f"LAoA_deg の中央値 {lf:+.1f}°") + f"（{nl} 回）")
    sign, text = sign_check_verdict(c, lf)
    print("\n  → " + text)
    return sign


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
    ap.add_argument("--sign-check", action="store_true",
                    help="方向の符号（左が＋か）を確かめる。相手を正面 → 左へ動かして、LAoA_deg がどちらに変わるかを見る")
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
        try:
            reader.start(args.role, configure=not args.no_config, diag=args.diag)
        except ConfigError as e:
            print(f"\n★測り始めませんでした★ {e}")
            return
        t_meas = time.monotonic()               # --seconds は測り始めてからの秒数
        if args.quiet:                          # --quiet でなければ、確かめた時点（initf の前）に同じ知らせを出している
            print(f"設定を確かめました: チャネル {reader.config[0]}・アンテナ型 {reader.config[1]}")
        if args.sign_check:
            reader.verbose = False
            moved = None
            if args.simulate:                   # シミュレータ: ②で相手を左へ 27° 動かしたことにする
                moved = lambda: ser.set_target(ser.d, ser.b + 27.0)   # noqa: E731
            sign_check(reader, on_moved=moved)
            return
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
