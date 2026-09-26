# robot/selfhost/ — セルフホスト（ネットが無い会場でも、アプリとロボを Pi の中だけで動かす）

ふだんのアプリは **Vercel（画面）＋ Supabase（データ）＝クラウド**で動いていて、スマホも Pi もインターネットが要ります。
ここにあるのは、同じアプリとデータの置き場を **Pi 5 の中の Docker** で動かし、**ルーターの LAN だけで完結させる**しくみと、
クラウドとの**切り替え・同期**の道具です。

| | クラウド（今まで） | セルフホスト（Pi） |
|---|---|---|
| スマホで開くページ | いつもの Vercel の URL（`https://moving-bookshelf-app.vercel.app`） | `http://<Pi の IP>/`（画面の上に「セルフホスト版」の帯が出る） |
| データの置き場所 | Supabase（クラウド） | Pi の中の PostgreSQL（Docker） |
| インターネット | スマホにも Pi にも要る（テザリングなど） | **要らない**（ルーターの LAN だけ） |
| ロボ側の接続先 `robot/.env` | → `.env.cloud` | → `.env.self` |
| 切り替え | `bash mode.sh cloud` | `bash mode.sh self` |

**Pi 本体に入れるのは Docker（Ubuntu 標準のパッケージ）だけです。** DB・読み書きの窓口・画面・入口はすべてコンテナの中にあり、
Pi の設定ファイルは触りません。要らなくなったら `bash setup.sh uninstall` で跡形なく片付けられます。

```
スマホ ──Wi-Fi──> ルーター ──> Pi 5
                                 ├─ caddy（入口・80 番だけ外に見える）
                                 │    ├─ /rest/v1/… → rest（PostgREST）→ db（PostgreSQL 16）
                                 │    └─ それ以外   → web（アプリの画面 = Next.js）
                                 └─ ROS 2 / Nav2 / 窓⑤ブリッジ（今までどおり Pi の上で。http://127.0.0.1/rest/v1 を使う）
```

---

## 1. 同期されるもの・されないもの

クラウドと Pi の DB は**別々**です。**モードを切り替えるときに、ネットがつながっていれば**自動で揃えます（リアルタイムではありません）。

| データ | 揃うか | 揃え方 |
|---|---|---|
| 本（`books`） | ◯ | 足りない本を**両方向**に足す。同じ本の題名などが違えば、直前まで使っていた側に合わせる |
| 貸出の記録（`loans`） | ◯ | 足りない記録を**両方向**に足す。片方で返却済みなら、もう片方も返却済みにする（**返却は取り消さない**） |
| 本の状態（貸出中／在庫あり） | ◯ | 貸出の記録が届いた側で、記録から決め直す（未返却の貸出がある＝貸出中）。前からの食い違いは直さずに「★要確認」で知らせる |
| 席・本棚のピン（`stop_points`） | ◯ | 直前まで使っていた側 → もう片方の**一方向**（ピンは Pi の地図に合わせたものだから） |
| 本の削除（管理者画面 `/admin/books`。2026-09-26〜） | ◯ | 前回そろえたときに両方にあった本（`.sync_state.json` に控える）が片方にだけ無ければ、その側で消されたと見て**もう片方でも消す**。貸出・呼出の記録が残っている本は消さずに足し戻す。消える本が多すぎるとき（DB を作り直した直後など）も消さずに足し戻す（`--max-delete` で上限を変えられる） |
| 呼出（`robot_calls`） | ✕ | その場かぎり。持ち込むと、古い呼出でロボが動き出す危険があるので揃えない |
| ロボの状態・現在地（`robot_status`）・手動操作（`robot_manual`） | ✕ | その場かぎりの状態 |

- **貸し借りは揃います。** セルフホストで借りた・返した記録は、`mode.sh cloud` でクラウドに戻したときにクラウドにも入ります。
- **本の登録**は、ISBN から題名を調べる外部サービス（openBD）をスマホのブラウザから呼ぶので、**ネットが無いと失敗します**。
  登録できた本は揃います。会場で使う本は、前もってクラウドで登録しておいてください。
- **同じ本を、2 つの DB で別々に借りた**（例: セルフホスト中に、別の人が家から Vercel 版で借りた）ときは、
  自動では決めずに「★要確認」と表示します。アプリの本の詳細で返却して、実物と揃えてください。
- **本の削除**（2026-09-26 に管理者画面へ追加）は上の表のとおり相手側にも伝えます。控え（`robot/selfhost/.sync_state.json`）が無いとき（初回・`reset-db` の直後）は消さず、足りない本を足すだけです。
  消えなかったときは「★…消えませんでした」と出ます（クラウドに `robot/sql/04` を流していないのが典型。控えに残して、次回もう一度消そうとします）。
