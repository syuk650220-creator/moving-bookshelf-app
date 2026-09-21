"""
make_nav2_params.py ― ~/nav2/nav2_params.yaml を自動で作る  ★段階3★

    Nav2 標準の nav2_params.yaml（この Pi に入っている版）
        ＋ nav2_params_差分.yaml（このフォルダ。既定値から変えるところだけ・根拠つき）
        ＋ 機体の半径（--robot-radius）
    ─────────────────────────────────────────────→ ~/nav2/nav2_params.yaml

nano で 1 つずつ直すかわりに、これを 1 回実行します。何を変えたかを一覧で表示し、
書いたファイルを読み直して中身が意図どおりかも確かめます。何度実行しても安全です
（前のファイルは nav2_params.yaml.bak_日時 に残します）。

────────────────────────────────────────────────────────────
使い方（Pi で）
────────────────────────────────────────────────────────────

  cd ~/moving-bookshelf-app/robot/nav2
  python3 make_nav2_params.py                       # まず中身を見るだけ（何も書かない）
  python3 make_nav2_params.py --write               # ~/nav2/nav2_params.yaml を作る
  python3 make_nav2_params.py --write --robot-radius 0.25    # 機体が大きいとき

  --robot-radius … 機体の中心から「いちばん遠い角」までの距離 [m] ＋ 余裕 0.02。
                   本棚が車体より張り出しているなら、本棚の角で測ること。
                   小さすぎると壁や机を擦り、大きすぎると狭い所を通れません。

────────────────────────────────────────────────────────────
差分の重ね方
────────────────────────────────────────────────────────────
  ・基本は「同じ場所のキーだけ上書き、ほかは標準のまま」
  ・ただし plugin の種類が変わる節（FollowPath: MPPI → RotationShim+RPP）は、
    節ごと差し替える（MPPI 用のキーを残すと紛らわしいため）
  ・behavior_server は既定では重ねない ★重要★
    差分は「後退・その場旋回のリカバリを外す」設定を載せているが、これは
    ビヘイビアツリーの XML からも Spin / BackUp を外すのが条件。片方だけ変えると
    bt_navigator が起動に失敗する。XML を直してから --with-behavior-server を付けること。
"""

from __future__ import annotations

import argparse
import copy
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE = "/opt/ros/jazzy/share/nav2_bringup/params/nav2_params.yaml"
DEFAULT_DIFF = os.path.join(HERE, "nav2_params_差分.yaml")
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "nav2", "nav2_params.yaml")

# 既定では重ねない節（理由は冒頭の説明）
SKIP_BY_DEFAULT = ("behavior_server",)

# 実走に必須で、重ねたあとにこの値になっていなければおかしいもの（最後に検算する）
MUST_HAVE = [
    (("amcl", "ros__parameters", "robot_model_type"), "nav2_amcl::OmniMotionModel"),
    (("controller_server", "ros__parameters", "general_goal_checker", "stateful"), True),
    (("controller_server", "ros__parameters", "general_goal_checker", "xy_goal_tolerance"), 0.08),
]


# =====================================================================
#  重ね合わせ（yaml を import しなくても test_logic.py から検証できる部分）
# =====================================================================

class _Missing:
    def __repr__(self):
        return "（なし）"


MISSING = _Missing()


def deep_merge(base, over, path=(), changes=None):
    """
    base に over を重ねた新しい dict を返す（base は書き換えない）。
    changes に (path, 旧, 新) を積む。旧が無ければ None の代わりに MISSING。
    plugin の種類が変わる dict は、節ごと差し替える。
    """
    if changes is None:
        changes = []
    if not isinstance(base, dict) or not isinstance(over, dict):
        if base != over:
            changes.append((path, base, over))
        return copy.deepcopy(over), changes

    if "plugin" in over and "plugin" in base and over["plugin"] != base["plugin"]:
        changes.append((path, f"（{base['plugin']} の節）", f"（{over['plugin']} の節に差し替え）"))
        return copy.deepcopy(over), changes

    out = copy.deepcopy(base)
    for k, v in over.items():
        if k in base:
            out[k], _ = deep_merge(base[k], v, path + (k,), changes)
        else:
            changes.append((path + (k,), MISSING, v))
            out[k] = copy.deepcopy(v)
    return out, changes


