-- =========================================================
-- 移動本棚ロボ DBスキーマ（基本設計 v0.2 確定版）
-- Supabase の SQL Editor に貼り付けて実行する
-- =========================================================
create extension if not exists pgcrypto;

-- ① stop_points : 停車ポイント（席）の座標表
create table stop_points (
  id          int  primary key,
  label       text not null,
  x           double precision not null,
  y           double precision not null,
  theta       double precision not null default 0,
  created_at  timestamptz not null default now()
);

-- ② users : 利用者（Supabase auth.users と1:1）
create table users (
  id           uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  email        text,
  created_at   timestamptz not null default now()
);

-- ③ books : 本マスタ（ロボ内5冊）
create table books (
  id          uuid primary key default gen_random_uuid(),
  isbn        text,
  title       text not null,
  author      text,
  shelf_level int  not null check (shelf_level between 1 and 5),
  tags        text[] not null default '{}',
  status      text not null default 'available'
                check (status in ('available','on_loan')),
  created_at  timestamptz not null default now()
);

-- ④ loans : 貸出履歴
create table loans (
  id            uuid primary key default gen_random_uuid(),
  book_id       uuid not null references books(id),
  borrower_type text not null check (borrower_type in ('user','guest')),
  user_id       uuid references users(id),
  guest_name    text,
  borrowed_at   timestamptz not null default now(),
  returned_at   timestamptz,
  returned_by   text
);

-- ⑤ robot_calls : ロボ呼出キュー
create table robot_calls (
  id           uuid primary key default gen_random_uuid(),
  book_id      uuid not null references books(id),
  seat_id      int  not null references stop_points(id),
  status       text not null default 'queued'
                 check (status in ('queued','moving','arrived','done','canceled')),
  requested_by text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- ⑥ robot_status : ロボ現在状態（常に1行）
create table robot_status (
  id              int  primary key default 1 check (id = 1),
  state           text not null default 'idle'
                    check (state in ('idle','moving','arrived','returning')),
  current_call_id uuid references robot_calls(id),
  updated_at      timestamptz not null default now()
);
insert into robot_status (id, state) values (1, 'idle');

-- FIFO処理用インデックス
create index idx_calls_queue on robot_calls (created_at) where status = 'queued';

-- ========== RLS（行レベル・セキュリティ） ==========
alter table books        enable row level security;
alter table loans        enable row level security;
alter table robot_calls  enable row level security;
alter table robot_status enable row level security;
alter table stop_points  enable row level security;
alter table users        enable row level security;

-- 閲覧：全員OK
create policy sel_books  on books        for select using (true);
create policy sel_loans  on loans        for select using (true);
create policy sel_calls  on robot_calls  for select using (true);
create policy sel_stat   on robot_status for select using (true);
create policy sel_stops  on stop_points  for select using (true);

-- 追加：全員OK
create policy ins_books  on books       for insert with check (true);
create policy ins_loans  on loans       for insert with check (true);
create policy ins_calls  on robot_calls for insert with check (true);

-- 更新：全員OK
create policy upd_books  on books        for update using (true);
create policy upd_loans  on loans        for update using (true);
create policy upd_calls  on robot_calls  for update using (true);
create policy upd_stat   on robot_status for update using (true);

-- 削除：ポリシーを作らない → 誰も削除不可（全削除事故の防止）
