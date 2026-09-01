-- =========================================================
--  schema.sql に足りていなかった2つを補う
--  Supabase の SQL Editor に貼り付けて実行する（1回だけでよい）
--
--  ★何度実行しても壊れないように書いてあります★
-- =========================================================


-- =========================================================
--  ① Realtime を有効にする
-- =========================================================
--
--  ★これをやらないと、購読できているのに何も飛んできません★
--  Supabase の Realtime は「publication に入っているテーブル」の変更だけを配信します。
--  schema.sql では追加されていないため、robot_calls を insert しても
--  ブリッジ側の callback が一度も呼ばれない、という状態になります。
--  （原因が見えないので、知らないと半日溶けます）
--
--  robot_calls  … ロボ側が「呼び出された」ことを即座に知るため
--  robot_status … アプリ側が「いまロボがどこにいるか」を即座に出すため

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public' and tablename = 'robot_calls'
  ) then
    alter publication supabase_realtime add table robot_calls;
  end if;

  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public' and tablename = 'robot_status'
  ) then
    alter publication supabase_realtime add table robot_status;
  end if;
end $$;


-- =========================================================
--  ② updated_at を自動更新する
-- =========================================================
--
--  robot_calls / robot_status には updated_at 列がありますが、
--  schema.sql にはトリガがないため **UPDATE しても値が変わりません**
--  （default now() は INSERT のときにしか効きません）。
--
--  「いつ状態が変わったか」が分からないと
--    ・ロボが固まっているのか処理中なのか区別できない
--    ・アプリ側でタイムアウト表示が作れない
--  ので、ここでトリガを付けます。
--
--  ※ Pi 側のブリッジ（bookshelf_bridge.py）は自分でも updated_at を入れています。
--    トリガと二重になりますが、同じ値で上書きされるだけなので問題ありません。
--    「どちらか片方を入れ忘れても動く」ようにしてあります。

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_robot_calls_updated_at on public.robot_calls;
create trigger trg_robot_calls_updated_at
  before update on public.robot_calls
  for each row execute function public.set_updated_at();

drop trigger if exists trg_robot_status_updated_at on public.robot_status;
create trigger trg_robot_status_updated_at
  before update on public.robot_status
  for each row execute function public.set_updated_at();


-- =========================================================
--  ③ 確認（実行すると結果が2つ返ります）
-- =========================================================

-- Realtime に入っているか
select tablename as "Realtime有効なテーブル"
from pg_publication_tables
where pubname = 'supabase_realtime' and schemaname = 'public'
order by tablename;

-- トリガが付いているか
select event_object_table as "テーブル", trigger_name as "トリガ"
from information_schema.triggers
where trigger_schema = 'public' and trigger_name like 'trg_%updated_at'
order by 1;


-- =========================================================
--  補足
-- =========================================================
--
--  ・Realtime の UPDATE イベントで「変更前の値」も受け取りたい場合は
--      alter table robot_calls replica identity full;
--    が必要ですが、今回の用途（INSERT を拾う / 新しい状態を読む）では不要です。
--
--  ・この内容は moving-bookshelf-app リポジトリの supabase/schema.sql にも
--    反映しておくこと（新しくプロジェクトを作り直したときに再現できるように）。
--    PR のタイトル例:「schema.sql に Realtime publication と updated_at トリガを追加」
