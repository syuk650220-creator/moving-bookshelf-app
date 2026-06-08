# コントリビューションガイド（Gitルール）

「03_Git入門」デッキと内容を合わせた、チームの共通ルールです。

- `main` には直接 push しない。必ずブランチを切って PR を出す
- ブランチ名: `feature/画面名` `fix/不具合名`（例 `feature/book-list`）
- コミットメッセージは日本語可・1行で要点（例「本一覧の枠を追加」）
- PR は小さく、1 PR = 1 Issue が目安
- PR は水谷（PM）がレビューして承認後にマージ
- マージは Squash and merge を基本にする

## 基本サイクル

```bash
git switch main && git pull        # 最新の本流を取り込む
git switch -c feature/◯◯           # 作業用の枝を作る
# （コードを編集）
npm run dev                        # ローカルで確認
git add .
git commit -m "やったことを1行で"
git push -u origin feature/◯◯      # GitHub へ送る
# → GitHub でプルリクを出す → レビュー → マージ → 自動デプロイ
```

## 困ったら
- 30分悩んだら抱え込まず共有する
- エラーは全文をコピーして貼る
- コンフリクトは自己判断で消さず相手と相談
