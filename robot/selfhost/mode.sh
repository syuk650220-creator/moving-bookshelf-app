#!/usr/bin/env bash
# =====================================================================
#  mode.sh ― アプリとロボの「つなぎ先」を切り替える
#
#    bash ~/moving-bookshelf-app/robot/selfhost/mode.sh self     # セルフホストへ（ネットが無くても動く）
#    bash ~/moving-bookshelf-app/robot/selfhost/mode.sh cloud    # クラウドへ（Vercel＋Supabase。★要ネット★）
#    bash ~/moving-bookshelf-app/robot/selfhost/mode.sh status   # いまどちらか・動いているか・負荷
#
#    --no-sync … 本・貸出・ピンを揃えずに切り替える
#    --force   … ロボが呼出の途中でも切り替える／クラウドにつながらなくても cloud にする
#
#  self : コンテナを起動 →（ネットがあれば）クラウドから本・貸出・ピンを揃える → robot/.env を .env.self に
#  cloud: Pi から本・貸出・ピンをクラウドへ揃える → robot/.env を .env.cloud に → コンテナを止める
#  ★切り替えたら、窓⑤（ブリッジ）・手動操作・pin_tool を起動し直す（起動したときに robot/.env を読むため）★
# =====================================================================
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="${1:-status}"
[ $# -gt 0 ] && shift
NO_SYNC=0
FORCE=0
for a in "$@"; do
  case "$a" in
    --no-sync) NO_SYNC=1 ;;
    --force)   FORCE=1 ;;
    *)         die "知らないオプションです: $a" ;;
  esac
done

ready_check() {
  [ -f "$HERE/.env" ] && [ -f "$ROBOT_DIR/.env.self" ] \
    || die "まだ準備ができていません。先に bash $HERE/setup.sh を実行してください（初回だけ・要ネット）"
}

# $1 の側でロボが呼出の途中（moving / arrived）なら止める
guard_ride() {
  local rc=0
  python3 "$HERE/sync.py" calls --side "$1" || rc=$?
  if [ "$rc" -eq 3 ] && [ "$FORCE" = 0 ]; then
    die "ロボが呼出の途中です。届け終わって本棚へ戻ってから切り替えてください（強行するなら --force）"
  fi
}

# 切り替え先に queued の呼出が残っていたら、取り消すか聞く（ブリッジは起動するとすぐ走り出すため）
offer_cancel() {
  local out
  out="$(python3 "$HERE/sync.py" calls --side "$1" 2>&1)" || true
  if grep -q 'queued' <<<"$out"; then
    echo "$out"
    warn "このまま窓⑤（ブリッジ）を起動すると、上の queued の呼出ですぐにロボが走り出します"
    if confirm "queued の呼出を取り消しますか？"; then
      python3 "$HERE/sync.py" calls --side "$1" --cancel-queued >/dev/null && ok "取り消しました"
    fi
  fi
}

after_switch() {
  echo
  if [ "$1" = self ]; then
    echo "   スマホ・PC は Pi と同じ Wi-Fi（ルーター）につないで、次のどれかを開く:"
    lan_urls | sed 's/^/     /'
    echo "   画面のいちばん上に「セルフホスト版」の帯が出ていれば、Pi につながっています"
  else
    echo "   スマホはいつもの Vercel の URL（https://moving-bookshelf-app.vercel.app）を開く（スマホにもネットが要ります）"
  fi
  local procs
  procs="$(robot_procs)"
  if [ -n "$procs" ]; then
    warn "次のプログラムは、前のつなぎ先のまま動いています。Ctrl-C で止めて起動し直してください:"
    echo "$procs" | sed 's/^/     /'
  else
    echo "   窓⑤（ブリッジ）・手動操作・pin_tool は、これから起動すれば新しいつなぎ先を使います"
  fi
}

