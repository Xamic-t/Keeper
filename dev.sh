#!/usr/bin/env bash
# 一键起本地开发环境：数据库 + 后端 + 前端。
#
# 用法：./dev.sh                 （前台跑，Ctrl+C 一起停；含每日任务调度器）
#       ./dev.sh --db           （只起数据库，方便用客户端连）
#       ./dev.sh --no-scheduler （不起每日任务调度器，调接口时少一个写库进程）
#
# 前置：Docker Desktop 在跑；backend 依赖已 uv sync；frontend 依赖已 pnpm install。
set -euo pipefail

cd "$(dirname "$0")"

START_SCHEDULER=1
if [ "${1:-}" = "--no-scheduler" ]; then
  START_SCHEDULER=0
fi

echo "==> 1/4 起数据库（Docker 容器里的 PostgreSQL 17 + pgvector）"
docker compose up -d db

if [ "${1:-}" = "--db" ]; then
  echo "数据库就绪。连接信息："
  echo "  主机 127.0.0.1  端口 5432  库 keeper  用户 keeper_app"
  echo "  密码见 .env 的 APP_DB_PASSWORD"
  exit 0
fi

echo "==> 2/4 跑数据库迁移（owner 连接串，配置见 DATABASE_URL_MIGRATE）"
(cd backend && uv run alembic upgrade head)

echo "==> 3/4 起后端（http://127.0.0.1:8000，接口文档 /docs）"
(cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &
BACKEND_PID=$!

SCHEDULER_PID=""
if [ "$START_SCHEDULER" = "1" ]; then
  echo "==> 3.5/4 起每日任务调度器（每天 08:00 扫事件、发提醒；不需要可用 --no-scheduler）"
  (cd backend && uv run python -m app.jobs.daily --serve) &
  SCHEDULER_PID=$!
fi

echo "==> 4/4 起前端（http://127.0.0.1:5173，手机同 WiFi 用本机 IP 访问）"
(cd frontend && pnpm install --silent && pnpm dev) &
FRONTEND_PID=$!

cleanup() {
  echo
  echo "==> 停掉服务"
  kill "$BACKEND_PID" "$FRONTEND_PID" $SCHEDULER_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 3
echo
echo "────────────────────────────────────────────"
echo " 前端（浏览器打开这个）  http://127.0.0.1:5173"
echo " 后端接口文档          http://127.0.0.1:8000/docs"
echo " 每日任务调度器        $([ "$START_SCHEDULER" = "1" ] && echo 已启动 || echo 未启动)"
echo " 手机同 WiFi 访问      http://$(ipconfig getifaddr en0 2>/dev/null || echo '本机IP'):5173"
echo "────────────────────────────────────────────"
echo " Ctrl+C 一起停"
wait
