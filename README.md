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
  page.tsx              # S-1 本一覧（ホーム）
  books/[id]/page.tsx   # S-2 本詳細・借りる/返す
  register/page.tsx     # S-3 本登録
  robot-call/page.tsx   # S-4 ロボ呼出（本と席を選ぶ・受取ボタン）
  history/page.tsx      # S-5 貸出履歴
  login/page.tsx        # S-6 ログイン/ゲスト
  search/page.tsx       # S-7 検索・タグ（COULD）
  admin/page.tsx        # 管理者画面（ロボの手動操作・連携確認）
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

`supabase/schema.sql` を Supabase の SQL Editor で実行するとテーブル一式（books / loans / users / robot_calls / robot_status / stop_points）と RLS が作成されます。削除ポリシーは意図的に未定義（全削除事故の防止）。
ロボ連携に必要な追加分（Realtime publication・updated_at トリガ・手動操作用 `robot_manual`）は `robot/sql/` の 2 ファイルを続けて実行してください。