- 揃えるのは何度やっても安全です（2 回目は「変更なし」）。途中でネットが切れても、もう一度実行すれば続きから揃います。
- 中身だけ見たいとき: `python3 sync.py sync --from self --to cloud --dry-run`（書き込まない）

---

## 2. 初回の準備（研究室などで。★インターネットが要る★）

Pi にテザリングなどでネットがある状態で、Nav2 を止めてから実行します（画面のイメージ作りで CPU を長く使うため）。

```bash
bash ~/moving-bookshelf-app/robot/pi_setup.sh          # コードを最新に（main。2026-09-26 にマージ済み）
bash ~/moving-bookshelf-app/robot/selfhost/setup.sh    # セルフホストの準備（初回は 10〜20 分）
```

`setup.sh` がやること（何度実行しても安全。途中で止まったら、原因を直して同じコマンドをもう一度）:

1. Docker を入れる（`docker.io`・`docker-compose-v2`・`docker-buildx`。すべて Ubuntu 標準）
2. `robot/selfhost/.env` を作る（DB のパスワード。ランダム。git には入らない）
3. CPU の上限が使えるか確かめる（使えれば、コンテナを合わせて 3 コア分までに抑え、Nav2 の取り分を守る）
4. イメージを取ってくる（postgres:16・postgrest v14.18・caddy:2）／画面のイメージを作る
5. `robot/.env` を `.env.cloud`（いまの中身）に移し、`.env` はそれを指すリンクにする。`.env.self` も作る
   （**このあともロボ側は今までどおりクラウドにつながります**）
6. 一度起動して DB を作り、動くことを確かめる → クラウドから本・貸出・ピンを入れる
7. いまのモード（クラウド）に合わせて、コンテナを止める

途中で `sudo` のパスワードを聞かれます。docker グループには入れていない（Pi の設定を増やさない）ので、
docker を使うところは `sudo` 付きで動きます。

**ルーターで Pi の IP を固定しておくと楽です**（ルーターの管理画面の「DHCP 予約／固定割当」で Pi を選ぶ）。
スマホで開く URL が毎回同じになります。

---

## 3. 切り替え

```bash
bash ~/moving-bookshelf-app/robot/selfhost/mode.sh self     # セルフホストへ（ネットが無くても切り替えられる）
bash ~/moving-bookshelf-app/robot/selfhost/mode.sh cloud    # クラウドへ（★Pi にネットが要る★）
bash ~/moving-bookshelf-app/robot/selfhost/mode.sh status   # いまどちらか・動いているか・負荷
```

| | `mode.sh self` | `mode.sh cloud` |
|---|---|---|
| 1 | クラウドでロボが呼出の途中なら止める | Pi でロボが呼出の途中なら止める |
| 2 | コンテナを起動 | Pi → クラウドへ本・貸出・ピンを揃える |
| 3 | ネットがあれば、クラウド → Pi へ揃える（無ければ前回のまま） | `robot/.env` → `.env.cloud` |
| 4 | `robot/.env` → `.env.self` | クラウドに queued の呼出が残っていれば、取り消すか聞く |
| 5 | Pi に queued の呼出が残っていれば、取り消すか聞く | コンテナを止める（DB の中身は残る） |
| 6 | スマホで開く URL を表示 | |

**★切り替えたら、窓⑤（ブリッジ）・手動操作（manual_control）・pin_tool を起動し直してください。★**
起動したときに `robot/.env` を読むので、動かしたままだと前のつなぎ先を使い続けます（`mode.sh` が動いているものを表示します）。
窓①〜④（LiDAR・オドメトリ・TF・Nav2）はネットを使わないので、そのままで構いません。

オプション: `--no-sync`（揃えずに切り替える）、`--force`（ロボが呼出の途中でも／クラウドにつながらなくても切り替える）

---

## 4. 会場での流れ（例）

1. **前日まで（ネットのある所）**: 本の登録・ピン建てを済ませ、`mode.sh self`（クラウド → Pi に揃う）
2. **会場**: ルーター → Pi の電源。窓①〜④ → 窓⑤ ブリッジ。スマホ・PC はルーターの Wi-Fi につなぎ、`http://<Pi の IP>/` を開く
3. 会場で地図を作り直したら、ピンはそのまま Pi に建てる（`pin_tool.py` も `/admin/pins` も Pi の DB に書く）
4. **帰ってきたら（ネットのある所、テザリングでも可）**: `mode.sh cloud`（Pi → クラウドに揃う。会場での貸し借りとピンがクラウドにも入る）

---

## 5. スマホからの開き方

