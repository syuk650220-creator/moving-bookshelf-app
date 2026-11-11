-- =========================================================
--  UWB ビーコン（席に置いた UWB ユニットのところへ走る）のための追加分
--  Supabase の SQL Editor に貼り付けて実行する（1回だけでよい）
--
--  ★何度実行しても壊れないように書いてあります★
--  ★schema.sql（2026-11-05 版以降）を新規に流した場合は、この内容はすでに入っています★
--  ★セルフホスト（robot/selfhost）の Pi の DB: 新しく作る DB には自動で入る。
--    2026-11-05 より前に作った DB には
--      bash ~/moving-bookshelf-app/robot/selfhost/setup.sh sql ~/moving-bookshelf-app/robot/sql/05_beacons.sql ★
-- =========================================================
--
--  アプリの「ロボを呼ぶ」画面は、行き先を stop_points の行（id > 0）から選びます。
--  「席のユニット（UWB ビーコン）のところへ」も同じ行き先の 1 つとして持たせるのが、いちばん小さい形です。
--
--    ① stop_points.kind に 'beacon' を足す（これまでは 'seat' と 'home' だけ）
--    ② ビーコンの行を 1 つ入れる（id = 100、label 'ビーコン（UWB）'）
--         x / y は「ロボが前回 UWB で推定した場所」。ブリッジが呼出のたびに書き戻し、次の呼出では
--         まずそちらへアンテナを向けて測る（/admin/pins の地図に推定の時刻つきで出る。手でも直せる）。
--         0 のまま（まだ推定していない）なら、ロボはその場で回って探す。theta は使わない。
--         ★席の id（1〜）とぶつからないように 100 にしてある。アプリの一覧では末尾に出る★
--
--  ロボ側（bookshelf_bridge.py）は kind='beacon' の呼出を受けると、--uwb 付きで起動していれば UWB で探して走り、
--  無ければ走り出さずに canceled にして、理由を robot_status.detail に書きます（アプリに理由が出る）。
--  新しいテーブルも robot_status.state の追加も無し。RLS は既存のポリシー（select / insert / update 全員OK）のまま
--  （推定の書き戻しは stop_points の update。sql/03 のポリシーで書ける）。
--
--  アプリでの見え方（S5）:
--    /robot-call … 席の下に「ユニットのところへ（UWB ビーコン）」の紫のボタンとして出る（kind='beacon' の行）
--    /admin/pins … 推定の場所に紫の破線の丸と時刻（3 秒ごとに読み直す。0,0 の間は出さない）。
--                   「席を追加」の次の id は席の最大 + 1（100 は数えない）
--
--  対応するコード: robot/bookshelf_bridge.py（kind='beacon' の分岐）, robot/uwb_reader.py（S1）,
--                 app/robot-call/page.tsx・app/admin/pins/page.tsx（S5）


-- =========================================================
--  ① stop_points.kind に 'beacon' を追加
-- =========================================================
--  schema.sql の列内 CHECK は自動命名（stop_points_kind_check）、sql/03 は同じ名前で付け直している。
--  名前が違っていても確実に外せるよう、kind を含む CHECK を定義から探して落としてから付け直す。
--  （stop_points_home_is_id0 も kind を含むが、こちらは残す）

do $$
declare
  c record;
begin
  for c in
    select conname
      from pg_constraint
     where conrelid = 'public.stop_points'::regclass
       and contype = 'c'
       and pg_get_constraintdef(oid) like '%kind%'
       and conname <> 'stop_points_home_is_id0'
  loop
    execute format('alter table public.stop_points drop constraint %I', c.conname);
  end loop;

  alter table public.stop_points
    add constraint stop_points_kind_check
    check (kind in ('seat', 'home', 'beacon'));

  -- ★ホーム ⇔ id = 0 ★ は sql/03 で付けてある。無ければここで付ける（schema.sql 直後なら入っている）
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.stop_points'::regclass
                   and conname = 'stop_points_home_is_id0') then
    alter table public.stop_points
      add constraint stop_points_home_is_id0 check ((kind = 'home') = (id = 0));
  end if;
end $$;


-- =========================================================
--  ② ビーコンの行（id = 100）
-- =========================================================
--  座標は原点＝まだ推定していない。最初の呼出のあと、ブリッジが推定した場所を書き戻す（kind は変えない）。

insert into public.stop_points (id, label, x, y, theta, kind)
values (100, 'ビーコン（UWB）', 0, 0, 0, 'beacon')
on conflict (id) do update set kind = 'beacon';


-- =========================================================
--  ③ 確認（実行すると結果が返ります）
-- =========================================================

-- ピン一覧（id=0 が本棚の場所、1〜 が席、100 がビーコン）
select id, kind, label, x, y, theta
  from public.stop_points
 order by id;

-- kind の CHECK に beacon が入ったか
select pg_get_constraintdef(oid) as "stop_points の kind 制約"
  from pg_constraint
 where conrelid = 'public.stop_points'::regclass and conname = 'stop_points_kind_check';
