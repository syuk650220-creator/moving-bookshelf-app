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
| Pi ↔ Arduino | USB シリアル 115200。指令 7 バイトを 50 Hz で両基板へ、報告 24 バイトが 10 Hz で各基板から |
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
         アプリ     <──select── robot_status <── bookshelf_bridge.py
【手動】 アプリ /admin ──update──> robot_manual <──manual_poll()── manual_control.py --serial ──> Arduino ×2
```

- `robot_calls.status` は queued → moving → arrived → done。moving と arrived はブリッジが進め、
  **arrived → done はアプリ S-4 の「受け取った」ボタン**で進みます（ゼミ決定 2026-09-01）。
- `robot_status` は常に 1 行（id=1）。書くのはブリッジだけです。
- **ブリッジと手動操作を同時に実機へつながないこと**（Arduino への指令の取り合いになります）。

---

## 2. 入っているもの

| ファイル | 役割 |
|---|---|
| `bookshelf_bridge.py` | ブリッジ本体（Issue #8 / #9 / #14）。`robot_calls` を 1 秒ポーリング（`--realtime` で購読も併用）し、席座標を Nav2 ゴールに変換、status を進める。`--nav2` で実走 |
| `manual_control.py` | 管理者画面（/admin）ラジコンモードの受け側。`robot_manual` を 0.2 秒ポーリングし、指令が 1.2 秒更新されなければ停止（デッドマン）。`--serial` で実機接続 |
| `mecanum_serial.py` | Arduino 2 枚との USB シリアル通信ライブラリ（送信ループ・テレメトリ＋IMU・量子化 `Quantizer`・暴走防止の検出） |
| `mecanum_node.py` | ROS 2 ノード。`/cmd_vel` → パケット、テレメトリ → `/odom` と TF |
| `pi_controller.py` | 手動操作と診断（`--identify` `--sweep` `--lowspeed`）。ROS 2 不要。**実機を初めて動かすときはまずこれ** |
| `robot_params.py` | ★寸法・速度の設定はここだけ★ 未較正の値に TODO |
| `calib_monitor.py` | 較正用。`/odom` を購読して累積の移動量・回転角を表示 |
| `test_logic.py` | 実機なしで計算を検証（44 項目）。pyserial も requests も不要 |
| `nav2/nav2_params_差分.yaml` | Nav2 の設定のうち既定値から変える分（根拠つき） |
| `sql/01_realtime_と_updated_at.sql` | schema.sql に足りない 2 つ（Realtime publication ／ updated_at 自動更新トリガ）。SQL Editor で 1 回実行 |
| `sql/02_manual_control.sql` | 手動操作用の `robot_manual` テーブル・RPC・ビュー。SQL Editor で 1 回実行 |
| `.env.example` | Supabase 接続情報のひな形。`.env` にコピーして値を入れる（`.env` はコミットされない） |
| `requirements.txt` | PC／venv 用の参考。**Pi では apt を使う** |

---

## 3. Pi へのインストール

### 3-1 コードを置く

```bash
sudo apt install -y git python3-serial python3-requests
cd ~ && git clone https://github.com/syuk650220-creator/moving-bookshelf-app.git
cd ~/moving-bookshelf-app/robot
```

以後の更新は `cd ~/moving-bookshelf-app && git pull` です。

> **★`pip install` は使えません★**
> Ubuntu 24.04 は PEP 668 により、システムの Python への `pip install` を拒否します
> （`error: externally-managed-environment`）。
> また **ROS 2 のノードはシステムの Python で動く**ため、venv に入れると `rclpy` と一緒に import できなくなります。**apt が正解です。**
> Realtime 用の `supabase` パッケージも Pi には入れません。ポーリングだけで運用できる設計です。

### 3-2 接続情報

```bash
cp .env.example .env
nano .env        # SUPABASE_URL と SUPABASE_ANON_KEY を書く
```

値はアプリの `.env.local` と同じです（URL と publishable キー `sb_publishable_…`）。
**service_role キーは絶対に Pi に置かないこと。** anon（publishable）キーで足ります。

Supabase の SQL Editor で `sql/01_realtime_と_updated_at.sql` と `sql/02_manual_control.sql` を 1 回ずつ実行しておきます
（何度実行しても壊れないように書いてあります。済んでいれば不要）。

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
python3 test_logic.py      # 「すべて成功」が出ること（44 項目）
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

アプリの S-4 で呼ぶと、順番待ち → 移動中 → 到着（受取待ち）と表示が進みます。
**「受け取った」を押すと done になり、ブリッジは次の呼出へ進みます。**
押し忘れ対策に `--arrive-timeout 120` を付けると 120 秒で自動 done にできます（既定は無効）。

### 5-3 Pi で実走する（Nav2）

前提: 地図が保存済み、`stop_points` が map 座標の実測値、`mecanum_node.py` と Nav2 が起動済み（§7）。

```bash
python3 bookshelf_bridge.py --live --nav2
```

1 回の走行は `--nav-timeout`（既定 180 秒）で打ち切られ、失敗時は canceled になります。

Realtime も試すなら `--realtime` を足します。**動かなくても構いません** ― ポーリング（1 秒）だけで運用できる設計です。
会場の Wi-Fi は不通前提という裁定が出ているため、意図的にこうしてあります。

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
**既定値から変えるところだけ**を根拠つきで書いてあるので、`nav2_bringup` の標準 `nav2_params.yaml` をコピーしたものに反映してください。
とくに `robot_model_type: "nav2_amcl::OmniMotionModel"` は、入れないと横移動のたびに自己位置が破綻します。

実走の起動順（目安）: LiDAR → `mecanum_node.py`（TF） → Nav2 bringup（地図＋AMCL） → RViz で初期位置 → `bookshelf_bridge.py --live --nav2`。

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
| DB スキーマ・RLS | [`../supabase/schema.sql`](../supabase/schema.sql) |
| 各 Issue の進め方 | [`../docs/開発の手引き/index.html`](../docs/開発の手引き/index.html) |