- スマホを**ルーターの Wi-Fi** につなぎ、`mode.sh self` が表示した `http://192.168.x.x/` を開く
- 画面のいちばん上に **「セルフホスト版（ロボの Pi で動作中）」** の帯が出ていれば、Pi につながっています。
  帯が無いのは Vercel 版です（セルフホスト中に Vercel 版から呼んでも、ロボには届きません）
- アドレス欄に「保護されていない通信」と出ますが、このアプリは https 専用のブラウザ機能を使っていないので、そのまま動きます
  （https のときだけ使える `crypto.subtle`・`crypto.randomUUID`・`navigator.locks`・`navigator.clipboard`・`serviceWorker` を消した状態で、
  呼出・本の詳細・履歴・ピン管理を開いて確認済み。ピン管理の「コピー」はクリップボードが無いと何もしないだけで、画面は止まらない）
- **Android で開けないとき**: モバイルデータを一時的に OFF にするか、Wi-Fi の「インターネット接続なし」の確認で「接続を維持」を選ぶ
  （ネットの無い Wi-Fi を避けて、通信をモバイル回線に流す機種があるため）
- Android は `http://<名前>.local/` の形で開けないことがあるので、数字の URL を使ってください

---

## 6. 中で何が動いているか

| コンテナ | イメージ | 役目 | 外から見えるか |
|---|---|---|---|
| `caddy` | caddy:2 | 入口。`/rest/v1/…` を rest へ、それ以外を web へ | **80 番だけ見える** |
| `web` | Pi で作る（[web.Dockerfile](web.Dockerfile)） | アプリの画面（Next.js） | いいえ |
| `rest` | postgrest/postgrest:v14.18 | 読み書きの窓口。Supabase の中で同じ役目をしている部品そのもの | いいえ |
| `db` | postgres:16 | データの置き場所。中身は Docker のボリューム `bookshelf-selfhost_db-data` | いいえ |

- **Supabase をまるごと載せない理由**: アプリもロボも Supabase の「読み書きの窓口（REST）」しか使っていないため
  （ログイン・Realtime・ファイル置き場は使っていない）。公式のセルフホスト版は 11 個のサービスで、最低でもメモリ 4GB・2 コア。
  Nav2 と同じ Pi に載せるには重いので、要る 4 つだけにしています。
- **テーブルの定義はクラウドと同じファイル**（`supabase/schema.sql`・`robot/sql/01〜04`）を、DB を初めて作るときに流します。
  Supabase にしか無いもの（ログイン用の `auth.users`、Realtime の入れ物、`anon` の役）は [sql/00_prelude.sh](sql/00_prelude.sh) で最小限だけ用意し、
  権限は [sql/90_grants.sql](sql/90_grants.sql) で Supabase の anon と同じ範囲（select・insert・update・delete）にしています。
  実際に消せるかは RLS（誰が何を読み書きできるか）が決め、これもクラウドと同じです（2026-09-26 時点で delete できるのは `books` だけ）。
- **鍵**: クラウドでは Supabase のキーで anon になります。セルフホストでは全員が anon なので鍵を使わず、入口（[Caddyfile](Caddyfile)）が
  `Authorization` を外してから渡します。アプリとロボのコードは、接続先の URL が違うだけで同じです。
- **画面の接続先**: セルフホスト版は `NEXT_PUBLIC_SUPABASE_URL=same-origin` でビルドし、「開いているページと同じ場所」を使います
  （[lib/supabaseClient.ts](../../lib/supabaseClient.ts)）。Pi の IP が変わってもビルドし直さなくて済みます。Vercel 版は今までどおりです。
- **Nav2 を守る**: コンテナごとに CPU の上限（DB 1.0・画面 1.0・窓口 0.5・入口 0.5 コア。`robot/selfhost/.env` で変えられる）。
  ログは 1 MB × 3 世代まで（SD カードを食いつぶさない）。
  重さの目安: Pi 5 実機（2026-09-26、待機中）で 4 つ合わせてメモリ約 120 MB（入口 10・窓口 38・DB 27・画面 46 MB）。
  PC の WSL〔Ubuntu 24.04・x86〕では約 150 MB（DB 56・画面 60・窓口 22・入口 11 MB）、何もしていないときの CPU はほぼ 0 %。
  ディスクはイメージ 4 つで約 1.1 GB。Nav2 と同時に動かしたときの負荷は未計測。
- **Docker の中の住所は `172.31.250.0/24` に固定**しています。自動で選ばせると iPhone のテザリング（172.20.10.x）とぶつかることがあるためです。
- コンテナは `restart: unless-stopped` なので、セルフホストのまま Pi を再起動すれば自動で立ち上がり、クラウドのモードなら止まったままです。

---

## 7. アプリや DB を変えたとき