def get_path(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return MISSING
        d = d[k]
    return d


def set_robot_radius(params: dict, radius: float, changes: list) -> list[str]:
    """local / global costmap の robot_radius を設定する。footprint を使っている節は触らず警告を返す。"""
    warns = []
    for name in ("local_costmap", "global_costmap"):
        node = get_path(params, (name, name, "ros__parameters"))
        if node is MISSING:
            warns.append(f"{name} の節が見つかりません（標準ファイルの形が違う？）")
            continue
        if "footprint" in node:
            warns.append(f"{name} は footprint（多角形）で形を決めています。robot_radius は設定しません")
            continue
        old = node.get("robot_radius", MISSING)
        if old != radius:
            changes.append(((name, name, "ros__parameters", "robot_radius"), old, radius))
        node["robot_radius"] = radius
    return warns


def stale_items(existing, merged: dict) -> list:
    """いまの出力ファイル existing を、あるべき姿 merged にするのに変える必要がある所。
    [(path, いまの値, あるべき値)]。空なら最新。"""
    if existing == merged:
        return []
    _, changes = deep_merge(existing if isinstance(existing, dict) else {}, merged)
    return changes or [(("(ファイル全体)",), "…", "…")]


def find_unsupported(d, path=()):
    """ROS 2 のパラメータ読み込みが苦手な値（null・空のリスト）を探す。"""
    bad = []
    if isinstance(d, dict):
        for k, v in d.items():
            bad += find_unsupported(v, path + (str(k),))
    elif d is None or (isinstance(d, list) and len(d) == 0):
        bad.append(path)
    return bad


def fmt_path(path) -> str:
    return ".".join(p for p in path if p != "ros__parameters")


def fmt_val(v) -> str:
    if isinstance(v, dict):
        return "{…%d 項目}" % len(v)
    return repr(v) if not isinstance(v, str) else v


# =====================================================================
#  main
# =====================================================================

def main():
    ap = argparse.ArgumentParser(description="標準の nav2_params.yaml に差分を重ねて ~/nav2/nav2_params.yaml を作る")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"標準ファイル（既定: {DEFAULT_BASE}）")
    ap.add_argument("--diff", default=DEFAULT_DIFF, help="差分ファイル（既定: このフォルダの nav2_params_差分.yaml）")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"出力先（既定: {DEFAULT_OUT}）")
    ap.add_argument("--robot-radius", type=float, default=None,
                    help="機体の半径 [m]。中心からいちばん遠い角まで＋余裕 0.02（既定 0.22 = Nav2 の標準値。"
                         "--check のときは、いまのファイルに入っている値）")
    ap.add_argument("--check", action="store_true",
                    help="何も書かずに、いまの出力ファイルが「標準＋いまの差分ファイル」と同じか（＝最新か）だけを調べる。"
                         "古ければ終了コード 1")
    ap.add_argument("--with-behavior-server", action="store_true",
                    help="behavior_server の差分（後退・その場旋回を外す）も重ねる。"
                         "★ビヘイビアツリーの XML から Spin/BackUp を外してあるときだけ★")
    ap.add_argument("--write", action="store_true", help="実際にファイルを書く（付けなければ変更点を表示するだけ）")
    args = ap.parse_args()

    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML がありません。Pi なら  sudo apt install -y python3-yaml  で入ります。")

    for label, p in (("標準ファイル", args.base), ("差分ファイル", args.diff)):
        if not os.path.exists(p):
            hint = ("  Nav2 が未導入なら: sudo apt install -y ros-jazzy-navigation2 ros-jazzy-nav2-bringup"
                    if p == args.base else "")
            sys.exit(f"{label}が見つかりません: {p}\n{hint}")
    existing = None
    if args.check:
        if not os.path.exists(args.out):
            sys.exit(f"★まだ作られていません★ {args.out}\n"
                     f"  python3 make_nav2_params.py --write --robot-radius 0.21")
        with open(args.out, encoding="utf-8") as f:
            existing = yaml.safe_load(f)
        if args.robot_radius is None:                 # いまのファイルの半径のまま比べる
            r = get_path(existing, ("local_costmap", "local_costmap", "ros__parameters", "robot_radius"))
            args.robot_radius = float(r) if isinstance(r, (int, float)) else 0.22
    if args.robot_radius is None:
        args.robot_radius = 0.22
    if not 0.05 <= args.robot_radius <= 1.0:
        sys.exit("--robot-radius は m 単位です（例: 0.22）。0.05〜1.0 の範囲で指定してください。")

    with open(args.base, encoding="utf-8") as f:
        base = yaml.safe_load(f)
    with open(args.diff, encoding="utf-8") as f:
        diff = yaml.safe_load(f)

    skipped = []
    if not args.with_behavior_server:
        for k in SKIP_BY_DEFAULT:
            if k in diff:
                diff.pop(k)
                skipped.append(k)
    unknown = [k for k in diff if k not in base]

    merged, changes = deep_merge(base, diff)
    warns = set_robot_radius(merged, args.robot_radius, changes)

    if args.check:
        stale = stale_items(existing, merged)
        if not stale:
            print(f"✓ {args.out} は最新です（標準＋いまの差分ファイル＋半径 {args.robot_radius} m と同じ）")
            return
        print(f"★{args.out} が古いです★ いまの差分ファイルと {len(stale)} か所ちがいます:")
        for path, old, new in stale[:12]:
            print(f"  {fmt_path(path)}\n      いまのファイル {fmt_val(old)}  →  あるべき値 {fmt_val(new)}")
        if len(stale) > 12:
            print(f"  …ほか {len(stale) - 12} か所")
        print("\n作り直すコマンド（Nav2 が動いていたら、作り直したあとで Nav2 を起動し直すこと）:")
        print(f"  cd {os.path.dirname(os.path.abspath(__file__))} && python3 make_nav2_params.py --write --robot-radius {args.robot_radius}")
        sys.exit(1)

    print(f"標準 : {args.base}")
    print(f"差分 : {args.diff}")
    print(f"出力 : {args.out}" + ("" if args.write else "   ★まだ書きません（--write で書く）★"))
    print(f"\n=== 変えるところ（{len(changes)} か所） ===")
    for path, old, new in changes:
        print(f"  {fmt_path(path)}\n      {fmt_val(old)}  →  {fmt_val(new)}")
    if skipped:
        print(f"\n重ねなかった節: {', '.join(skipped)}（ビヘイビアツリーの XML も直してから --with-behavior-server）")
    for k in unknown:
        warns.append(f"差分の節 {k} は標準ファイルにありません（Nav2 の版が違う？）。そのまま追加しました")
    for path, want in MUST_HAVE:
        got = get_path(merged, path)
        if got != want:
            warns.append(f"検算: {fmt_path(path)} が {fmt_val(got)} です（{fmt_val(want)} のはず）")
    for path in find_unsupported(merged):
        if get_path(base, tuple(path)) is MISSING:     # 標準ファイルに元からあるものは問題にしない
            warns.append(f"{'.'.join(path)} が空です（ROS 2 が読めないことがあります）")
    if warns:
        print("\n=== ★注意★ ===")
        for w in warns:
            print("  ・" + w)

    print("\n=== フレーム名（TF の鎖と合っているか） ===")
    for path in (("amcl", "ros__parameters", "base_frame_id"),
                 ("amcl", "ros__parameters", "odom_frame_id"),
                 ("amcl", "ros__parameters", "scan_topic"),
                 ("local_costmap", "local_costmap", "ros__parameters", "robot_base_frame"),
                 ("global_costmap", "global_costmap", "ros__parameters", "robot_base_frame")):
        print(f"  {fmt_path(path)} = {fmt_val(get_path(merged, path))}")
    print("  → map → odom → base_footprint → base_link → laser の鎖（mecanum_node を base_frame:=base_footprint、"
          "\n     残りを robot_tf_launch.py）なら、このままで合います。")

    if not args.write:
        print("\nこれでよければ、同じコマンドに --write を付けて実行してください。")
        return

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(args.out):
        bak = args.out + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.replace(args.out, bak)
        print(f"\n前のファイルを退避しました: {bak}")

    header = (
        "# =====================================================================\n"
        "#  このファイルは make_nav2_params.py が作りました。★直接編集しないでください★\n"
        f"#    作成: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"#    標準: {args.base}\n"
        f"#    差分: {os.path.basename(args.diff)}（値の根拠はこちらに書いてあります）\n"
        f"#    機体の半径: {args.robot_radius} m\n"
        "#  値を変えたいときは差分ファイルを直して、もう一度 make_nav2_params.py --write を実行します。\n"
        "# =====================================================================\n"
    )
    class _Dumper(yaml.SafeDumper):       # 標準ファイルと同じ見た目（節は段組み、リストだけ [a, b]）
        pass

    _Dumper.add_representer(
        list, lambda d, data: d.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True))
    body = yaml.dump(merged, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=1000)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + body)

    with open(args.out, encoding="utf-8") as f:       # 書いたものを読み直して検算
        again = yaml.safe_load(f)
    if again != merged:
        sys.exit("★書いたファイルを読み直したら中身が一致しません。このファイルは使わないでください★")
    print(f"\n✓ 書きました: {args.out}（読み直して一致を確認）")
    print("\n次にやること（Nav2 の起動）:")
    print(f"  ros2 launch nav2_bringup bringup_launch.py map:=$HOME/maps/my_map.yaml use_sim_time:=false params_file:={args.out}")


if __name__ == "__main__":
    main()
