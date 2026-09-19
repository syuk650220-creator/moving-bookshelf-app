# robot/ — ロボ側コード（Raspberry Pi 5）

アプリ（Supabase）・ラズパイ・Arduino をつなぐコード一式です。

> **★このフォルダが正本です（2026-09-03〜）★**
> それまで OneDrive の `ものづくりゼミ/pi/` に仮置きしていた走行系ツールをここへ統合しました。
> Pi へは **`git clone` / `git pull` で配ります**（scp での配布はやめました）。
> 設計の考え方は OneDrive の `ものづくりゼミ/02_設計/Pi制御ブリッジ設計メモ_v0_4.md`、
> Arduino との通信仕様は同 `simulink/arduino/README_raspberrypi.md`（v2）を参照してください。

---

## 0. 機体の構成（この機体の確定値）

| | 値 |
|---|---|
| ロボ側の計算機 | Raspberry Pi 5 ／ Ubuntu Server 24.04 ＋ ROS 2 Jazzy（ros-base） |
| Pi のアドレス | `ssh cse_c@cse-c-ubuntu2404.local`（IP は DHCP で変わる。`ROS_DOMAIN_ID=7`） |
| 走行制御 | Arduino Nano 33 IoT ×2（左＝FL/RL、右＝FR/RR）。Simulink 製ファーム **v2**（テレメトリ 24 バイト・内蔵 IMU LSM6DS3・暴走防止）が書き込み済み。ソースは OneDrive の `ものづくりゼミ/simulink/arduino/` |
| Pi ↔ Arduino | USB シリアル 115200。指令 7 バイトを 25 Hz（`robot_params.SEND_PERIOD`。50 Hz にすると片側が遅れる）で両基板へ、報告 24 バイトが 10 Hz で各基板から |
| 基板の固定名 | `/dev/mecanum_left`（シリアル `24B3C5715030574B412E3120FF18212B`）／ `/dev/mecanum_right`（`B8A2ED6B5030534E512E3120FF02362A`）。udev で固定（§3-3） |
| モータドライバ | TB67H420FTG ×2 |
| 車輪 | OSOYOO φ80 mm メカナム ×4。ホイールベース 95 mm・トレッド 175 mm（`robot_params.py`） |
| LiDAR | RPLIDAR A1M8（`ros-jazzy-rplidar-ros`、`/scan`） |
| 速度の上限 | 会場運用 0.25 m/s ＝ 60 rpm。最終進入 0.10〜0.15 m/s ＝ 24〜36 rpm |

---

## 1. つなぎ目（疎結合の原則）

アプリとロボは直接やり取りせず、Supabase のテーブルを介してつながります。経路は 2 本あります。

```
【呼出】 アプリ S-4 ──insert──> robot_calls ──> bookshelf_bridge.py ──> Nav2 ──> mecanum_node.py ──> Arduino ×2
         アプリ     <──select── robot_status <── bookshelf_bridge.py（状態・現在地・進行状況）
【手動】 アプリ /admin ──update──> robot_manual <──manual_poll()── manual_control.py --serial ──> Arduino ×2
【ピン】 アプリ /admin/pins ──update──> stop_points <──upsert── pin_tool.py（TF か RViz のクリックから）
```

- `robot_calls.status` は queued → moving → arrived → done。moving と arrived はブリッジが進め、
  **arrived → done はアプリ S-4 の「本を取得した」ボタン**で進みます（ゼミ決定 2026-09-01。ボタンの文言は 2026-09-18 に変更）。
- `robot_status.state` は **idle → localizing（自己位置推定中）→ moving → arrived → returning（本棚の場所へ帰還中）→ idle**。
  常に 1 行（id=1）。書くのはブリッジだけです。走行中は現在地（`pose_x/y/theta`）と一言（`detail`）も 1 秒ごとに書きます。
- `stop_points` は **id=0 が「本棚の場所」（`kind='home'`）、id≥1 が席**。本棚の場所は受取後に帰る先で、
  呼出のたびに AMCL の初期位置にもなります（§5-4）。アプリの席一覧には id≥1 だけが出ます。
- 上の 3 つは `sql/03_pins_home_pose.sql` で入ります（§3-2）。未適用でもブリッジは動きますが、localizing と現在地は書きません。
- **ブリッジと手動操作を同時に実機へつながないこと**（Arduino への指令の取り合いになります）。

---

## 2. 入っているもの

