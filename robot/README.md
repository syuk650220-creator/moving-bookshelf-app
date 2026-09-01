# robot/ — ロボ側コード

ロボ側（Raspberry Pi 5 + ROS2 + Nav2 + NeoPixel）のコードを置くディレクトリです。

## つなぎ目（疎結合の原則）

アプリとロボは直接やり取りせず、Supabase のテーブルを介してつながります。

```
アプリ ──insert──> robot_calls ──> bookshelf_bridge.py ──> Nav2 ゴール
アプリ <──select── robot_status <── bookshelf_bridge.py <── Nav2 の結果
```

## 入っているもの

| ファイル | 役割 |
|---|---|
| `bookshelf_bridge.py` | ブリッジ本体（Issue #8 / #9）。`robot_calls` を1秒ポーリング（`--realtime` で購読も併用）し、席座標を Nav2 ゴールに変換、status を queued→moving→arrived→done と進める |
| `.env.example` | Supabase 接続情報のひな形。`.env` にコピーして値を入れる（`.env` はコミットされない） |
| `sql/01_realtime_と_updated_at.sql` | schema.sql に足りない2つ（Realtime publication / updated_at 自動更新トリガ）を補う。SQL Editor で1回実行 |
| `requirements.txt` | 依存パッケージ（PC/venv 用の参考。**Pi では apt を使う**こと） |

> メカナム走行系（`pi_controller.py` / `mecanum_node.py` など実機用ツール一式）は
> 実機検証中のため OneDrive の `ものづくりゼミ/pi/` に仮置きしています。
> 動作確認が済んだものから順にこのフォルダへ移します。
> 設計の考え方は同フォルダの `02_設計/Pi制御ブリッジ設計メモ_v0_3.md` を参照。

## 動かし方（PCだけで試せます）

```bash
cd robot
python -m venv .venv
.venv\Scripts\activate            # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # SUPABASE_URL と SUPABASE_ANON_KEY を記入
python bookshelf_bridge.py        # ★DBは書き換えない dry-run。まずはこれ★
```

この状態で Supabase の Table Editor（またはアプリの S-4）から `robot_calls` に
1行 insert すると、1秒以内に検知してログと Nav2 ゴール（クォータニオン変換済み）を表示します。

実際に status を書き戻すところまで試すなら:

```bash
python bookshelf_bridge.py --live --simulate 5   # 走行の代わりに5秒待って「到着」扱い
```

ラズパイ上で Nav2 と接続して実走するなら（ROS2 環境を source してから）:

```bash
python3 bookshelf_bridge.py --live --nav2
```

## Pi でのインストールについて

Ubuntu 24.04 は PEP 668 によりシステム Python への `pip install` を拒否します。
また ROS2 ノードはシステム Python で動くため venv と同居できません。**Pi では apt が正解**:

```bash
sudo apt install -y python3-requests
```

## 注意

- Pi に置くのは **publishable（anon）キーだけ**。`service_role` キーは絶対に置かない
- `robot_calls` には delete ポリシーが無いため、入れた行は API からは消せません（テスト行の掃除はダッシュボードから）
- `arrived → done` の遷移条件（受取ボタン or 時間）は未決事項。現状は `--arrive-hold` 秒（既定3秒）で自動で進みます
