#!/usr/bin/env bash
# =====================================================================
#  pi_setup.sh ― Pi にロボ側コードを入れる／更新する（何度実行しても安全）
#
#  初回（Pi にまだ何も無いとき）:
#    curl -fsSL https://raw.githubusercontent.com/syuk650220-creator/moving-bookshelf-app/main/robot/pi_setup.sh | bash
#  2 回目以降:
#    bash ~/moving-bookshelf-app/robot/pi_setup.sh
#  main 以外のブランチを使うとき:
#    BRANCH=feature/xxx bash ~/moving-bookshelf-app/robot/pi_setup.sh
#
#  やること
#    1. apt で git / python3-serial / python3-requests を入れる
#    2. ~/moving-bookshelf-app を clone（あれば git pull）
#    3. 古い ~/pi（2026-09-01 に scp した仮置き）を ~/pi_old_YYYYMMDD に退避。
#       ~/pi/.env があれば robot/.env に引き継ぐ（★消しません。移動とコピーだけ★）
#    4. robot/.env が無ければ .env.example から作り、値の入力を促す
#    5. udev の固定名 /dev/mecanum_left /dev/mecanum_right を確認
#    6. test_logic.py で計算部分を検証
# =====================================================================
set -euo pipefail

REPO_URL="https://github.com/syuk650220-creator/moving-bookshelf-app.git"
REPO_DIR="$HOME/moving-bookshelf-app"
ROBOT_DIR="$REPO_DIR/robot"
BRANCH="${BRANCH:-main}"
OLD_PI="$HOME/pi"

step() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '   \033[1;33m★\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- 1
step "1/6 依存パッケージ（apt）"
sudo apt-get install -y -q git python3-serial python3-requests >/dev/null
ok "git / python3-serial / python3-requests"

# ---------------------------------------------------------------- 2
step "2/6 リポジトリ（ブランチ: $BRANCH）"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" fetch -q origin
  if ! git -C "$REPO_DIR" switch -q "$BRANCH" 2>/dev/null; then
    git -C "$REPO_DIR" switch -q -c "$BRANCH" --track "origin/$BRANCH"
  fi
  if ! git -C "$REPO_DIR" pull -q --ff-only; then
    warn "Pi 側で直接編集した変更があるため pull できません。git -C $REPO_DIR status で確認し、必要なら git stash してから再実行"
    exit 1
  fi
  ok "更新しました: $(git -C "$REPO_DIR" log -1 --format='%h %s')"
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
  ok "clone しました: $REPO_DIR"
fi

# ---------------------------------------------------------------- 3
step "3/6 古い ~/pi の退避"
if [ -d "$OLD_PI" ] && [ ! -d "$OLD_PI/.git" ]; then
  if [ -f "$OLD_PI/.env" ] && [ ! -f "$ROBOT_DIR/.env" ]; then
    cp "$OLD_PI/.env" "$ROBOT_DIR/.env"
    chmod 600 "$ROBOT_DIR/.env"
    ok ".env を ~/pi から robot/ に引き継ぎました"
  fi
  DEST="$HOME/pi_old_$(date +%Y%m%d)"
  n=1
  while [ -e "$DEST" ]; do DEST="$HOME/pi_old_$(date +%Y%m%d)_$n"; n=$((n + 1)); done
  mv "$OLD_PI" "$DEST"
  ok "~/pi → $DEST（消していません。要らなければ rm -rf $DEST）"
elif [ -d "$OLD_PI" ]; then
  warn "~/pi は git 管理のフォルダなので触りませんでした"
else
  ok "古い ~/pi はありません"
fi

# ---------------------------------------------------------------- 4
step "4/6 接続情報 robot/.env"
if [ -f "$ROBOT_DIR/.env" ]; then
  if grep -q 'xxxxxxxxxxxx' "$ROBOT_DIR/.env"; then
    warn "robot/.env がひな形のままです。nano $ROBOT_DIR/.env で SUPABASE_URL と SUPABASE_ANON_KEY を書いてください"
  else
    ok "robot/.env あり"
  fi
else
  cp "$ROBOT_DIR/.env.example" "$ROBOT_DIR/.env"
  chmod 600 "$ROBOT_DIR/.env"
  warn "robot/.env を作りました。nano $ROBOT_DIR/.env で SUPABASE_URL と SUPABASE_ANON_KEY を書いてください（値は PC の .env.local と同じ）"
fi

# ---------------------------------------------------------------- 5
step "5/6 udev の固定名"
if [ -e /dev/mecanum_left ] && [ -e /dev/mecanum_right ]; then
  ok "/dev/mecanum_left → $(readlink /dev/mecanum_left)   /dev/mecanum_right → $(readlink /dev/mecanum_right)"
elif [ -f /etc/udev/rules.d/99-mecanum.rules ]; then
  warn "ルールはありますが Arduino が見えません（USB を 2 本とも挿してあるか。挿した直後は数秒待つ）"
else
  warn "udev ルールがありません。robot/README.md §3-3 の手順で作ってください"
fi

# ---------------------------------------------------------------- 6
step "6/6 計算部分の検証（実機なし）"
( cd "$ROBOT_DIR" && python3 -B test_logic.py | tail -3 )

printf '\n次にやること:  cd %s\n' "$ROBOT_DIR"
printf '  段階1 手動操作 : python3 manual_control.py --serial\n'
printf '  段階2 呼出フロー: python3 bookshelf_bridge.py --live --simulate 5\n'
printf '  （両方を同時に動かさないこと）\n'