| ファイル | 役割 |
|---|---|
| `bookshelf_bridge.py` | ブリッジ本体（Issue #8 / #9 / #14 ／ 段階3）。`robot_calls` を 1 秒ポーリング（`--realtime` で購読も併用）し、自己位置推定（本棚の場所を初期位置に）→ 席へ走行 → 受取待ち → 本棚の場所へ帰還、と status を進める。`--nav2` で実走 |
| `pin_tool.py` | ★ピン建て★ 席1〜3 と本棚の場所（id=0）の map 座標を `stop_points` に登録。ロボを置いた場所（TF `map→base_link` の中央値）か RViz のクリック（2D Goal Pose ／ Publish Point）から。ROS 2 が無くても `--set` で数値登録できる |
| `manual_control.py` | 管理者画面（/admin）ラジコンモードの受け側。`robot_manual` を 0.2 秒ポーリングし、指令が 1.2 秒更新されなければ停止（デッドマン）。`--serial` で実機接続 |
| `mecanum_serial.py` | Arduino 2 枚との USB シリアル通信ライブラリ（送信ループ・テレメトリ＋IMU・量子化 `Quantizer`・暴走防止の検出） |
| `mecanum_node.py` | ROS 2 ノード。`/cmd_vel` → パケット、テレメトリ → `/odom` と TF |
| `pi_controller.py` | 手動操作と診断（`--identify` `--sweep` `--lowspeed`）。ROS 2 不要。**実機を初めて動かすときはまずこれ** |
| `robot_params.py` | ★寸法・速度の設定はここだけ★ 未較正の値に TODO |
| `calib_monitor.py` | 較正用。`/odom` を購読して累積の移動量・回転角を表示 |
| `test_logic.py` | 実機なしで計算・ブリッジの状態機械・Nav2 設定の重ね合わせを検証（111 項目）。pyserial も requests も不要 |
| `nav2/nav2_params_差分.yaml` | Nav2 の設定のうち既定値から変える分（根拠つき） |
| `nav2/make_nav2_params.py` | ★段階3★ Pi に入っている標準の `nav2_params.yaml` に上の差分と機体の半径を重ねて `~/nav2/nav2_params.yaml` を作る。変更点を一覧表示し、書いたファイルを読み直して検算する |
| `nav2/robot_tf_launch.py` | ★段階3★ 実走のときの静的 TF（`base_footprint→base_link→laser`）。`temp_tf_launch.py` のかわりに使う（§7） |
| `sql/01_realtime_と_updated_at.sql` | schema.sql に足りない 2 つ（Realtime publication ／ updated_at 自動更新トリガ）。SQL Editor で 1 回実行 |
| `sql/02_manual_control.sql` | 手動操作用の `robot_manual` テーブル・RPC・ビュー。SQL Editor で 1 回実行 |
| `sql/03_pins_home_pose.sql` | ★段階3★ `stop_points.kind`（本棚の場所＝id 0）と insert/update ポリシー、`robot_status` の `localizing` と現在地（`pose_*`・`detail`）。SQL Editor で 1 回実行 |
| `.env.example` | Supabase 接続情報のひな形。`.env` にコピーして値を入れる（`.env` はコミットされない） |
| `requirements.txt` | PC／venv 用の参考。**Pi では apt を使う** |

---

## 3. Pi へのインストール

### 3-1 コードを置く（1 行で済みます）

Pi で次を実行すると、apt の導入・clone（2 回目以降は pull）・古い `~/pi` の退避・`.env` の用意・udev の確認・`test_logic.py` までまとめてやります。
**何度実行しても安全**です（消す操作はありません）。

```bash
curl -fsSL https://raw.githubusercontent.com/syuk650220-creator/moving-bookshelf-app/main/robot/pi_setup.sh | bash
```

2 回目以降は `bash ~/moving-bookshelf-app/robot/pi_setup.sh` です。main 以外のブランチを試すときは `BRANCH=feature/xxx` を頭に付けます。

> **★Pi の中の何が変わるのか★** 変わるのは **Python ファイルの置き場所だけ**です。
> 9/1 に scp した `~/pi/` は git とつながっていない「コピー」なので、更新が届きません。
> そこで同じコード（＋受取ボタン待ち対応の新しいブリッジ）を GitHub から `~/moving-bookshelf-app/` に置き、
> `~/pi/` は `~/pi_old_日付/` に改名して脇へどけます（**削除はしません**。`~/pi/.env` があれば `robot/.env` に引き継ぎます）。
> ROS 2・slam_toolbox の設定・udev ルール・地図・Arduino の書き込みなど、Pi のほかの環境はいっさい触りません。

手でやる場合は次のとおりです。

```bash
sudo apt install -y git python3-serial python3-requests
cd ~ && git clone https://github.com/syuk650220-creator/moving-bookshelf-app.git
cd ~/moving-bookshelf-app/robot
mv ~/pi ~/pi_old_20260901      # 古いコピーを脇へ（消さない）
```

以後の更新は `cd ~/moving-bookshelf-app && git pull` です。

> **★`pip install` は使えません★**
> Ubuntu 24.04 は PEP 668 により、システムの Python への `pip install` を拒否します
> （`error: externally-managed-environment`）。
> また **ROS 2 のノードはシステムの Python で動く**ため、venv に入れると `rclpy` と一緒に import できなくなります。**apt が正解です。**
> Realtime 用の `supabase` パッケージも Pi には入れません。ポーリングだけで運用できる設計です。

