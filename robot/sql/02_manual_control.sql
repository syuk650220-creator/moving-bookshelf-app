-- =========================================================
--  手動操作（管理者画面のラジコンモード）用テーブル
--  Supabase の SQL Editor に貼り付けて実行する（1回だけでよい）
--
--  ★何度実行しても壊れないように書いてあります★
-- =========================================================
--
--  仕組み:
--    アプリ(管理者画面) ──update──> robot_manual <──RPC manual_poll()── Pi
--
--  ・アプリは「動作(motion)・速度(rpm)・有効(enabled)」を書き込む
--  ・Pi は manual_poll() を 0.2秒ごとに呼んで指令を受け取る
--  ・★デッドマン判定はすべてサーバー(DB)の時計で行う★
--    アプリやPiの時計がずれていても安全に止まれるように、
--    cmd_at はトリガでDB側が付け、経過秒数もDB側で計算して返す


-- =========================================================
--  ① テーブル（常に1行・id=1）
-- =========================================================

create table if not exists public.robot_manual (
  id          int  primary key default 1 check (id = 1),
  enabled     boolean not null default false,          -- 手動モードON/OFF
  motion      int  not null default 0 check (motion between 0 and 6),
                -- 0=停止 1=前進 2=後退 3=左横 4=右横 5=左回転 6=右回転
                -- （番号は simulink の動作表 = mecanum_serial.py と同じ）
  rpm         int  not null default 0 check (rpm between 0 and 60),
                -- 上限60 = 0.25 m/s（会場裁定の速度上限をDBレベルでも強制）
  cmd_at      timestamptz not null default now(),      -- 最後にアプリが指令した時刻（トリガが自動で付ける）
  pi_seen_at  timestamptz                              -- Pi が最後に読みに来た時刻（生存確認）
);

insert into public.robot_manual (id) values (1) on conflict do nothing;


-- =========================================================
--  ② cmd_at を守るトリガ
-- =========================================================
--
--  ・アプリの書き込み → cmd_at = now()（クライアントの値は信用しない）
--  ・Pi のハートビート（pi_seen_at だけの更新）→ cmd_at は触らない
--    （ここを区別しないと、Piが読みに来るだけで「新しい指令が来た」ことに
--      なってしまい、デッドマン（古い指令で止まる仕組み）が機能しなくなる）

create or replace function public.robot_manual_touch()
returns trigger
language plpgsql
as $$
begin
  if new.pi_seen_at is distinct from old.pi_seen_at
     and new.enabled = old.enabled
     and new.motion  = old.motion
     and new.rpm     = old.rpm then
    new.cmd_at := old.cmd_at;      -- Pi のハートビート: 指令時刻は据え置き
  else
    new.cmd_at := now();           -- アプリの指令（同じ値の再送も keepalive として有効）
  end if;
  return new;
end;
$$;

drop trigger if exists trg_robot_manual_touch on public.robot_manual;
create trigger trg_robot_manual_touch
  before update on public.robot_manual
  for each row execute function public.robot_manual_touch();


-- =========================================================
--  ③ Pi 用 RPC: 指令の受け取り + 生存報告を1回で
-- =========================================================
--
--  cmd_age（指令からの経過秒）はDBの時計で計算して返すので、
--  Pi 側は自分の時計と比べる必要がない（クロックずれの影響ゼロ）

create or replace function public.manual_poll()
returns table (enabled boolean, motion int, rpm int, cmd_age double precision)
language sql
as $$
  update public.robot_manual
     set pi_seen_at = now()
   where id = 1
  returning enabled, motion, rpm,
            extract(epoch from (now() - cmd_at))::double precision;
$$;


-- =========================================================
--  ④ アプリ表示用ビュー: 経過秒数つきで読める
-- =========================================================

create or replace view public.robot_manual_v
with (security_invoker = true) as
select id, enabled, motion, rpm, cmd_at, pi_seen_at,
       extract(epoch from (now() - cmd_at))     as cmd_age,
       extract(epoch from (now() - pi_seen_at)) as pi_age
from public.robot_manual;


-- =========================================================
--  ⑤ RLS（schema.sql と同じ方針: select/update 全員OK・delete不可）
-- =========================================================

alter table public.robot_manual enable row level security;

drop policy if exists sel_manual on public.robot_manual;
create policy sel_manual on public.robot_manual for select using (true);

drop policy if exists upd_manual on public.robot_manual;
create policy upd_manual on public.robot_manual for update using (true);

-- insert / delete はポリシーを作らない → 行の増減は誰にもできない（常に1行を保証）


-- =========================================================
--  ⑥ 確認（実行すると結果が返ります）
-- =========================================================

select * from public.robot_manual_v;
