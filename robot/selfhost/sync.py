#!/usr/bin/env python3
"""
sync.py ― クラウド（Supabase）と Pi のセルフホスト DB のあいだで、本・貸出・ピンを揃える

ふだんは mode.sh が呼ぶので、直接打つのは中身を確かめたいときだけ。

  python3 sync.py sync --from cloud --to self             # クラウド → Pi（mode.sh self が呼ぶ）
  python3 sync.py sync --from self --to cloud             # Pi → クラウド（mode.sh cloud が呼ぶ）
  python3 sync.py sync --from cloud --to self --dry-run   # 何が変わるかを見るだけ（書き込まない）
  python3 sync.py calls --side self                       # 呼出の残り（queued / moving / arrived）を見る
  python3 sync.py calls --side self --cancel-queued       # queued の呼出を取り消す
  python3 sync.py check --side cloud                      # つながるか（終了コード 0 = つながる）

何が揃うか（README の「同期されるもの・されないもの」と同じ）
  本（books）          … 両方向に「足りない本を足す」。同じ本の題名などが違えば --from 側に合わせる
  貸出（loans）        … 両方向に「足りない記録を足す」。片方で返却済みなら、もう片方も返却済みにする
                          （返却を取り消す方向には動かさない）
  本の状態（status）   … 貸出の記録が増えた・返却が入った側で、記録から決め直す（未返却の貸出がある＝貸出中）。
                          写しを入れたばかりの本は写し元の状態のまま。貸出が動いていない本は --from 側に合わせる
  ピン（stop_points）  … --from → --to の一方向（ピンは Pi の地図に合わせたもの。直前まで使っていた側が正）
  本の削除             … 前回そろえたときに両方にあった本（.sync_state.json に控える）が片方にだけ無ければ、
                          その側で消されたと見て、もう片方でも消す（管理者画面の「本の削除」に対応。2026-09-26）。
                          貸出・呼出の記録が残っている本は消さずに足し戻す。消える本が多すぎるとき
                          （DB を作り直した直後など）も消さずに足し戻す（--max-delete で上限を変えられる）
  揃えないもの         … 呼出（robot_calls）・ロボの状態（robot_status）・手動操作（robot_manual）
                          ＝その場かぎりの状態。持ち込むと、古い呼出でロボが動き出す危険がある
  何度実行しても安全（2 回目は「変更なし」）。途中で切れても、もう一度実行すれば続きから揃う。

接続先は robot/.env.cloud と robot/.env.self（setup.sh が作る）。robot/.env（いまのモード）は見ない。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROBOT_DIR = os.path.dirname(HERE)
ENV_FILES = {"cloud": os.path.join(ROBOT_DIR, ".env.cloud"),
             "self": os.path.join(ROBOT_DIR, ".env.self")}
SIDE_NAME = {"cloud": "クラウド（Supabase）", "self": "Pi（セルフホスト）"}

BOOK_META = ("isbn", "title", "author", "shelf_level", "tags")
BOOK_COLS = ("id",) + BOOK_META + ("status", "created_at")
LOAN_COLS = ("id", "book_id", "borrower_type", "user_id", "guest_name",
             "borrowed_at", "returned_at", "returned_by")
PIN_COLS = ("id", "label", "x", "y", "theta", "kind")
CALL_COLS = ("id", "status", "seat_id", "requested_by", "created_at")
ACTIVE_CALLS = ("queued", "moving", "arrived")

# 前回そろえたときに両方の DB にあった本の id の控え。「片方にだけ無い＝その側で消された」を見分けるのに使う
#   （setup.sh reset-db / uninstall が消す。無ければ「消された本」は判定せず、足りない本を足すだけ）
STATE_FILE = os.path.join(HERE, ".sync_state.json")


def load_state() -> set:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("books_both", []))
    except (OSError, ValueError):
        return set()


def save_state(book_ids) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"books_both": sorted(book_ids),
                   "saved_at": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


# =====================================================================
#  接続（PostgREST を REST で直接たたく。ブリッジと同じ流儀）
# =====================================================================

def read_env(path: str) -> dict:
    """KEY=VALUE の .env を dict で返す（bookshelf_bridge.py の load_env と同じ読み方）。"""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _chunks(rows: list, n: int):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


class Rest:
    """PostgREST の最小クライアント。クラウドもセルフホストも同じ道順（/rest/v1）で話せる。"""

    def __init__(self, url: str, key: str, timeout: float = 10.0):
        import requests   # Pi では apt の python3-requests。test_sync.py はこのクラスを使わない
        self.rq = requests
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Authorization": f"Bearer {key}",
                  "Content-Type": "application/json"}
        self.timeout = timeout

    def _ok(self, r):
        if r.status_code >= 400:
            raise RuntimeError(f"{r.request.method} {r.url} → {r.status_code} {r.text[:300]}")

    def select_all(self, table: str, cols, order: str = "id.asc", where: str = "") -> list[dict]:
        out, page = [], 1000          # Supabase は 1 回に 1000 行まで返す設定なので、ページに分けて読む
        while True:
            q = f"select={','.join(cols)}&order={order}&limit={page}&offset={len(out)}"
            if where:
                q += "&" + where
            r = self.rq.get(f"{self.base}/{table}?{q}", headers=self.h, timeout=self.timeout)
            self._ok(r)
            rows = r.json()
            out += rows
            if len(rows) < page:
                return out

    def _post(self, table: str, rows: list[dict], resolution: str):
        h = dict(self.h, Prefer=f"resolution={resolution},return=minimal")
        for chunk in _chunks(rows, 200):
            r = self.rq.post(f"{self.base}/{table}?on_conflict=id", headers=h,
                             data=json.dumps(chunk), timeout=self.timeout)
            self._ok(r)

    def insert_missing(self, table: str, rows: list[dict]):
        """無い行だけ足す（同じ id がもう有れば何もしない）。"""
        self._post(table, rows, "ignore-duplicates")

    def upsert(self, table: str, rows: list[dict]):
        """無ければ足し、有れば上書きする。"""
        self._post(table, rows, "merge-duplicates")

    def patch(self, table: str, id_, body: dict, only_if_open: bool = False):
        """1 行を書き換える。only_if_open … 未返却の貸出のときだけ（返却済みの日時を上書きしない）。"""
        q = f"id=eq.{id_}" + ("&returned_at=is.null" if only_if_open else "")
        self.patch_where(table, q, body)

    def patch_where(self, table: str, where: str, body: dict):
        h = dict(self.h, Prefer="return=minimal")
        r = self.rq.patch(f"{self.base}/{table}?{where}", headers=h,
                          data=json.dumps(body), timeout=self.timeout)
        self._ok(r)

    def delete(self, table: str, id_) -> int:
        """1 行を消し、消えた行数を返す（RLS で消せない行は 0。PostgREST は消せる行が無くてもエラーにしない）。"""
        h = dict(self.h, Prefer="return=representation")
        r = self.rq.delete(f"{self.base}/{table}?id=eq.{id_}", headers=h, timeout=self.timeout)
        self._ok(r)
        try:
            return len(r.json())
        except ValueError:
            return 0


def client(side: str, timeout: float = 10.0) -> Rest:
    path = ENV_FILES[side]
    if not os.path.exists(path):
        raise SystemExit(f"{path} がありません。先に bash {HERE}/setup.sh を実行してください")
    env = read_env(path)
    if not env.get("SUPABASE_URL") or not env.get("SUPABASE_ANON_KEY"):
        raise SystemExit(f"{path} に SUPABASE_URL / SUPABASE_ANON_KEY がありません")
    return Rest(env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"], timeout)


def fetch(c) -> dict:
    return {"books": c.select_all("books", BOOK_COLS),
            "loans": c.select_all("loans", LOAN_COLS),
            "stop_points": c.select_all("stop_points", PIN_COLS),
            "calls": c.select_all("robot_calls", ("id", "book_id"))}   # 本を消してよいか（呼出の記録が無いか）を見るためだけ


# =====================================================================
#  揃え方を決める（通信しない純粋な計算。test_sync.py で検証する）
# =====================================================================

@dataclass
class Op:
    side: str                  # "src"（--from 側）か "dst"（--to 側）
    kind: str                  # "insert" | "upsert" | "patch"
    table: str
    rows: list = field(default_factory=list)
    id: object = None
    body: dict = field(default_factory=dict)
    only_if_open: bool = False


@dataclass
class Plan:
    ops: list
    notes: list                # 自動では直さないもの（人が確かめること）
    both_after: set = field(default_factory=set)   # 揃えたあと両方にある本の id（次回「消された本」を見分けるための控え）


def _referenced(d: dict) -> set:
    """貸出か呼出の記録が参照している本の id（外部キーがあるので消せない）。"""
    return {r["book_id"] for r in d.get("loans", ())} | {r["book_id"] for r in d.get("calls", ())}


def _pick(row: dict, cols) -> dict:
    return {c: row.get(c) for c in cols}


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9)
    return a == b


def plan_sync(src: dict, dst: dict, known=None, max_delete: int | None = None,
              names: dict | None = None) -> Plan:
    """src（--from 側）と dst（--to 側）の中身から、揃えるための書き込みの一覧を作る。

    src / dst … {"books": [...], "loans": [...], "stop_points": [...], "calls": [...]}（fetch() の戻り値と同じ形）
    known     … 前回そろえたときに両方にあった本の id（load_state()）。無ければ「消された本」は判定しない
    max_delete… 一度に消してよい冊数の上限（None なら 3 冊か known の 1 割の多いほう）
    書き込みの順番どおりに並べて返す（本 → 貸出 → 本の状態 → ピン → 消す。貸出は本を参照しているため）。
    """
    ops: list[Op] = []
    notes: list[str] = []
    names = names or {"src": "--from 側", "dst": "--to 側"}
    known = set(known or ())

    sb = {r["id"]: r for r in src["books"]}
    db = {r["id"]: r for r in dst["books"]}

    # ---- ⓪ 消された本を見分ける: 前回そろえたときに両方にあった本が、片方にだけ無い → その側で消された
    #        → もう片方でも消す。ただし貸出・呼出の記録が残っていれば消さずに足し戻す（外部キーで消せないため）
    refs = {"src": _referenced(src), "dst": _referenced(dst)}
    cand = [("src", i) for i in sorted(known) if i in sb and i not in db] \
        + [("dst", i) for i in sorted(known) if i in db and i not in sb]
    limit = max_delete if max_delete is not None else max(3, len(known) // 10)
    deleted: set = set()
    del_ops: list[Op] = []
    if len(cand) > limit:
        notes.append(f"消された本が {len(cand)} 冊もあるので、自動では消しません（DB を作り直した直後などの見間違いを"
                     f"防ぐため。本当に消すなら --max-delete {len(cand)}）。今回はもう片方から足し戻します")
    else:
        for side, i in cand:
            row = (sb if side == "src" else db)[i]
            other = "dst" if side == "src" else "src"
            if i in refs[side]:
                notes.append(f"「{row.get('title')}」は{names[other]}で消されていましたが、{names[side]}に"
                             "貸出・呼出の記録があるので消さず、足し戻します")
            else:
                del_ops.append(Op(side, "delete", "books", id=i, body={"title": row.get("title")}))
                deleted.add(i)

    # ---- ① 本: 両方向に足りない本を足す（消すと決めた本は除く）。同じ本の中身が違えば src に合わせる
    add_dst = [_pick(sb[i], BOOK_COLS) for i in sorted(sb) if i not in db and i not in deleted]
    add_src = [_pick(db[i], BOOK_COLS) for i in sorted(db) if i not in sb and i not in deleted]
    if add_dst:
        ops.append(Op("dst", "insert", "books", rows=add_dst))
    if add_src:
        ops.append(Op("src", "insert", "books", rows=add_src))
    for i in sorted(sb.keys() & db.keys()):
        diff = {k: sb[i].get(k) for k in BOOK_META if not _same(sb[i].get(k), db[i].get(k))}
        if diff:
            ops.append(Op("dst", "patch", "books", id=i, body=diff))

    # ---- ② 貸出: 両方向に足りない記録を足す。返却は「済み」の側に寄せる（取り消さない）
    sl = {r["id"]: r for r in src["loans"]}
    dl = {r["id"]: r for r in dst["loans"]}
    changed = {"src": set(), "dst": set()}    # 貸出の記録が増えた・返却が入った本（側ごと）
    add_dst_l = [_pick(sl[i], LOAN_COLS) for i in sorted(sl) if i not in dl]
    add_src_l = [_pick(dl[i], LOAN_COLS) for i in sorted(dl) if i not in sl]
    if add_dst_l:
        ops.append(Op("dst", "insert", "loans", rows=add_dst_l))
    if add_src_l:
        ops.append(Op("src", "insert", "loans", rows=add_src_l))
    changed["dst"].update(r["book_id"] for r in add_dst_l)
    changed["src"].update(r["book_id"] for r in add_src_l)

    merged: dict = {}             # 揃えたあとの貸出（両側で同じになる）
    for i in sorted(sl.keys() | dl.keys()):
        a, b = sl.get(i), dl.get(i)
        if a and b and a.get("returned_at") and not b.get("returned_at"):
            ops.append(Op("dst", "patch", "loans", id=i, only_if_open=True,
                          body={"returned_at": a["returned_at"], "returned_by": a.get("returned_by")}))
            changed["dst"].add(a["book_id"])
            merged[i] = a
        elif a and b and b.get("returned_at") and not a.get("returned_at"):
            ops.append(Op("src", "patch", "loans", id=i, only_if_open=True,
                          body={"returned_at": b["returned_at"], "returned_by": b.get("returned_by")}))
            changed["src"].add(b["book_id"])
            merged[i] = b
        else:
            merged[i] = a or b

    # ---- ③ 本の状態
    #   貸出が動いた本 … 「前からその本があって、貸出の記録が増えた・返却が入った側」だけ、記録から決め直す。
    #                     いま写しを入れた側は写し元の状態のまま（初めて揃えるときに、相手の DB の状態を書き換えないため）
    #   貸出が動いていない本 … src に合わせる
    open_n = Counter(r["book_id"] for r in merged.values() if not r.get("returned_at"))
    for bid in sorted((sb.keys() | db.keys()) - deleted):
        s, d = sb.get(bid), db.get(bid)
        base = s or d               # 片側にしか無い本は、もう片側にはこの行の写しが入る
        k = open_n.get(bid, 0)
        derived = "on_loan" if k else "available"
        title = base.get("title")
        if k > 1:
            notes.append(f"「{title}」に未返却の貸出が {k} 件あります（2 つの DB で別々に借りた？）。"
                         "アプリの本の詳細で返却して、実物と揃えてください")
        moved = bid in changed["src"] or bid in changed["dst"]
        final = {}
        for side, row in (("src", s), ("dst", d)):
            cur = (row or base).get("status")
            if not moved:
                want = base.get("status")
            elif bid in changed[side] and row is not None:
                want = derived
            else:
                want = cur
            if cur != want:
                ops.append(Op(side, "patch", "books", id=bid, body={"status": want}))
            final[side] = want
        if k <= 1 and any(v != derived for v in final.values()):
            shown = "／".join(sorted(set(final.values())))
            notes.append(f"「{title}」は状態が {shown} ですが、未返却の貸出は {k} 件です"
                         "（前からの食い違い。自動では直しません）")

    # ---- ④ ピン: src → dst の一方向
    sp = {r["id"]: r for r in src["stop_points"]}
    dp = {r["id"]: r for r in dst["stop_points"]}
    pins = [_pick(sp[i], PIN_COLS) for i in sorted(sp)
            if i not in dp or any(not _same(sp[i].get(c), dp[i].get(c)) for c in PIN_COLS)]
    if pins:
        ops.append(Op("dst", "upsert", "stop_points", rows=pins))
    for i in sorted(dp.keys() - sp.keys()):
        notes.append(f"ピン id={i}（{dp[i].get('label')}）は --to 側にだけあります（消さずに残します）")

    # ---- ⑤ 消す（最後に。消す側に貸出・呼出の記録が無いことは ⓪ で確かめてある）
    ops += del_ops
    return Plan(ops, notes, both_after=(sb.keys() | db.keys()) - deleted)


def describe(plan: Plan, names: dict) -> list[str]:
    """人が読む要約。names … {"src": "クラウド（Supabase）", "dst": "Pi（セルフホスト）"}"""
    c = Counter()
    for op in plan.ops:
        if op.kind == "insert":
            c[(op.side, op.table, "add")] += len(op.rows)
        elif op.kind == "upsert":
            c[(op.side, op.table, "put")] += len(op.rows)
        elif op.kind == "delete":
            c[(op.side, "books", "delete")] += 1
        elif op.table == "loans":
            c[(op.side, "loans", "return")] += 1
        elif "status" in op.body:
            c[(op.side, "books", "status")] += 1
        else:
            c[(op.side, "books", "meta")] += 1
    label = {("books", "add"): "本を {n} 冊足す",
             ("books", "meta"): "本の題名などを {n} 冊書き換える",
             ("books", "status"): "本の状態（貸出中／在庫あり）を {n} 冊直す",
             ("books", "delete"): "本を {n} 冊消す（もう片方で消されていたもの）",
             ("loans", "add"): "貸出の記録を {n} 件足す",
             ("loans", "return"): "返却を {n} 件反映する",
             ("stop_points", "put"): "ピンを {n} 件書き込む"}
    lines = []
    for side in ("dst", "src"):
        for (tbl, what), text in label.items():
            n = c.get((side, tbl, what), 0)
            if n:
                lines.append(f"  {names[side]}: " + text.format(n=n))
    return lines or ["  変更なし（すでに揃っています）"]


def apply(plan: Plan, clients: dict) -> set:
    """plan の順番どおりに書き込む。clients … {"src": Rest, "dst": Rest}
    戻り値 … 消そうとして消せなかった本の id（控えに残し、次回もう一度消そうとする）。"""
    failed: set = set()
    for op in plan.ops:
        c = clients[op.side]
        if op.kind == "insert":
            c.insert_missing(op.table, op.rows)
        elif op.kind == "upsert":
            c.upsert(op.table, op.rows)
        elif op.kind == "delete":
            try:
                n = c.delete(op.table, op.id)
            except Exception as e:                    # 外部キー（貸出・呼出の記録がある）など
                print(f"    ★「{op.body.get('title')}」を消せませんでした: {str(e)[:200]}（次回もう一度消そうとします）")
                failed.add(op.id)
                continue
            if n < 1:
                print(f"    ★「{op.body.get('title')}」は消えませんでした（delete のポリシーが無い？ クラウドなら "
                      "robot/sql/04 を SQL Editor で、Pi なら setup.sh sql で流す。次回もう一度消そうとします）")
                failed.add(op.id)
        else:
            c.patch(op.table, op.id, op.body, only_if_open=op.only_if_open)
    return failed


# =====================================================================
#  コマンド
# =====================================================================

def cmd_sync(a) -> int:
    if a.frm == a.to:
        raise SystemExit("--from と --to は別々にしてください（cloud と self）")
    names = {"src": SIDE_NAME[a.frm], "dst": SIDE_NAME[a.to]}
    print(f"[同期] {names['src']} → {names['dst']}"
          "（本・貸出は両方向に足し合わせ、ピンはこの向きだけ。片方で消された本はもう片方でも消す）")
    clients = {"src": client(a.frm), "dst": client(a.to)}
    known = load_state()
    try:
        plan = plan_sync(fetch(clients["src"]), fetch(clients["dst"]),
                         known=known, max_delete=a.max_delete, names=names)
    except Exception as e:
        print(f"  ★読み込みに失敗しました: {e}")
        return 1
    for line in describe(plan, names):
        print(line)
    for n in plan.notes:
        print(f"  ★要確認: {n}")
    if a.dry_run:
        print("  （--dry-run なので書き込みません）")
        return 0
    try:
        failed = apply(plan, clients)
    except Exception as e:
        print(f"  ★書き込みの途中で止まりました: {e}\n"
              "    もう一度実行すれば続きから揃います（何度実行しても安全）")
        return 1
    save_state(plan.both_after | failed)      # 次回「消された本」を見分けるための控え
    if plan.ops:
        print("  揃えました")
    return 0


def cmd_calls(a) -> int:
    """呼出の残りを見る。moving / arrived があれば終了コード 3（＝ロボが仕事中。切り替えないほうがよい）。"""
    c = client(a.side)
    try:
        rows = c.select_all("robot_calls", CALL_COLS, order="created_at.asc",
                            where=f"status=in.({','.join(ACTIVE_CALLS)})")
    except Exception as e:
        print(f"  ★{SIDE_NAME[a.side]} の呼出を読めません: {e}")
        return 1
    if not rows:
        print(f"  {SIDE_NAME[a.side]}: 残っている呼出はありません")
        return 0
    print(f"  {SIDE_NAME[a.side]}: 残っている呼出が {len(rows)} 件あります")
    for r in rows:
        print(f"    {r['status']:<8} 席{r['seat_id']}  {r.get('requested_by') or ''}  {r['created_at']}")
    queued = [r["id"] for r in rows if r["status"] == "queued"]
    if a.cancel_queued and queued:
        c.patch_where("robot_calls", f"id=in.({','.join(queued)})&status=eq.queued",
                      {"status": "canceled"})
        print(f"  queued の {len(queued)} 件を取り消しました（canceled）")
    return 3 if any(r["status"] in ("moving", "arrived") for r in rows) else 0


def cmd_check(a) -> int:
    try:
        c = client(a.side, timeout=5.0)
        c.select_all("robot_status", ("id",))
    except SystemExit as e:
        print(f"  {e}")
        return 1
    except Exception as e:
        print(f"  {SIDE_NAME[a.side]} につながりません: {str(e)[:200]}")
        return 1
    print(f"  {SIDE_NAME[a.side]} につながります")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="クラウドと Pi のセルフホスト DB を揃える")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync", help="本・貸出・ピンを揃える")
    p.add_argument("--from", dest="frm", choices=("cloud", "self"), required=True)
    p.add_argument("--to", choices=("cloud", "self"), required=True)
    p.add_argument("--dry-run", action="store_true", help="何が変わるかを見るだけ")
    p.add_argument("--max-delete", type=int, default=None,
                   help="一度に消してよい本の冊数の上限（既定: 3 冊か、前回そろえた本の 1 割の多いほう）")
    p.set_defaults(func=cmd_sync)
    p = sub.add_parser("calls", help="残っている呼出を見る")
    p.add_argument("--side", choices=("cloud", "self"), required=True)
    p.add_argument("--cancel-queued", action="store_true", help="queued の呼出を取り消す")
    p.set_defaults(func=cmd_calls)
    p = sub.add_parser("check", help="つながるか")
    p.add_argument("--side", choices=("cloud", "self"), required=True)
    p.set_defaults(func=cmd_check)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