### 3-2 接続情報

`pi_setup.sh` が `.env` のひな形まで作ります。このプロジェクトの値は次のとおりで、そのまま貼り付けて Enter すれば書き込まれます
（PC の `.env.local` と同じ。publishable キーは公開してよいもので、[環境構築ガイド](../docs/開発の手引き/環境構築ガイド.md) にも同じ値があります）。

```bash
cat > ~/moving-bookshelf-app/robot/.env <<'EOF'
SUPABASE_URL=https://zljswppciglhvwjyquow.supabase.co
SUPABASE_ANON_KEY=sb_publishable_Ebl1Rfth5d2_Kikh6nK2wA_PEM75NnJ
EOF
```

**service_role キーは絶対に Pi に置かないこと。** anon（publishable）キーで足ります。
Supabase のプロジェクトを作り直したときは、ダッシュボードの Settings → API の値に差し替えてください。

書けたかは、ブリッジを DB を書き換えないモードで起動して確かめます（`現在の robot_status: … 'idle'` と出れば接続できています。Ctrl-C で抜ける）。

```bash
cd ~/moving-bookshelf-app/robot && python3 bookshelf_bridge.py
```

Supabase の SQL Editor で `sql/01_realtime_と_updated_at.sql`・`sql/02_manual_control.sql`・`sql/03_pins_home_pose.sql` を 1 回ずつ実行しておきます
（何度実行しても壊れないように書いてあります。済んでいれば不要）。
03 を流すと `stop_points` に本棚の場所（id=0）の行ができ、`robot_status` に `localizing` と現在地の列が増えます。
ブリッジは起動時に「スキーマ : 段階3（sql/03 適用済み）」と表示して判定結果を教えてくれます。

### 3-3 左右の基板を固定する（udev・初回のみ）

COM／デバイス名は挿し直すと変わります。**番号を覚えさせないでください。**
どちらが左かは `python3 pi_controller.py --identify` で目視確認できます（§6-①）。

シリアル番号は次で調べられます（2 枚ぶんまとめて出ます）。

```bash
for p in /dev/ttyACM0 /dev/ttyACM1; do echo "== $p"; udevadm info -q property -n $p | grep -E '^ID_(SERIAL_SHORT|VENDOR_ID|MODEL_ID)='; done
```

この機体の値（2026-09-01 調査）は §0 のとおりです。ルールの作成は 1 コマンドで済みます。

```bash
printf 'SUBSYSTEM=="tty", ATTRS{idVendor}=="2341", ATTRS{serial}=="24B3C5715030574B412E3120FF18212B", SYMLINK+="mecanum_left"\nSUBSYSTEM=="tty", ATTRS{idVendor}=="2341", ATTRS{serial}=="B8A2ED6B5030534E512E3120FF02362A", SYMLINK+="mecanum_right"\n' | sudo tee /etc/udev/rules.d/99-mecanum.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
ls -l /dev/mecanum_*        # ← シンボリックリンクが 2 本出れば成功
```

> `idProduct` は条件に入れていません。Nano 33 IoT はブートローダ状態だと PID が `8057` → `0057` に変わるため、
> 固定条件にすると書き込み直後に外れることがあります。**シリアル番号だけで一意**なので、これで十分です。
> **基板を交換したら**、上の `for` ループで新しい番号を調べて書き換えてください。

### 3-4 動作の確認（実機なしでできる）

```bash
python3 test_logic.py      # 「すべて成功」が出ること（111 項目）
python3 robot_params.py    # φ80mm 版の換算表（60 rpm = 0.251 m/s）
```

---

## 4. 手動操作（アプリ ⇄ 実機の最初の連携）

アプリの `/admin`（暗証番号つき）からロボを十字キーで動かせます。**地図も Nav2 も要りません。**
アプリ → Supabase → Pi → Arduino の経路が全部つながっていることを、最初にここで確かめます。

```bash
cd ~/moving-bookshelf-app/robot
python3 manual_control.py            # PC: 受信した指令をログ表示（実機なし）
python3 manual_control.py --serial   # Pi: 実機（Arduino 2 枚）を動かす
```

合格の目印:

1. 画面の「Pi 受信スクリプト」が **● オンライン** になる（`pi_seen_at` が更新されている）
2. 「手動モードにする」→ ↑ を押している間だけ実機が前進し、離すと止まる
3. ブラウザを閉じても 1.2 秒以内に止まる（デッドマン）

安全設計: アプリはボタンを押している間だけ指令を更新し続け、Pi 側は
「指令が 1.2 秒更新されなければ停止」（経過秒は DB の時計で判定＝クロックずれ無関係）。
実機側はさらに `MecanumLink` の 0.5 秒タイムアウトと Arduino のウォッチドッグが控える三重構えです。

