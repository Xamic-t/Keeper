#!/usr/bin/env bash
# 一键起本地开发环境：数据库 + 后端 + 前端。
#
# 用法：./dev.sh          （前台跑，Ctrl+C 一起停）
#       ./dev.sh --db     （只起数据库，方便用客户端连）
#
# 前置：Docker Desktop 在跑；backend 依赖已 uv sync；frontend 依赖已 pnpm install。
set -euo pipefail

cd "$(dirname "$0")"

echo "==> 1/4 起数据库（Docker 容器里的 PostgreSQL 17 + pgvector）"
docker compose up -d db

if [ "${1:-}" = "--db" ]; then
  echo "数据库就绪。连接信息："
  echo "  主机 127.0.0.1  端口 5432  库 keeper  用户 keeper_app"
  echo "  密码见 .env 的 APP_DB_PASSWORD"
  exit 0
fi

echo "==> 2/4 跑数据库迁移"
(cd backend && uv run alembic upgrade head)

echo "==> 3/4 起后端（http://127.0.0.1:8000，接口文档 /docs）"
(cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &
BACKEND_PID=$!

echo "==> 4/4 起前端（http://127.0.0.1:5173，手机同 WiFi 用本机 IP 访问）"
(cd frontend && pnpm install --silent && pnpm dev) &
FRONTEND_PID=$!

cleanup() {
  echo
  echo "==> 停掉服务"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 3
echo
echo "────────────────────────────────────────────"
echo " 前端（浏览器打开这个）  http://127.0.0.1:5173"
echo " 后端接口文档          http://127.0.0.1:8000/docs"
echo " 手机同 WiFi 访问      http://$(ipconfig getifaddr en0 2>/dev/null || echo '本机IP'):5173"
echo "────────────────────────────────────────────"
echo " Ctrl+C 一起停"
wait