| 変えたもの | やること |
|---|---|
| アプリの画面（`app/`・`lib/`・`components/` など） | `git pull`（pi_setup.sh）→ `bash setup.sh rebuild-web`（★要ネット★）。`mode.sh status` が古いと知らせます |
| テーブル（例: `robot/sql/05_xxx.sql` を足した） | クラウドは今までどおり SQL Editor で流す。Pi の DB には `bash setup.sh sql ../sql/05_xxx.sql`（権限も付け直す） |
| main に入った変更を Pi へ（例: 2026-09-26 の「本の削除」画面 `app/admin/books/` と `robot/sql/04`） | `pi_setup.sh` → `bash setup.sh rebuild-web`（画面）。2026-09-26 より前に作った Pi の DB には `bash setup.sh sql ~/moving-bookshelf-app/robot/sql/04_books_delete_policy.sql`（delete のポリシーと delete の権限が付く。新しく作る DB には自動で入る） |
| ロボ側の Python（`robot/*.py`） | 何もしなくてよい（Docker の外で動いている） |

---

## 8. 困ったとき

| 症状 | 見るところ・直し方 |
|---|---|
| スマホで開けない | 同じルーターの Wi-Fi か／URL の IP は `mode.sh status` の表示と同じか／Android はモバイルデータを OFF |
| 開けるが帯が出ない | それは Vercel 版。`http://<Pi の IP>/` を開き直す |
| 呼んでもロボが動かない | 窓⑤を切り替えのあとに起動し直したか（起動時の「接続先 :」が `http://127.0.0.1` か）／`mode.sh status` |
| `mode.sh cloud` が「クラウドにつながりません」 | Pi がネットに出られていない。テザリングなどでつなぐ（`curl -I https://supabase.com` で確認） |
| 「ロボが呼出の途中です」で止まる | 届け終わって本棚へ戻ってから。前の異常終了で moving のまま残っているだけなら、アプリで取り消すか `--force` |
| 起動を確かめられない | `sudo docker compose -f ~/moving-bookshelf-app/robot/selfhost/compose.yaml logs --tail 50` |
| 「NanoCPUs can not be set」 | `robot/selfhost/.env` の `*_CPUS` を `0` にする（上限なし） |
| Pi の DB を作り直したい | `bash setup.sh reset-db`（Pi にしか無い記録は消えるので、先に `mode.sh cloud`） |
| 本を消したのに、次の同期で相手側から復活する | 控え（`robot/selfhost/.sync_state.json`）が無い（初回・`reset-db` 直後）か、消した本に貸出・呼出の記録がある（外部キーで消せないので足し戻す）。`python3 sync.py sync --from self --to cloud --dry-run` に「本を n 冊消す」「足し戻します」のどちらが出るか見る |
| 「★…消えませんでした（delete のポリシーが無い？）」 | クラウドに `robot/sql/04_books_delete_policy.sql` を流していない → Supabase の SQL Editor で 1 回。Pi の DB なら `bash setup.sh sql …/robot/sql/04_books_delete_policy.sql`。控えに残るので、流したあとの同期で消える |

---

## 9. 片付け

```bash
bash ~/moving-bookshelf-app/robot/selfhost/mode.sh cloud      # Pi にしか無い記録をクラウドへ送る（要ネット）
bash ~/moving-bookshelf-app/robot/selfhost/setup.sh uninstall # コンテナ・イメージ・Pi の DB を消し、robot/.env を元に戻す
sudo apt purge docker.io docker-compose-v2 docker-buildx       # Docker そのものも消すなら
```

---

## 10. ファイル

| ファイル | 中身 |
|---|---|
| [compose.yaml](compose.yaml) | 4 つのコンテナの組み立て |
| [web.Dockerfile](web.Dockerfile) / [web.Dockerfile.dockerignore](web.Dockerfile.dockerignore) | 画面のイメージの作り方 |
| [Caddyfile](Caddyfile) | 入口の道案内 |
| [sql/00_prelude.sh](sql/00_prelude.sh) / [sql/90_grants.sql](sql/90_grants.sql) | Supabase との差を埋める SQL（DB を初めて作るときだけ流れる） |
| [setup.sh](setup.sh) | 準備・画面の作り直し・SQL の追加・DB の作り直し・片付け |
| [mode.sh](mode.sh) | 切り替えと状態表示 |
| [sync.py](sync.py) / [test_sync.py](test_sync.py) | クラウドと Pi の DB を揃える／その検証（`python3 test_sync.py`） |
| `.sync_state.json` | 前回そろえたときに両方の DB にあった本の id の控え（`sync.py` が作る。git には入らない。`reset-db`・`uninstall` で消える） |
| [lib.sh](lib.sh) | setup.sh と mode.sh の共通部品 |