> ★`--serial` は同じフォルダの `mecanum_serial.py` と `robot_params.py` を使います。
> 別の場所へコピーして使うときは、この 2 つも一緒に置いてください。

---

## 5. 呼出フロー（ブリッジ）

### 5-1 PC で試す（実機なし・DB を書き換えない）

```bash
cd robot
python -m venv .venv
.venv\Scripts\activate            # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # SUPABASE_URL と SUPABASE_ANON_KEY を記入
python bookshelf_bridge.py        # ★DB は書き換えない dry-run。まずはこれ★
```

この状態でアプリの S-4（または Supabase の Table Editor）から `robot_calls` に 1 行 insert すると、
1 秒以内に検知してログと Nav2 ゴール（クォータニオン変換済み）を表示します。

```
[呼出] id=3f2a91c4…  book=7bd0…  seat=2  requested_by=guest
    [席] 席2  (1.000, 0.000) theta=0.000 rad
    [db] robot_calls 3f2a91c4… → moving（dry-run なので書き込みません）
    [nav] ゴール送出（ふり）: (1.000, 0.000) theta=0.000rad  qz=0.0000 qw=1.0000
```

### 5-2 Pi で、走行なしで status を通す

```bash
python3 bookshelf_bridge.py --live --simulate 5   # 走行の代わりに 5 秒待って「到着」扱い
```

アプリの S-4 で呼ぶと、ロボの状態が 自己位置推定中（1 秒）→ 移動中（残り x m）→ 到着（受取待ち）と進みます。
**「本を取得した」を押すと done になり、ロボは 本棚へ帰還中 → 待機中 と進んでから次の呼出へ移ります。**
`--simulate` でも現在地を直線補間で動かすので、`/admin/pins` の地図で「動いたふり」を確認できます。
押し忘れ対策に `--arrive-timeout 120` を付けると 120 秒で自動 done にできます（既定は無効）。

### 5-3 Pi で実走する（Nav2）

前提: 地図が保存済み、`stop_points` の席と本棚の場所が map 座標の実測値（§5-4）、`mecanum_node.py` と Nav2 が起動済み（§7）、
**ロボが本棚の場所（id=0 のピン）に、登録したときと同じ向きで置いてある**こと。

```bash
python3 bookshelf_bridge.py --live --nav2
```

起動すると amcl と bt_navigator が active になるのを待ち、**まず本棚の場所で自己位置推定を 1 回**行います（母艦の RViz でレーザーの点が地図の壁に重なっているかを、走り出す前にここで確認）。そのあとは呼出が来るたびに次の順で動きます。

| 順 | ブリッジがすること | `robot_status.state` |
|---|---|---|
| ① | 本棚の場所の座標を AMCL の初期位置として `/initialpose` に送り、`/amcl_pose` が返るのを待って 2 秒落ち着かせる（★ロボが本棚の場所に居るときだけ★。走行失敗などで居ないときは送らず、AMCL の追跡をそのまま使う） | `localizing` |
| ② | 席の座標へ `goToPose`。1 秒ごとに現在地と「残り x m」を書く | `moving` |
| ③ | 到着。アプリの「本を取得した」を待つ | `arrived` |
| ④ | 本棚の場所へ `goToPose` | `returning` |
| ⑤ | 帰り着いたら待機（次の呼出で①から） | `idle` |

- 1 回の走行は `--nav-timeout`（既定 180 秒）で打ち切られ、失敗時は canceled になります。帰還に失敗したときは呼出は done のまま idle に戻り、`detail` に「帰還に失敗」と出ます。**手で本棚の場所へ戻してブリッジを再起動**してください（再起動＝「本棚の場所に居る」前提に戻る）。
- RViz の 2D Pose Estimate で人が初期位置を与える運用なら `--no-localize`。受取後に席で待たせるなら `--no-return-home`。
- 初期位置のばらつきは `--init-sigma-xy 0.15`（m）`--init-sigma-yaw 0.17`（rad ≒ 10°）。本棚の場所に置く精度に合わせます。
- `nav2_simple_commander` の `waitUntilNav2Active()` は**使っていません**。Jazzy では初期位置が未設定だと地図原点 (0,0,0) を勝手に送るためです（②完全ガイド E3 の注記）。
- Ctrl-C で止めると走行を中止（`cancelTask`）し、処理中の呼出を canceled、状態を idle に戻してから終了します。

> ★`--nav2` の経路は 2026-09-18 時点で実機未検証です★ 実機なしで検証できる部分（状態機械・DB への書き込み・失敗時の扱い）は
> `test_logic.py` の「Bridge」の項で通してあります。初回は `--nav-timeout 60` など短めで、非常停止に手を添えて試してください。

