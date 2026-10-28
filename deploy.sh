#!/usr/bin/env bash
# 一键部署：拉代码 → 建镜像 → 起容器 → 跑迁移。
#
# 用法：
#   ./deploy.sh          # 正常部署
#   ./deploy.sh --build  # 强制重建镜像
#
# 前置：.env 已填好（cp .env.example .env），域名已备案并改进 Caddyfile。
set -euo pipefail

cd "$(dirname "$0")"

echo "==> 检查 .env"
if [ ! -f .env ]; then
  echo "缺少 .env。先执行：cp .env.example .env 并填好必填项" >&2
  exit 1
fi

echo "==> 构建并启动容器"
if [ "${1:-}" = "--build" ]; then
  docker compose build --pull
fi
docker compose up -d db
docker compose up -d --build app web

echo "==> 等待数据库就绪"
for _ in $(seq 1 30); do
  if docker compose exec -T db pg_isready -U keeper -d keeper >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "==> 跑数据库迁移"
docker compose exec -T app uv run alembic upgrade head

echo "==> 健康检查"
for _ in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/api/health || true)
  if [ "$code" = "200" ]; then
    echo "部署完成，接口正常。"
    exit 0
  fi
  sleep 2
done

echo "健康检查没通过，看日志：docker compose logs -f app web" >&2
exit 1
