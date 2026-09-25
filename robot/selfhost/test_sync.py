#!/usr/bin/env python3
"""
test_sync.py ― sync.py の「揃え方」の検証（ネットも DB も要らない）

  python3 test_sync.py

実際の DB の代わりに、手元の表（FakeRest）へ書き込んで、
  ・揃ったあとに両方の DB の本・貸出が同じになるか
  ・もう一度揃えると「変更なし」になるか（何度実行しても安全か）
  ・返却が取り消されないか、ピンが一方向だけに動くか
を確かめる。
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync  # noqa: E402

B1, B2, B3 = "b1111111-0000-0000-0000-000000000001", "b2222222-0000-0000-0000-000000000002", \
    "b3333333-0000-0000-0000-000000000003"
L1, L2 = "11111111-0000-0000-0000-00000000000a", "22222222-0000-0000-0000-00000000000b"


def book(id_, title="本", status="available", **kw):
    r = {"id": id_, "isbn": None, "title": title, "author": "著者", "shelf_level": 1,
         "tags": [], "status": status, "created_at": "2026-09-01T00:00:00+00:00"}
    r.update(kw)
    return r


def loan(id_, book_id, returned=False, who="水谷"):
    return {"id": id_, "book_id": book_id, "borrower_type": "guest", "user_id": None,
            "guest_name": who, "borrowed_at": "2026-09-25T10:00:00+00:00",
            "returned_at": "2026-09-25T11:00:00+00:00" if returned else None,
            "returned_by": who if returned else None}


def pin(id_, x=0.0, y=0.0, theta=0.0, label=None):
    return {"id": id_, "label": label or ("本棚の場所" if id_ == 0 else f"席{id_}"),
            "x": x, "y": y, "theta": theta, "kind": "home" if id_ == 0 else "seat"}


class FakeRest:
    """sync.Rest と同じ呼び方で、メモリ上の表を読み書きする偽の DB。"""

    def __init__(self, books=(), loans=(), pins=()):
        self.t = {"books": [copy.deepcopy(r) for r in books],
                  "loans": [copy.deepcopy(r) for r in loans],
                  "stop_points": [copy.deepcopy(r) for r in pins]}

    def select_all(self, table, cols, order="id.asc", where=""):
        return [{c: r.get(c) for c in cols} for r in sorted(self.t[table], key=lambda r: r["id"])]

    def insert_missing(self, table, rows):
        have = {r["id"] for r in self.t[table]}
        self.t[table] += [copy.deepcopy(r) for r in rows if r["id"] not in have]

    def upsert(self, table, rows):
        for new in rows:
            for r in self.t[table]:
                if r["id"] == new["id"]:
                    r.update(copy.deepcopy(new))
                    break
            else:
                self.t[table].append(copy.deepcopy(new))

    def patch(self, table, id_, body, only_if_open=False):
        for r in self.t[table]:
            if r["id"] == id_ and not (only_if_open and r.get("returned_at")):
                r.update(copy.deepcopy(body))


def run(src, dst):
    """1 回揃えて、その計画を返す。"""
    plan = sync.plan_sync(sync.fetch(src), sync.fetch(dst))
    sync.apply(plan, {"src": src, "dst": dst})
    return plan


class TestSync(unittest.TestCase):

    def assertConverged(self, a, b):
        """両方の本・貸出が同じで、もう一度揃えても変更なし。"""
        self.assertEqual(sync.fetch(a)["books"], sync.fetch(b)["books"])
        self.assertEqual(sync.fetch(a)["loans"], sync.fetch(b)["loans"])
        again = sync.plan_sync(sync.fetch(a), sync.fetch(b))
        self.assertEqual(again.ops, [], f"2 回目に変更が出た: {again.ops}")

    def status(self, db, bid):
        return next(r["status"] for r in db.t["books"] if r["id"] == bid)

    def test_Piで借りた本がクラウドにも貸出中として届く(self):
        cloud = FakeRest(books=[book(B1)])
        pi = FakeRest(books=[book(B1, status="on_loan")], loans=[loan(L1, B1)])
        run(pi, cloud)                               # mode.sh cloud（Pi → クラウド）
        self.assertEqual(self.status(cloud, B1), "on_loan")
        self.assertEqual(len(cloud.t["loans"]), 1)
        self.assertConverged(pi, cloud)

    def test_クラウドで借りてPiで返した本は返却済みになる(self):
        cloud = FakeRest(books=[book(B1, status="on_loan")], loans=[loan(L1, B1)])
        pi = FakeRest(books=[book(B1)], loans=[loan(L1, B1, returned=True)])
        run(pi, cloud)
        self.assertEqual(self.status(cloud, B1), "available")
        self.assertIsNotNone(cloud.t["loans"][0]["returned_at"])
        self.assertConverged(pi, cloud)

    def test_同期を忘れて逆向きに揃えても返却は取り消されない(self):
        # Pi で返したのに、Pi → クラウドをせずにクラウド → Pi をしてしまった
        cloud = FakeRest(books=[book(B1, status="on_loan")], loans=[loan(L1, B1)])
        pi = FakeRest(books=[book(B1)], loans=[loan(L1, B1, returned=True)])
        run(cloud, pi)                               # mode.sh self（クラウド → Pi）
        self.assertIsNotNone(pi.t["loans"][0]["returned_at"])
        self.assertEqual(self.status(pi, B1), "available")
        self.assertEqual(self.status(cloud, B1), "available")
        self.assertConverged(cloud, pi)

    def test_片方にしか無い本は両方に足される(self):
        cloud = FakeRest(books=[book(B1), book(B2, title="クラウドで登録")])
        pi = FakeRest(books=[book(B1), book(B3, title="Pi で登録")])
        run(cloud, pi)
        ids = lambda db: sorted(r["id"] for r in db.t["books"])  # noqa: E731
        self.assertEqual(ids(cloud), [B1, B2, B3])
        self.assertEqual(ids(pi), [B1, B2, B3])
        self.assertConverged(cloud, pi)

    def test_両方で別々に借りた本は要確認になり貸出中のまま(self):
        cloud = FakeRest(books=[book(B1, status="on_loan")], loans=[loan(L1, B1, who="Aさん")])
        pi = FakeRest(books=[book(B1, status="on_loan")], loans=[loan(L2, B1, who="Bさん")])
        plan = run(pi, cloud)
        self.assertTrue(any("未返却の貸出が 2 件" in n for n in plan.notes))
        self.assertEqual(self.status(cloud, B1), "on_loan")
        self.assertEqual(self.status(pi, B1), "on_loan")
        self.assertConverged(pi, cloud)

    def test_ピンはfrom側からto側へだけ動く(self):
        pi = FakeRest(pins=[pin(0), pin(1, x=1.5, y=0.2)])
        cloud = FakeRest(pins=[pin(0), pin(1, x=9.9), pin(4, x=3.0)])
        plan = run(pi, cloud)
        c1 = next(r for r in cloud.t["stop_points"] if r["id"] == 1)
        self.assertEqual((c1["x"], c1["y"]), (1.5, 0.2))
        self.assertEqual(len(pi.t["stop_points"]), 2, "src（Pi）側のピンは増えない")
        self.assertTrue(any("id=4" in n for n in plan.notes), "to 側にだけあるピンは消さずに知らせる")
        again = sync.plan_sync(sync.fetch(pi), sync.fetch(cloud))
        self.assertEqual([op for op in again.ops if op.table == "stop_points"], [])

    def test_貸出が動いていない本の状態はfrom側に合わせる(self):
        cloud = FakeRest(books=[book(B1, status="on_loan")], loans=[loan(L1, B1)])
        pi = FakeRest(books=[book(B1, status="available")], loans=[loan(L1, B1)])
        plan = run(cloud, pi)
        self.assertEqual(self.status(pi, B1), "on_loan")
        self.assertEqual(plan.notes, [])
        self.assertConverged(cloud, pi)

    def test_状態と貸出の記録の前からの食い違いは知らせるだけ(self):
        cloud = FakeRest(books=[book(B1, status="on_loan")])
        pi = FakeRest(books=[book(B1, status="on_loan")])
        plan = run(cloud, pi)
        self.assertEqual(plan.ops, [])
        self.assertTrue(any("前からの食い違い" in n for n in plan.notes))

    def test_初めて揃えるときはクラウドに何も書かない(self):
        # 空の Pi へ初めて入れる。クラウドには前からの食い違い（返却済みなのに貸出中）の本がある
        cloud = FakeRest(books=[book(B1, status="on_loan"), book(B2, status="on_loan")],
                         loans=[loan(L1, B1, returned=True), loan(L2, B2)],
                         pins=[pin(0), pin(1, x=1.0)])
        pi = FakeRest(pins=[pin(0)])
        before = copy.deepcopy(cloud.t)
        plan = run(cloud, pi)                        # setup.sh / mode.sh self（クラウド → Pi）
        self.assertEqual([op for op in plan.ops if op.side == "src"], [], "クラウドへの書き込みがあった")
        self.assertEqual(cloud.t, before)
        self.assertEqual(self.status(pi, B1), "on_loan", "写しは写し元の状態のまま")
        self.assertEqual(self.status(pi, B2), "on_loan")
        self.assertTrue(any("前からの食い違い" in n for n in plan.notes))
        self.assertEqual(len(pi.t["stop_points"]), 2)
        self.assertConverged(cloud, pi)

    def test_同じ本の題名が違えばfrom側に合わせる(self):
        cloud = FakeRest(books=[book(B1, title="正しい題名", tags=["ROS"])])
        pi = FakeRest(books=[book(B1, title="古い題名")])
        run(cloud, pi)
        r = pi.t["books"][0]
        self.assertEqual((r["title"], r["tags"]), ("正しい題名", ["ROS"]))
        self.assertConverged(cloud, pi)

    def test_まとめて動かしても揃い2回目は変更なし(self):
        cloud = FakeRest(books=[book(B1, status="on_loan"), book(B2)],
                         loans=[loan(L1, B1)], pins=[pin(0), pin(1, x=1.0)])
        pi = FakeRest(books=[book(B1), book(B3, status="on_loan")],
                      loans=[loan(L1, B1, returned=True), loan(L2, B3)],
                      pins=[pin(0), pin(1, x=1.2), pin(2, x=2.0)])
        run(pi, cloud)
        self.assertConverged(pi, cloud)
        self.assertEqual(self.status(cloud, B1), "available")
        self.assertEqual(self.status(cloud, B3), "on_loan")

    def test_要約の文(self):
        empty = sync.Plan([], [])
        self.assertIn("変更なし", sync.describe(empty, {"src": "A", "dst": "B"})[0])
        cloud = FakeRest(books=[book(B1)])
        pi = FakeRest()
        plan = sync.plan_sync(sync.fetch(cloud), sync.fetch(pi))
        self.assertIn("本を 1 冊足す", "\n".join(sync.describe(plan, {"src": "A", "dst": "B"})))


if __name__ == "__main__":
    unittest.main(verbosity=2)