Realtime も試すなら `--realtime` を足します。**動かなくても構いません** ― ポーリング（1 秒）だけで運用できる設計です。
会場の Wi-Fi は不通前提という裁定が出ているため、意図的にこうしてあります。

### 5-4 ピン建て（席1〜3 と本棚の場所の座標を登録する）

ピンは 4 つです。**本棚の場所（id=0）は床にテープで印を付け、毎回同じ向きに置く**こと（ここが帰る先であり、自己位置推定の初期位置です）。

| id | kind | 意味 | theta の意味 |
|---|---|---|---|
| 0 | home | 本棚の場所（定位置） | 置いたときにロボが向く向き |
| 1〜3 | seat | 席（アプリの「届け先の席」） | 席に着いたときロボが向く向き |

登録の仕方は 3 通り。どれも `stop_points` に upsert します（sql/03 の insert/update ポリシーが必要）。

```bash
cd ~/moving-bookshelf-app/robot
python3 pin_tool.py                     # 対話モード（SLAM か AMCL が動いている Pi／母艦で）
python3 pin_tool.py --list              # いまのピンを見る
python3 pin_tool.py --set 1 1.85 0.42 1.57 --label 席1    # 数値を直接（ROS 2 不要・PC からでも）
```

- **A. ロボを置いた場所を登録（推奨）** … 対話モードで番号 → `c`。裁定書 §5 の手順どおり、席へ 0.15 m/s 以下で進入 → ±40° 首振り → 2〜3 秒静止 → `c`。TF `map→base_link` を 5 回読んで中央値を採ります。
- **B. RViz でクリック** … 番号 → `r` → RViz の **2D Goal Pose**（位置＋向き）か **Publish Point**（位置のみ）。★Nav2 起動中に 2D Goal Pose を押すとロボが走り出すので、ピン建ては SLAM 中か Nav2 停止中に★
- **C. アプリの管理者画面 `/admin/pins`** … 地図の上でピンとロボの現在地を見ながら数値を直す。本棚の場所（id=0）の追加もここでできる。

採ったあとは `/admin/pins` の地図で 4 つの位置関係が部屋と合っているか確かめ、AMCL を起動して席1 へ 1 本試走してテープからのズレを実測します（±10 cm 以内で合格。裁定書 §5）。
REST で書けないときは同じ内容の SQL を表示するので、SQL Editor に貼ってください。

---

## 6. 実機の診断・較正（pi_controller / calib_monitor）

**車輪を浮かせた状態から始めてください。** `pi_controller.py` と `mecanum_node.py` は同じポートを開くので同時には動きません。

### ① どちらの基板が左か確かめる

```bash
python3 pi_controller.py --identify
```

片方のポートにだけ低速で前進を送るので、**どちらの車輪が回ったか目視**します。確認できたら §3-3 の udev ルールで固定します。

### ② 手で動かす

```bash
python3 pi_controller.py
```

W/S で前後、A/D で横、Q/E で回転、X または Space で停止、`+`/`-` で速度、ESC で終了。
**4 輪の rpm が画面に出て、W で 4 つとも正の値になれば成功**です。
画面の右端に `gz`（IMU の角速度 Z、左／右）も出ます。**Q/E で回すと符号つきで動き、左右がほぼ同じ値なら IMU も正常**です。

> **rpm がギザギザなのは正常です。** フィルタ前の生の値を送っているためです。見るべきは `u`（PWM 指令）が静かなことです。

> **★指令中なのに `u=0`・`rpm=0` の車輪が続くと「★暴走防止?★」と出ます★**
> v2 の Arduino がその車輪を止めてラッチしています。**X（停止）で解除**し、配線・障害物・タイヤの拘束を直してから動かし直してください（仕様書 §6.2）。

### ③ 何 rpm から回り始めるか（MIN_RPM）

```bash
python3 pi_controller.py --sweep
```

指令 5→45 rpm を順に試し、**指令の 8 割以上が実際に出ている最小の rpm** を教えてくれます。
2026-09-01 の実測では全域で誤差 1% 以内でした。無負荷の値なので余裕を見て `MIN_RPM = 10` にしてあります。

### ④ 最終進入速度でのばらつき（★±10cm に効く★）

```bash
python3 pi_controller.py --lowspeed 24 --seconds 10
```

エンコーダは 1 カウント ≒ 2.01 rpm。**最終進入の 24 rpm はその約 8%** で、いちばん精度が欲しい場面がいちばん測定が粗い領域です。
「ばらつき ÷ 分解能」が **1 前後なら正常**、**2 以上なら制御が量子化を増幅**しています
（その場合は Simulink 側の `Kp` を下げるか、FRIT でゲインを決め直します）。

### ⑤ 床に置いての較正（`robot_params.py` の TODO を埋める）

`mecanum_node.py` と `calib_monitor.py` を別の窓で動かし、3 つの値を順に合わせます。
`calib_monitor.py` は起動した瞬間を原点にして、累積の移動量と回転角（畳まれない）を表示し続けます。

