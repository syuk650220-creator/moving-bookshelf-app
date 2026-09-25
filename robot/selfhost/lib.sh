# shellcheck shell=bash
# =====================================================================
#  setup.sh と mode.sh の共通部品（source して使う。直接は実行しない）
# =====================================================================

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_DIR="$(dirname "$HERE")"
REPO_DIR="$(dirname "$ROBOT_DIR")"
VOLUME_DB="bookshelf-selfhost_db-data"     # compose.yaml の name + ボリューム名

step() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '   \033[1;33m★\033[0m %s\n' "$*"; }
die()  { printf '   \033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# docker グループに入っていなければ sudo を付ける（グループに入れない＝Pi の設定を増やさないため）
docker_() {
  if docker info >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi
}
dc() {
  docker_ compose --project-directory "$HERE" -f "$HERE/compose.yaml" "$@"
}

# いまのモード（robot/.env がどちらを指しているか）: self / cloud / plain（setup 前の普通のファイル）/ none
current_mode() {
  local link="$ROBOT_DIR/.env"
  if [ -L "$link" ]; then
    case "$(readlink "$link")" in
      *.env.self)  echo self ;;
      *.env.cloud) echo cloud ;;
      *)           echo unknown ;;
    esac
  elif [ -f "$link" ]; then echo plain
  else echo none
  fi
}

# セルフホストのコンテナが動いているか
self_running() {
  dc ps --status running --services 2>/dev/null | grep -qx rest
}

# 入口（caddy）から、読み書きの窓口と画面の両方が返事をするまで待つ。$1 = 最大秒数
wait_ready() {
  local limit="${1:-120}" t=0
  until curl -fsS -m 3 -o /dev/null "http://127.0.0.1/rest/v1/robot_status?select=id" 2>/dev/null \
        && curl -fsS -m 5 -o /dev/null "http://127.0.0.1/" 2>/dev/null; do
    t=$((t + 3))
    [ "$t" -ge "$limit" ] && return 1
    sleep 3
  done
}

# クラウド（robot/.env.cloud の接続先）につながるか
cloud_ok() {
  [ -f "$ROBOT_DIR/.env.cloud" ] && python3 "$HERE/sync.py" check --side cloud >/dev/null 2>&1
}

# スマホで開く URL（Docker の中の住所は除く）
lan_urls() {
  ip -4 -o addr show scope global 2>/dev/null \
    | awk '$2 !~ /^(docker|br-|veth)/ { split($4, a, "/"); print "http://" a[1] "/" }'
  echo "http://$(hostname).local/   （iPhone・PC なら名前でも開ける。Android は上の数字の URL で）"
}

# 接続先を起動時に読み込んだまま動いているロボ側のプログラム
robot_procs() {
  pgrep -af 'bookshelf_bridge\.py|manual_control\.py|pin_tool\.py' 2>/dev/null | grep -v pgrep || true
}

# confirm "質問" → y なら 0。端末から動かしていないとき（自動実行）は「いいえ」
confirm() {
  local ans
  [ -t 0 ] || return 1
  read -r -p "   $1 [y/N] " ans
  [[ "$ans" =~ ^[yY] ]]
}
