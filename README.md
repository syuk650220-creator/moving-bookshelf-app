# 移動本棚ロボ アプリ

研究室の本を、自走する「移動本棚ロボ」と Web アプリで一元管理するシステムです。
利用者はアプリで本を探し、自席までロボに本棚ごと呼び出せます。ものづくりゼミ 2026 / 9人・3ヶ月。

> 🚀 **はじめての人・困ったときは、まず [開発の手引き（docs/開発の手引き/index.html）](docs/開発の手引き/index.html) を開いてください。** 環境構築から各Issueの進め方・調べ方まで、初心者向けにまとまっています。

## 技術スタック

- **フロント**: Next.js (App Router) + TypeScript + Tailwind CSS + shadcn/ui
- **バックエンド**: Supabase (PostgreSQL / Auth / Realtime)
- **デプロイ**: Vercel（`main` へのマージで自動デプロイ）
- **ロボ側**: Raspberry Pi 5（Ubuntu 24.04 + ROS 2 Jazzy + Nav2）+ Arduino Nano 33 IoT ×2（メカナム速度制御）+ NeoPixel（`robot/` 配下）

## セットアップ

```bash
npm install
# プロジェクト直下に .env.local を手作り（手順は「開発の手引き」の環境構築ガイド参照）
npm run dev                        # http://localhost:3000
```

詳しい環境構築手順は [開発の手引き / 環境構築ガイド](docs/開発の手引き/環境構築ガイド.html) を参照。

`.env.local` は `.gitignore` 済みでコミットされません。サービスロールキー（service_role）は絶対に共有しないこと。

## ディレクトリ構成

```
app/            # Next.js App Router（画面 S-1〜S-7）
  page.tsx              # タイトル画面（アプリを開く → S-6 ログイン → S-1 本一覧）
  books/page.tsx        # S-1 本一覧
  books/[id]/page.tsx   # S-2 本詳細・借りる/返す
  register/page.tsx     # S-3 本登録
  robot-call/page.tsx   # S-4 ロボ呼出（本と席を選ぶ・「本を取得した」ボタンでロボを本棚へ帰す）
  history/page.tsx      # S-5 貸出履歴
  login/page.tsx        # S-6 ログイン/ゲスト
  search/page.tsx       # S-7 検索・タグ（COULD）
  admin/page.tsx        # 管理者画面（ロボの手動操作・連携確認・現在地）
  admin/pins/page.tsx   # 席・本棚の場所のピン管理（stop_points を地図で確認・編集。ロボの現在地も表示）
  admin/books/page.tsx  # 管理者：登録した本の一覧・編集・削除（貸出・呼出の履歴がある本は消えない）
  admin/books/[id]/page.tsx  # 管理者：本の情報（ISBN・題名・著者・段数）の編集
components/     # 共通UI
lib/            # supabaseClient.ts ほか
supabase/       # schema.sql（テーブル定義 + RLS）
docs/           # 設計・運用ドキュメント
robot/          # ロボ側コード（Pi 5 上で動く：ブリッジ・手動操作・走行系・Nav2 設定）★正本★
scripts/        # 疎通確認・本の一括登録
.github/        # PR / Issue テンプレート
```

## ドキュメント

まずは [開発の手引き](docs/開発の手引き/index.html)（環境構築・各Issueの進め方）。設計・運用の資料は [docs/](docs/)、Git・PR のルールは [CONTRIBUTING.md](CONTRIBUTING.md) を参照。
ロボ側の動かし方（Pi へのインストール・手動操作・ブリッジ・較正）は [robot/README.md](robot/README.md)。

## データベース

`supabase/schema.sql` を Supabase の SQL Editor で実行するとテーブル一式（books / loans / users / robot_calls / robot_status / stop_points）と RLS が作成されます。削除ポリシーは `books` だけ（管理者画面の「本の削除」用。貸出・呼出の履歴がある本は外部キー制約で消えない）。ほかの表は意図的に未定義（全削除事故の防止）。
ロボ連携に必要な追加分（Realtime publication・updated_at トリガ・手動操作用 `robot_manual`・ピン建てと自己位置推定用の `stop_points.kind` / `robot_status.localizing` / 現在地）は `robot/sql/` の 4 ファイル（01 → 02 → 03 → 04。04 は既存プロジェクトに `books` の delete ポリシーを足すもの）を続けて実行してください。
`stop_points` は **id=0 が本棚の場所（ロボの定位置）、id≥1 が席**です。
