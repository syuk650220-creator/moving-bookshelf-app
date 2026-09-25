#!/bin/bash
# =====================================================================
#  セルフホスト（Docker の postgres）の前置き ― DB を初めて作るときだけ自動で流れる
#  （/docker-entrypoint-initdb.d。2 回目以降の起動では流れない）
#
#  Supabase にはあって素の PostgreSQL には無いものを、最小限だけ用意する。
#  このあと 10_schema.sql（= supabase/schema.sql）→ 21〜23（= robot/sql/01〜03）→ 90_grants.sql の順に流れる
# =====================================================================
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v authpw="$AUTHENTICATOR_PASSWORD" <<'EOSQL'
-- ① ロール。PostgREST は authenticator で接続し、リクエストごとに anon に切り替える
--    （名前を Supabase にそろえておくと、RLS の考え方がクラウドとまったく同じになる）
create role anon nologin;
create role authenticator login noinherit;
alter role authenticator password :'authpw';
grant anon to authenticator;

-- ② auth.users … schema.sql の users 表が参照している（Supabase のログイン機能の表）。
--    アプリはログイン機能を使っていないので、参照先として空の表があれば足りる
create schema auth;
create table auth.users (id uuid primary key);

-- ③ supabase_realtime … robot/sql/01 がテーブルを足す先。セルフホストでは Realtime は使わない（空の入れ物）
--    （wal_level の警告が出るが、入れ物として置くだけなので問題ない）
create publication supabase_realtime;
EOSQL