| 直す値 | やること | 式 |
|---|---|---|
| `WHEEL_RADIUS` | 前進 0.15 m/s で 3 m ほど走らせ、実距離をメジャーで測る | 新 = 旧 × 実測 ÷ odom の x |
| `WHEEL_GEOM_L` | 床に基準線を引き、その場旋回 1.0 rad/s で 5 回転ぴったりで止める | 新 = 旧 × odom の回転 ÷ 1800 |
| `K_STRAFE` | `-p use_vy:=true` で起動し、左横 0.15 m/s で走らせて実距離を測る | 新 = 旧 × 実測 ÷ odom の y |

`WHEEL_GEOM_L` だけ式の向きが逆です（odom を割る値のため）。`K_STRAFE` は床材で変わるので、会場でも測り直してください。

```bash
# 窓①  python3 mecanum_node.py
# 窓②  python3 calib_monitor.py
# 窓③  ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.15}}"
```

---

## 7. ROS 2 ノードと Nav2

ラズパイの上で ROS 2 環境を source してから実行します。

```bash
python3 mecanum_node.py
```

別の端末から動作確認します。

```bash
ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1}}"
ros2 topic echo /odom
```

- `/odom` … 4 輪の実測 rpm から積分した自己位置
- `/wheel_rpm` … 4 輪の生の rpm（FL, RL, FR, RR）。採寸と量子化の確認用
- `/tf` … `odom` → `base_link`

> **★`robot_localization`（EKF）を動かすときは `publish_tf:=false` にしてください★**
> EKF も `odom → base_link` を出すので、両方が出すと TF が二重になって壊れます。

主なパラメータ:

```bash
python3 mecanum_node.py --ros-args -p publish_tf:=false -p use_vy:=false -p max_rpm:=60.0
```

Nav2 の設定は [`nav2/nav2_params_差分.yaml`](nav2/nav2_params_差分.yaml) にあります。
**既定値から変えるところだけ**を根拠つきで書いてあります。反映は手作業ではなく **`make_nav2_params.py`** で行います（Pi に入っている標準ファイルに差分を重ねて `~/nav2/nav2_params.yaml` を作る。何度実行しても安全で、前のファイルは `.bak_日時` に残ります）。

```bash
cd ~/moving-bookshelf-app/robot/nav2
python3 make_nav2_params.py                          # 何を変えるかを見るだけ（書かない）
python3 make_nav2_params.py --write                  # ~/nav2/nav2_params.yaml を作る
python3 make_nav2_params.py --write --robot-radius 0.25    # 機体の半径 [m]（中心からいちばん遠い角まで＋0.02）
```

`behavior_server`（後退・その場旋回のリカバリを外す）は既定では重ねません。ビヘイビアツリーの XML からも Spin / BackUp を外すのが条件で、片方だけ変えると `bt_navigator` が起動に失敗するためです（XML を直したら `--with-behavior-server`）。
差分ファイルの値を変えたら、もう一度 `--write` で作り直します（`~/nav2/nav2_params.yaml` を直接編集しない）。

**`--robot-radius` の目安（2026-09-19・Fusion 360 の「全体像 v2」を計測）**

この機体は「その場で回ってから直進」するので、回るときに振り回す円＝**回転の中心（4 輪の中心）からいちばん遠い角まで**が必要な半径です。
上物の外形は 幅 210（2 階の天板）× 奥行き 303（本棚の後端）× 高さ 208 mm。実機のホイール外側どうしは 215 mm（トレッド 175 ＋ 幅 40）で、奥行き方向にはホイールははみ出しません。

| ホイール（回転の中心）の位置 | いちばん遠い角 | 距離 | 指定値（＋2 cm） |
|---|---|---|---|
| 300 mm の 1 階天板の真ん中（前端から 150 mm） | 2 階天板の前の角 | 183 mm | `0.21` |
| 前寄り（旧シャーシ v9 と同じ、前端から 100 mm） | 本棚の後ろの角 | 218 mm | `0.24` |

★未確定★ 「1 階天板の前端（基板側）から、前後の車軸の中間まで」の実測がまだです（全体像 v2 にシャーシが入っていないため CAD からは分からない）。
**測るまでは安全側の `0.24`** を使います（通路は最低 50 cm ほど必要）。測ったら上の表で決め直して `--write` し直してください。
とくに `robot_model_type: "nav2_amcl::OmniMotionModel"` は、入れないと横移動のたびに自己位置が破綻します。

実走の起動順（目安）: LiDAR → `mecanum_node.py`（odom→base_footprint の TF） → `nav2/robot_tf_launch.py`（残りの静的 TF） → Nav2 bringup（地図＋AMCL） → 母艦の RViz（監視） → `bookshelf_bridge.py --live --nav2`（初期位置は本棚の場所のピンから自動。§5-3）。

