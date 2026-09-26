import { createClient } from "@supabase/supabase-js";

const envUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!;

// セルフホスト（Pi の Docker。robot/selfhost/）では NEXT_PUBLIC_SUPABASE_URL=same-origin でビルドする。
//   ブラウザ … いま開いているページと同じ場所（http://<Pi の IP>/）の /rest/v1 へ。Pi の IP が変わってもビルドし直さなくてよい
//   サーバー … 本一覧（app/books/page.tsx）の描画用。Docker の中から入口（caddy）へ
// Vercel 版はこれまでどおり Supabase の URL がそのまま入る
const url =
  envUrl !== "same-origin"
    ? envUrl
    : typeof window !== "undefined"
      ? window.location.origin
      : (process.env.SELFHOST_INTERNAL_URL ?? "http://127.0.0.1");

export const supabase = createClient(url, publishableKey);
