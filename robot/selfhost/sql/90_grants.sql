-- =====================================================================
--  セルフホスト: アプリとロボが使う役（anon）に、Supabase の anon と同じ範囲の権限を与える
--  DB を初めて作るときに自動で流れる。あとからテーブルを足したら setup.sh sql <ファイル> で流し直す
--
--  行ごとの可否は、各テーブルの RLS（schema.sql・robot/sql）が決める＝クラウドと同じルールで動く
-- =====================================================================

grant usage on schema public to anon;
grant select, insert, update on all tables in schema public to anon;
grant usage, select on all sequences in schema public to anon;
grant execute on all functions in schema public to anon;
-- delete は与えない（クラウドでも delete のポリシーが無く、誰も消せない）

-- PostgREST が起動中なら、表の変わったことを知らせて読み直させる（初回は誰も聞いていないので何も起きない）
notify pgrst, 'reload schema';
