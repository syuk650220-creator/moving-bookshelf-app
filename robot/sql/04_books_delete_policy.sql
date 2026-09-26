-- =========================================================
--  本の削除（管理者画面 /admin/books）のための追加分
--  Supabase の SQL Editor に貼り付けて実行する（1回だけでよい）
--
--  ★何度実行しても壊れないように書いてあります★
--  ★schema.sql（2026-09-25 版以降）を新規に流した場合は、この内容はすでに入っています★
-- =========================================================
--
--  これまで books には delete のポリシーが無く、誰も本を消せませんでした（全削除事故の防止）。
--  管理者画面に「本の削除」（app/admin/books/page.tsx）ができたので、delete を許可します。
--
--  履歴の保護:
--    loans.book_id と robot_calls.book_id は books(id) を参照していて on delete cascade ではないため、
--    貸出履歴・呼出履歴がある本は外部キー制約（23503）で削除できません。履歴のない本だけ消えます。
--
--  注意:
--    using (true) なので、管理者画面を通さなくても publishable（anon）キーがあれば
--    履歴のない本は削除できます。ゼミのプロトタイプとしての割り切りです。
--
--  対応するコード: app/admin/books/page.tsx, app/admin/page.tsx（「本の削除」リンク）


-- =========================================================
--  books の delete ポリシー
-- =========================================================

drop policy if exists del_books on public.books;
create policy del_books on public.books for delete using (true);


-- =========================================================
--  確認（実行後にこの SELECT だけ流してもよい）
-- =========================================================
--  del_books（cmd = DELETE）が 1 行出れば適用済み

select policyname, cmd
  from pg_policies
 where schemaname = 'public' and tablename = 'books'
 order by policyname;
