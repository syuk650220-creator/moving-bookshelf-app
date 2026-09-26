#!/usr/bin/env bash
# =====================================================================
#  setup.sh ― セルフホスト（Docker）の準備（何度実行しても安全）
#
#    bash ~/moving-bookshelf-app/robot/selfhost/setup.sh              # 初回・やり直し（★インターネットが要る★）
#    bash ~/moving-bookshelf-app/robot/selfhost/setup.sh rebuild-web  # アプリ（画面）を更新したとき（★要ネット★）
#    bash ~/moving-bookshelf-app/robot/selfhost/setup.sh sql <file>   # あとから足した SQL を Pi の DB にも流す（権限も付け直す）
#    bash ~/moving-bookshelf-app/robot/selfhost/setup.sh reset-db     # Pi の DB を空から作り直す
#    bash ~/moving-bookshelf-app/robot/selfhost/setup.sh uninstall    # セルフホストをまるごと片付ける
#
#  ★Pi 本体に入れるのは Docker（Ubuntu 標準の docker.io・docker-compose-v2・docker-buildx）だけ★
#   DB・読み書きの窓口・画面・入口はすべてコンテナの中（compose.yaml）。Pi の設定ファイルは触らない。
#
#  初回にやること
#    1. Docker を入れる（apt）
#    2. robot/selfhost/.env を作る（DB のパスワード。ランダム。git には入らない）
#    3. CPU の上限が使えるか確かめる（Nav2 の取り分を守るため）
#    4. イメージを取ってくる・画面のイメージを作る（数分〜十数分）
#    5. robot/.env を .env.cloud（いまの中身）と .env.self に分け、.env はどちらかを指すリンクにする
#    6. 一度起動して DB を作り、動くことを確かめる → クラウドから本・貸出・ピンを入れる
#    7. いまのモードがクラウドなら、コンテナを止めて終わる（mode.sh self で動き出す）
# =====================================================================
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

net_ok() {   # Docker のイメージ置き場に届くか（401 が返れば届いている）
  [ "$(curl -sS -m 8 -o /dev/null -w '%{http_code}' https://registry-1.docker.io/v2/ 2>/dev/null)" != "000" ]
}

nav2_warn() {
  if pgrep -f 'nav2|slam_toolbox' >/dev/null 2>&1; then
    warn "Nav2 / SLAM が動いています。画面のイメージ作りは CPU を長く使うので、走らせていないときに実行してください"
    confirm "このまま続けますか？" || exit 1
  fi
}

# 初回に DB を作ったら、クラウドから本・貸出・ピンを入れる
first_fill() {
  if cloud_ok; then
    python3 "$HERE/sync.py" sync --from cloud --to self \
      || warn "クラウドから入れるのに失敗しました。ネットがあるときに mode.sh self を実行すれば入ります"
  else
    warn "クラウドにつながらないので、本・ピンは空のままです。ネットがあるときに mode.sh self を実行すれば入ります"
  fi
}

# いまのモードに合わせて、コンテナを動かしたままにするか止めるか
settle() {
  if [ "$(current_mode)" = self ]; then
    ok "いまはセルフホストのモードなので、動かしたままにします"
  else
    dc stop >/dev/null 2>&1
    ok "いまはクラウドのモードなので、コンテナは止めておきます（bash $HERE/mode.sh self で動き出します）"
  fi
}