to_self() {
  ready_check
  local now
  now="$(current_mode)"
  step "セルフホスト（Pi の中だけで動く）に切り替えます"
  if [ "$now" != self ] && cloud_ok; then guard_ride cloud; fi
  dc up -d
  wait_ready 120 || { dc ps; die "コンテナは起動しましたが返事がありません。bash $HERE/mode.sh status で確かめてください"; }
  ok "コンテナが動いています"
  if [ "$now" = self ]; then
    ok "すでにセルフホストのモードです（揃えるのは cloud から切り替えるときだけ）"
  elif [ "$NO_SYNC" = 1 ]; then
    warn "--no-sync: 揃えずに切り替えます（Pi の DB は前回のまま）"
  elif cloud_ok; then
    python3 "$HERE/sync.py" sync --from cloud --to self \
      || die "揃えるのに失敗しました。もう一度実行するか、揃えずに切り替えるなら --no-sync を付けてください"
  else
    warn "クラウドにつながらないので、揃えずに切り替えます（Pi の DB は前回のまま）"
  fi
  ln -sfn .env.self "$ROBOT_DIR/.env"
  ok "robot/.env → .env.self（ロボ側は Pi の中の DB を使います）"
  offer_cancel self
  after_switch self
}

to_cloud() {
  ready_check
  [ -f "$ROBOT_DIR/.env.cloud" ] || die "robot/.env.cloud がありません（クラウドの接続先。robot/README.md §3-2 の値で作ってください）"
  local now
  now="$(current_mode)"
  step "クラウド（Vercel＋Supabase）に切り替えます"
  if ! cloud_ok; then
    [ "$FORCE" = 1 ] || die "クラウドにつながりません。テザリングなどで Pi をネットにつないでから実行してください
      （揃えずに切り替えだけするなら --force --no-sync。そのあと、ネットにつないでから揃え直すこと）"
    warn "--force: クラウドにつながらないまま切り替えます"
    NO_SYNC=1
  fi
  if [ "$now" = self ] && [ "$NO_SYNC" = 0 ]; then
    if ! self_running; then
      dc up -d
      wait_ready 120 || die "Pi の DB が起動しないので、揃えられません"
    fi
    guard_ride self
    python3 "$HERE/sync.py" sync --from self --to cloud \
      || die "揃えるのに失敗しました。もう一度実行してください（揃えずに切り替えるなら --no-sync）"
  fi
  ln -sfn .env.cloud "$ROBOT_DIR/.env"
  ok "robot/.env → .env.cloud（ロボ側はクラウドの DB を使います）"
  [ "$NO_SYNC" = 1 ] || offer_cancel cloud
  dc stop >/dev/null 2>&1 || true
  ok "コンテナを止めました（Pi の DB の中身は残っています）"
  after_switch cloud
}

show_status() {
  step "いまの状態"
  case "$(current_mode)" in
    self)  echo "   モード: セルフホスト（robot/.env → .env.self）" ;;
    cloud) echo "   モード: クラウド（robot/.env → .env.cloud）" ;;
    plain) echo "   モード: クラウド（setup.sh をまだ実行していない）" ;;
    *)     echo "   モード: 不明（robot/.env がありません）" ;;
  esac
  echo "   コンテナ:"
  dc ps --all --format 'table {{.Service}}\t{{.State}}\t{{.Status}}' 2>/dev/null | sed 's/^/     /' || echo "     （まだありません）"
  if self_running; then
    if wait_ready 3; then ok "セルフホストは返事をします"; else warn "コンテナは動いていますが、まだ返事がありません"; fi
    lan_urls | sed 's/^/     /'
    echo "   コンテナの負荷:"
    docker_ stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null | sed 's/^/     /' || true
  fi
  if cloud_ok; then ok "クラウドにつながります"; else warn "クラウドにつながりません（ネットが無い？）"; fi
  # 画面のイメージが、手元のアプリのコードより古くないか
  local built last
  built="$(docker_ image inspect bookshelf-selfhost-web --format '{{.Created}}' 2>/dev/null || true)"
  last="$(git -C "$REPO_DIR" log -1 --format=%cI -- app components lib package-lock.json next.config.ts 2>/dev/null || true)"
  if [ -n "$built" ] && [ -n "$last" ] && [ "$(date -d "$last" +%s)" -gt "$(date -d "$built" +%s)" ]; then
    warn "画面のイメージが古いです（アプリのコードが後から更新された）→ bash $HERE/setup.sh rebuild-web（要ネット）"
  fi
  echo "   Pi 全体: $(uptime | sed 's/.*load average/load average/')"
  free -h | awk 'NR<=2' | sed 's/^/     /'
  local procs
  procs="$(robot_procs)"
  [ -z "$procs" ] || { echo "   動いているロボ側のプログラム:"; echo "$procs" | sed 's/^/     /'; }
}

case "$TARGET" in
  self)   to_self ;;
  cloud)  to_cloud ;;
  status) show_status ;;
  *)      sed -n '2,17p' "$0"; exit 1 ;;
esac
