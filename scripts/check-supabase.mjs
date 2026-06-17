// Supabase 接続確認スクリプト
//   実行: node scripts/check-supabase.mjs
//   .env.local を読み込み、各テーブルが見えるか（＝スキーマ適用済みか）を確認する
import { readFileSync } from "node:fs";
import { createClient } from "@supabase/supabase-js";

// .env.local を手動パース（Next を介さず単体実行するため）
const env = {};
for (const line of readFileSync(".env.local", "utf8").split(/\r?\n/)) {
  const m = line.match(/^\s*([\w.]+)\s*=\s*(.*)\s*$/);
  if (m && !line.trim().startsWith("#")) env[m[1]] = m[2];
}

const url = env.NEXT_PUBLIC_SUPABASE_URL;
const key = env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
console.log("URL:", url);
console.log("KEY:", key ? key.slice(0, 16) + "…(" + key.length + " chars)" : "(なし)");
if (!url || !key) {
  console.error("\n✗ URL か KEY が空です。.env.local を確認してください。");
  process.exit(1);
}

const supabase = createClient(url, key);

const tables = ["books", "loans", "users", "robot_calls", "robot_status", "stop_points"];
let ok = 0;
console.log("\n--- テーブル疎通確認 ---");
for (const t of tables) {
  const { count, error } = await supabase.from(t).select("*", { count: "exact", head: true });
  if (error) {
    console.log(`✗ ${t.padEnd(13)} : ${error.message}`);
  } else {
    console.log(`✓ ${t.padEnd(13)} : ${count} 行`);
    ok++;
  }
}
console.log(`\n結果: ${ok}/${tables.length} テーブルに接続できました。`);
process.exit(ok === tables.length ? 0 : 2);