cmd_all() {
  [ "$(id -u)" -ne 0 ] || die "sudo を付けずに実行してください（必要なところだけ sudo を使います）"
  net_ok || die "インターネットにつながっていません。テザリングなどでつないでから実行してください（初回だけ要ります）"

  step "1/7 Docker（Ubuntu 標準のパッケージだけ）"
  if docker compose version >/dev/null 2>&1 && docker buildx version >/dev/null 2>&1; then
    ok "入っています: $(docker compose version --short 2>/dev/null)"
  else
    sudo apt-get update -q
    sudo apt-get install -y -q docker.io docker-compose-v2 docker-buildx
    sudo systemctl enable --now docker
    ok "入れました: $(docker compose version --short 2>/dev/null)"
  fi

  step "2/7 DB のパスワード（robot/selfhost/.env）"
  if [ -f "$HERE/.env" ]; then
    ok "あります（そのまま使います）"
  else
    ( umask 077
      { echo "# セルフホストの DB のパスワード（setup.sh が作った。共有しない。git には入らない）"
        echo "POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
        echo "AUTHENTICATOR_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
      } > "$HERE/.env" )
    ok "作りました"
  fi

  step "3/7 CPU の上限（Nav2 の取り分を守る）"
  if grep -q '^WEB_CPUS=' "$HERE/.env"; then
    ok "設定済み: $(grep -E '^(DB|REST|WEB|CADDY)_CPUS=' "$HERE/.env" | tr '\n' ' ')"
  elif echo "   （DB のイメージを取ってきて試すので、1〜2 分かかります）" \
       && docker_ run --rm --cpus 0.5 postgres:16 true >/dev/null 2>&1; then
    printf '%s\n' "# コンテナごとの CPU の上限（コア数）。0 にすると上限なし" \
      "DB_CPUS=1.0" "REST_CPUS=0.5" "WEB_CPUS=1.0" "CADDY_CPUS=0.5" >> "$HERE/.env"
    ok "使えます（DB 1.0・画面 1.0・読み書きの窓口 0.5・入口 0.5 コアまで。Pi 5 は 4 コア）"
  else
    printf '%s\n' "# この Pi のカーネルでは CPU の上限が使えなかった（setup.sh が確かめた）ので上限なし" \
      "DB_CPUS=0" "REST_CPUS=0" "WEB_CPUS=0" "CADDY_CPUS=0" >> "$HERE/.env"
    warn "この Pi のカーネルでは CPU の上限が使えません → 上限なしで動かします（負荷は mode.sh status で見られます）"
  fi

  step "4/7 イメージ（取ってくる・画面を作る。初回は数分〜十数分）"
  nav2_warn
  dc pull db rest caddy
  dc build web
  ok "そろいました"

  step "5/7 ロボ側の接続先（robot/.env → .env.cloud / .env.self）"
  if [ ! -f "$ROBOT_DIR/.env.self" ]; then
    cat > "$ROBOT_DIR/.env.self" <<'EOF'
# セルフホスト（Pi の Docker）につなぐ設定。mode.sh self のとき robot/.env がこのファイルを指す
#   鍵は使わない（入口の caddy が外す）ので、SUPABASE_ANON_KEY の値は何でもよい
SUPABASE_URL=http://127.0.0.1
SUPABASE_ANON_KEY=selfhost
EOF
    ok ".env.self を作りました"
  fi
  case "$(current_mode)" in
    self|cloud)
      ok "切り替えられる形になっています（いま: $(current_mode)）" ;;
    plain)
      [ ! -e "$ROBOT_DIR/.env.cloud" ] \
        || die "robot/.env と robot/.env.cloud の両方があります。どちらが正しいか確かめ、片方を消してから再実行してください"
      mv "$ROBOT_DIR/.env" "$ROBOT_DIR/.env.cloud"
      ln -s .env.cloud "$ROBOT_DIR/.env"
      ok "いまの robot/.env を .env.cloud に移し、.env → .env.cloud のリンクにしました（今までどおりクラウドにつながります）" ;;
    *)
      warn "robot/.env がありません。クラウドの接続先は robot/README.md §3-2 の値で robot/.env.cloud に書いてください" ;;
  esac

  step "6/7 起動して確かめる（初回は DB を作るので 1 分ほど）"
  local fresh=0
  docker_ volume inspect "$VOLUME_DB" >/dev/null 2>&1 || fresh=1
  dc up -d
  if wait_ready 240; then
    ok "入口・読み書きの窓口・画面が返事をしました（http://127.0.0.1/）"
  else
    dc ps
    dc logs --tail 40 db rest web caddy || true
    die "起動を確かめられませんでした（上のログを見てください）"
  fi
  if [ "$fresh" = 1 ]; then rm -f "$HERE/.sync_state.json"; first_fill; fi   # 控え（前回両方にあった本）は新しい DB には合わない

  step "7/7 いまのモードに合わせる"
  settle

  echo
  echo "   次は: bash $HERE/mode.sh self    （セルフホストに切り替える）"
  echo "         bash $HERE/mode.sh status  （いまの状態を見る）"
}

