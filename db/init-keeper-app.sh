#!/bin/sh
# 首次初始化：建应用连接用的普通角色 keeper_app（不是库 owner，RLS 才会生效）。
# 幂等：角色已存在就跳过。
set -e

if [ -z "${APP_DB_PASSWORD:-}" ]; then
  echo "APP_DB_PASSWORD 没设置，跳过建角色" >&2
  exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$APP_DB_PASSWORD" <<'EOSQL'
SELECT format('CREATE ROLE keeper_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keeper_app')
\gexec

GRANT CONNECT ON DATABASE :"POSTGRES_DB" TO keeper_app;
EOSQL

echo "keeper_app 角色就绪"
