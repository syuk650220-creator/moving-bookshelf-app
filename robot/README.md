# robot/ — ロボ側コード（別管理）

ロボ側（Raspberry Pi 5 + ROS2 Humble + Nav2 + NeoPixel）のコードを置くディレクトリです。
今回のスキャフォルドではプレースホルダのみ。ハード制御チームが実装します。

## 持ち場
- ブリッジ（Supabase `robot_calls` を Realtime 購読 → Nav2 ゴール変換）
- ROS2 / Nav2 によるナビゲーション
- LED 制御（NeoPixel）

## つなぎ目（疎結合の原則）
アプリとロボは直接やり取りせず、Supabase のテーブル（`robot_calls` / `robot_status` など）を介してつながります。共有テーブルの形は PM（水谷）が決めます。