cmd_rebuild_web() {
  net_ok || die "インターネットにつながっていません（画面を作るのに要ります）"
  nav2_warn
  step "画面のイメージを作り直す（いまの手元のコード: $(git -C "$REPO_DIR" log -1 --format='%h %s')）"
  dc build web
  if self_running; then
    dc up -d web
    ok "作り直して入れ替えました"
  else
    ok "作り直しました（mode.sh self で動き出します）"
  fi
}

cmd_sql() {
  local file="${1:-}"
  [ -n "$file" ] && [ -f "$file" ] || die "流す SQL のファイルを指定してください（例: setup.sh sql ../sql/04_xxx.sql）"
  local was_running=0
  self_running && was_running=1
  step "Pi の DB に流す: $file"
  dc up -d db
  local t=0
  until dc exec -T db pg_isready -h 127.0.0.1 -U postgres -d bookshelf >/dev/null 2>&1; do
    t=$((t + 2)); [ "$t" -ge 60 ] && die "DB が起動しません"; sleep 2
  done
  dc exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d bookshelf < "$file"
  dc exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d bookshelf < "$HERE/sql/90_grants.sql"
  ok "流しました（権限も付け直し、読み書きの窓口に読み直させました）"
  [ "$was_running" = 1 ] || dc stop db >/dev/null
}

cmd_reset_db() {
  warn "Pi の DB を消して、空から作り直します。★Pi にしか無い貸出の記録やピンは消えます★"
  warn "消したくないものがあるなら、先に mode.sh cloud でクラウドへ送ってください"
  confirm "作り直しますか？" || exit 1
  dc down
  docker_ volume rm "$VOLUME_DB" >/dev/null 2>&1 || true
  rm -f "$HERE/.sync_state.json"     # 「前回両方にあった本」の控え。残すと、空の DB を「全部消された」と見誤るもとになる
  dc up -d
  wait_ready 240 || { dc logs --tail 40 db; die "起動を確かめられませんでした"; }
  ok "作り直しました"
  first_fill
  settle
}

cmd_uninstall() {
  warn "セルフホストのコンテナ・イメージ・Pi の DB の中身をすべて消し、robot/.env を元の普通のファイルに戻します"
  warn "Pi にしか無い貸出の記録やピンがあるなら、先に mode.sh cloud でクラウドへ送ってください"
  confirm "片付けますか？" || exit 1
  dc down -v --rmi all || true
  if [ -L "$ROBOT_DIR/.env" ] && [ -f "$ROBOT_DIR/.env.cloud" ]; then
    rm "$ROBOT_DIR/.env"
    mv "$ROBOT_DIR/.env.cloud" "$ROBOT_DIR/.env"
    ok "robot/.env をクラウドの接続先（普通のファイル）に戻しました"
  fi
  rm -f "$ROBOT_DIR/.env.self" "$HERE/.env" "$HERE/.sync_state.json"
  ok "片付けました"
  echo "   Docker そのものも消すなら: sudo apt purge docker.io docker-compose-v2 docker-buildx"
}

case "${1:-all}" in
  all)          cmd_all ;;
  rebuild-web)  cmd_rebuild_web ;;
  sql)          cmd_sql "${2:-}" ;;
  reset-db)     cmd_reset_db ;;
  uninstall)    cmd_uninstall ;;
  *)            sed -n '2,12p' "$0"; exit 1 ;;
esac