> ★TF の鎖は地図づくりのときと同じ `map → odom → base_footprint → base_link → laser` にすること★
> 地図づくりで使った `~/bringup/temp_tf_launch.py` は `odom→base_footprint` まで「動かない仮の TF」で出します。
> 実走ではそこを車輪オドメトリが出すので、**`temp_tf_launch.py` は止めて**、次の 2 つに置き換えます（両方動かすと `base_footprint` の親が 2 つになって TF が壊れます）。
>
> ```bash
> python3 mecanum_node.py --ros-args -p base_frame:=base_footprint                      # odom → base_footprint
> ros2 launch ~/moving-bookshelf-app/robot/nav2/robot_tf_launch.py laser_x:=0.10 laser_z:=0.20   # base_footprint → base_link → laser
> ```
>
> `laser_x` / `laser_z` は **`temp_tf_launch.py` と同じ値**にします（`grep -n -E "'--(x|y|z|yaw)'" ~/bringup/temp_tf_launch.py` で確認。違うと地図と `/scan` がずれます）。
> 鎖の形が同じなので、Nav2 標準の `nav2_params.yaml` のフレーム名（amcl は `base_footprint`、costmap は `base_link`）は書き換え不要で、`pin_tool.py` の既定（`base_link`）も地図づくり・実走の両方で使えます。
> 当日の窓の構成は OneDrive の `03_ガイド・解説/08_アプリ連携_段階3_マッピングから呼出・帰還まで_v1.html`。

### まだ書いていないもの

| タスク | 内容 |
|---|---|
| C2 | LED（NeoPixel）演出。到着で `books.shelf_level` の段を点灯、done で消灯 |
| A6 | 内蔵 IMU を `/imu`（`sensor_msgs/Imu`）で配信し EKF に角速度 Z を渡す。値は `Telemetry.gyro` と `MecanumLink.yaw_rate()` に来ている |
| D1 | 車輪ごとの目標 rpm を送る指令拡張（Arduino 側と同時に変更。設計メモ §8） |

---

## 8. 困ったとき

| 症状 | 原因 |
|---|---|
| ポートが見つからない | 起動直後はまだ現れません（自動でリトライします）。Arduino IDE / MATLAB がポートを掴んでいないか確認 |
| まったく動かない | 指令を送り続けているか。連番が変わっているか。検算に `0x5A` を混ぜているか |
| その場で回ってしまう | 片方の基板にしか届いていない（`--identify` の直後はこれが正常） |
| 小刻みに振動する | 検算の `0x5A` 忘れ。全 0 が「停止指令」として通っています |
| テレメトリが読めない | `readline()` を使っている。バイト数で読むこと（`read_frame` 参照） |
| 起動時に「フレーム長が合いません」と出る | Arduino が v1（12 バイト）のまま。Arduino 担当に v2 の書き込みを依頼 |
| 一部の車輪だけ応答しない（`u=0`・`rpm=0` のまま） | **暴走防止のラッチ**（v2）。X（停止）／`cmd_vel=0` で解除。原因（配線・障害物・タイヤの拘束）を直してから動かし直す |
| 片方の基板の rpm が 0 のまま | その基板の USB が挿さっていない。**2 枚とも挿す**（エンコーダの電源を隣の基板から取っているため、片方だけだと暴走する） |
| 前進で 4 輪の符号がバラバラ | Arduino 側の `MOTOR_DIR`／`MOTOR_INV_*` の問題。Arduino 担当へ |
| /admin で「Pi 受信スクリプト オフライン」 | `manual_control.py` が動いていない、または `.env` の URL／キーが違う |
| ボタンを押しても実機が動かない（オンライン表示はある） | `--serial` を付けていない。またはブリッジの `--nav2` と取り合っている |
| Realtime が飛んでこない | `sql/01_realtime_と_updated_at.sql` を実行していない。Pi では `--realtime` を付けずポーリングで運用 |
| ブリッジが 401 を返す | キーが違う。`.env` を確認 |
| 呼出が queued のまま進まない | ブリッジが `--live` なし（dry-run）で動いている |
| 到着後ずっと待っている | S-4 の「受け取った」が押されていない。`--arrive-timeout` で自動 done にできる |
| ノードが「送信が滞っています」と出す | CPU が詰まっている。0.5 秒でウォッチドッグが効いて機体が止まります |
| RViz で機体が 2 重に見える／TF が喧嘩する | `publish_tf` が EKF と両方 true になっている |
| 横移動すると自己位置が飛ぶ | AMCL が `OmniMotionModel` になっていない（既定は差動用） |
| 目標付近で行ったり来たりする | `stateful: true` になっていない |
| 唸るだけで進まない | `min_x_velocity_threshold` が `MIN_RPM` 相当（0.04）より小さい |
| Pi 上で `sed -i` が Permission denied | scp 時代の名残で `~/pi` が読み取り専用。`git clone` した `~/moving-bookshelf-app` を使う |
| **片側の車輪だけ 0.3 秒ほど遅れて動き出す** | `robot_params.SEND_PERIOD` が 0.02（50 Hz）になっている。Arduino の受信ブロックは Ts=20 ms ごとに 1 パケットしか取り出さないので、同じ周期で送ると取りこぼしがバッファに溜まって片側だけ遅れる。**0.04（25 Hz）が正**（2026-09-03 実機で確認） |
| ブリッジ起動時に「★sql/03 未適用★」と出る | `sql/03_pins_home_pose.sql` を SQL Editor で実行していない。動くが localizing と現在地は書けない |
| 「ホーム : ★未登録★」と出る／帰還しない | `stop_points` に id=0（kind='home'）が無い。`pin_tool.py` か `/admin/pins` の「＋ 本棚の場所」で登録 |
| 呼ぶとすぐ canceled になり detail が「自己位置推定に失敗」 | 初期位置を送っても `/amcl_pose` が来ない。AMCL が `/scan` と TF（`map→odom→base_link→laser`）を受け取れているか。`ros2 topic echo /amcl_pose` |
| 初期位置を与えた直後、RViz で粒子が壁からずれている | ロボが本棚の場所のピンと違う位置・向きに置かれている。テープの印に合わせ直してブリッジを再起動。または `--no-localize` で RViz から与える |
| 席に着くたび少しずつずれる／帰還後に向きが違う | 帰還は `xy_goal_tolerance`（8 cm）の精度で止まる。次の呼出で初期位置をピンに戻すので大きくは溜まらないが、気になるなら `--init-sigma-xy` を大きくして AMCL に任せる |
| 「帰還に失敗」と出て idle になった | 帰り道で詰まった。手で本棚の場所へ戻し、ブリッジを再起動（再起動で「本棚の場所に居る」前提に戻る） |
| `/admin/pins` で保存すると「書き込めません」 | `stop_points` の insert/update ポリシーが無い。`sql/03` を実行 |
| `pin_tool.py` で `c` を押しても TF が読めない | SLAM か AMCL が動いていない、またはフレーム名が違う（暫定 TF 構成なら `--base-frame base_footprint`） |
| `pin_tool.py` で `r` を押したのに何も来ない | RViz の Fixed Frame が map でない／ROS_DOMAIN_ID が違う（7）。クリックのフレームが map 以外だと拒否する |

