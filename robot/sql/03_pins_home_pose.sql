-- =========================================================
--  ピン建て（席1〜3＋本棚の場所）・自己位置推定・ロボ現在地 のための追加分
--  Supabase の SQL Editor に貼り付けて実行する（1回だけでよい）
--
--  ★何度実行しても壊れないように書いてあります★
--  ★schema.sql（2026-09-18 版）を新規に流した場合は、この内容はすでに入っています★
-- =========================================================
--
--  段階 3（地図の上を実走して席へ行き、本棚の場所へ帰る）で必要になったもの:
--
--    ① stop_points に「本棚の場所（ホーム）」を持てるようにする
--         kind 列: 'seat'（席・呼出先）／ 'home'（本棚の定位置・帰る場所・自己位置推定の初期位置）
--         ★ホームは必ず id = 0 ★（アプリの席一覧は id > 0 だけを出す。CHECK 制約で強制）
--    ② アプリと Pi のピン建てツールが stop_points を書けるようにする（insert / update ポリシー）
--         これまでは select だけだったため、席座標は SQL Editor でしか入れられなかった
--    ③ robot_status.state に 'localizing'（自己位置推定中）を追加
--    ④ robot_status にロボの現在地（pose_x / pose_y / pose_theta / pose_at）と
--         進行状況の一言（detail）を持たせる → アプリの管理者画面の地図に出す
--
--  対応するコード: robot/bookshelf_bridge.py（--nav2）, robot/pin_tool.py,
--                 app/admin/pins/page.tsx, app/robot-call/page.tsx


-- =========================================================
--  ① stop_points: kind / updated_at
-- =========================================================

alter table public.stop_points
  add column if not exists kind text not null default 'seat';

alter table public.stop_points
  add column if not exists updated_at timestamptz not null default now();

do $$
begin
  -- kind は 'seat' か 'home' だけ
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.stop_points'::regclass
                   and conname = 'stop_points_kind_check') then
    alter table public.stop_points
      add constraint stop_points_kind_check check (kind in ('seat', 'home'));
  end if;

  -- ★ホーム ⇔ id = 0 ★（席は 1 以上）。アプリは id > 0 を席として扱う
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.stop_points'::regclass
                   and conname = 'stop_points_home_is_id0') then
    alter table public.stop_points
      add constraint stop_points_home_is_id0 check ((kind = 'home') = (id = 0));
  end if;
end $$;

-- ホームの行を用意する（座標はピン建てで上書きする。仮に原点）
insert into public.stop_points (id, label, x, y, theta, kind)
values (0, '本棚の場所', 0, 0, 0, 'home')
on conflict (id) do update set kind = 'home';

-- updated_at を自動更新（sql/01 と同じ関数。無ければここで作る）
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_stop_points_updated_at on public.stop_points;
create trigger trg_stop_points_updated_at
  before update on public.stop_points
  for each row execute function public.set_updated_at();


-- =========================================================
--  ② stop_points の RLS: insert / update を許可（delete はこれまでどおり不可）
-- =========================================================
--
--  ピン建ては「管理者画面 /admin/pins」と「Pi の pin_tool.py」の 2 経路から行う。
--  どちらも publishable（anon）キーなので、ポリシーが無いと 42501 で弾かれる。

drop policy if exists ins_stops on public.stop_points;
create policy ins_stops on public.stop_points for insert with check (true);

drop policy if exists upd_stops on public.stop_points;
create policy upd_stops on public.stop_points for update using (true);


-- =========================================================
--  ③ robot_status.state に 'localizing' を追加
-- =========================================================
--
--  idle → localizing（自己位置推定中）→ moving → arrived → returning → idle
--
--  schema.sql の列内 CHECK は自動命名（robot_status_state_check）だが、
--  名前が違っていても確実に外せるよう、state を含む CHECK を定義から探して落とす。

do $$
declare
  c record;
begin
  for c in
    select conname
      from pg_constraint
     where conrelid = 'public.robot_status'::regclass
       and contype = 'c'
       and pg_get_constraintdef(oid) like '%state%'
  loop
    execute format('alter table public.robot_status drop constraint %I', c.conname);
  end loop;

  alter table public.robot_status
    add constraint robot_status_state_check
    check (state in ('idle', 'localizing', 'moving', 'arrived', 'returning'));
end $$;


-- =========================================================
--  ④ robot_status: ロボの現在地と進行状況
-- =========================================================
--
--  書くのはブリッジだけ（1 秒に 1 回程度）。アプリは読むだけ。
--    pose_x / pose_y [m]・pose_theta [rad] … map フレーム。AMCL の推定値（/amcl_pose）
--    pose_at … その推定値の時刻（古ければ「位置不明」扱いにできる）
--    detail  … 「席2 へ移動中（残り 1.2 m）」のような一言。画面にそのまま出す

alter table public.robot_status add column if not exists pose_x     double precision;
alter table public.robot_status add column if not exists pose_y     double precision;
alter table public.robot_status add column if not exists pose_theta double precision;
alter table public.robot_status add column if not exists pose_at    timestamptz;
alter table public.robot_status add column if not exists detail     text;


-- =========================================================
--  ⑤ 確認（実行すると結果が返ります）
-- =========================================================

-- ピン一覧（id=0 が本棚の場所、1〜 が席）
select id, kind, label, x, y, theta, updated_at
  from public.stop_points
 order by id;

-- state の CHECK に localizing が入ったか
select pg_get_constraintdef(oid) as "robot_status の state 制約"
  from pg_constraint
 where conrelid = 'public.robot_status'::regclass and conname = 'robot_status_state_check';

-- stop_points のポリシー（sel / ins / upd の 3 つ）
select policyname, cmd
  from pg_policies
 where schemaname = 'public' and tablename = 'stop_points'
 order by policyname;