より詳しい症状表は Arduino の通信仕様書 §11（OneDrive `simulink/arduino/README_raspberrypi.md`）にあります。

---

## 9. 安全

- **非常停止はモータ電源を物理的に切るスイッチで。** USB を抜くだけでは 0.5 秒（上限 0.25 m/s で約 12 cm）走ります
- **2 枚の Arduino は必ず両方 USB につなぐ。** 片方だけだと、もう片方のエンコーダが死んで暴走します
- プログラムは終了時に必ず停止指令を送ります（Ctrl-C・`kill` のどちらでも）
- `--live` は DB を書き換えます。最初は付けずに動きを確かめてください
- `robot_calls` には delete ポリシーが無いため、入れた行は API からは消せません（テスト行の掃除はダッシュボードから）
- ブリッジ（`--nav2`）と `manual_control.py --serial` を同時に実機へつながない

---

## 10. 参照

| 内容 | 場所 |
|---|---|
| 設計の考え方（状態機械・量子化・Nav2 設定の根拠・未決事項） | OneDrive `ものづくりゼミ/02_設計/Pi制御ブリッジ設計メモ_v0_4.md` |
| Arduino 通信仕様（**これが正**・v2） | OneDrive `ものづくりゼミ/simulink/arduino/README_raspberrypi.md` |
| 実機の作業記録（確定値・較正手順・次の一手） | OneDrive `ものづくりゼミ/04_報告・記録/実機ログ_PARTD_オドメトリ実装_v1.html` |
| 地図づくりの起動手順（LiDAR → TF → SLAM の 3 窓） | OneDrive `ものづくりゼミ/03_ガイド・解説/03_マッピング_クイックスタート_v1.html` |
| **段階3 の当日手順**（マッピング → ピン建て → アプリ呼出 → 自己位置推定 → 席 → 本を取得 → 本棚へ帰還） | OneDrive `ものづくりゼミ/03_ガイド・解説/08_アプリ連携_段階3_マッピングから呼出・帰還まで_v1.html` |
| 席座標の採取手順（首振り・静止・中央値・試走で検証） | OneDrive `ものづくりゼミ/05_技術調査/発表会場デモ運用方針書_v1_0.docx` §5 |
| DB スキーマ・RLS | [`../supabase/schema.sql`](../supabase/schema.sql) |
| 各 Issue の進め方 | [`../docs/開発の手引き/index.html`](../docs/開発の手引き/index.html) |
