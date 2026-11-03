#!/usr/bin/env bash
# Keeper 本地一键启动 / 停止 / 查看状态。
#
#   ./start.sh              起数据库 + 迁移 + 后端 + 前端（后台常驻，关终端不影响）
#   ./start.sh stop         全部停掉
#   ./start.sh restart      重启
#   ./start.sh status       看谁在跑、端口是否通
#   ./start.sh logs         跟踪日志（默认后端；logs frontend / logs scheduler）
#   ./start.sh --scheduler  额外起每日任务调度器（每天 08:00 扫事件发提醒）
#
# 和 dev.sh 的区别：
#   dev.sh   前台跑，Ctrl+C 全停，适合"一边改代码一边看"（--reload）
#   start.sh 后台跑，带 PID 文件和日志，适合"起好了我要用"，关掉终端也不停
#
# 日志：.run/logs/backend.log · frontend.log · scheduler.log
# 端口：后端 8000（/docs 看接口）· 前端 5173（浏览器打开这个）
set -euo pipefail

cd "$(dirname "$0")"

RUN_DIR=".run"
LOG_DIR="$RUN_DIR/logs"
BACKEND_PID="$RUN_DIR/backend.pid"
FRONTEND_PID="$RUN_DIR/frontend.pid"
SCHEDULER_PID="$RUN_DIR/scheduler.pid"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

mkdir -p "$LOG_DIR"

info() { echo "==> $*"; }
fail() { echo "❌ $*" >&2; exit 1; }

# ---------- 小工具 ----------

# pid 文件里的进程是否还活着
alive() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  local pid
  pid=$(cat "$file" 2>/dev/null || echo "")
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

# 端口上有没有人监听（用来发现"上次没停干净"或"别的程序占着"）。
# 结尾的 || true 不能省：lsof 没匹配时退出码 1，配合 set -e/pipefail
# 会让脚本在这一行**静默退出**（第一版就踩了，表现为"停在 3/4 什么都不打印"）。
port_pid() {
  lsof -ti "tcp:$1" 2>/dev/null | head -1 || true
}

# 等到 URL 返回 200，最多等 40 秒
wait_http() {
  local url="$1" name="$2" i
  for i in $(seq 1 80); do
    if curl -fsS --noproxy '*' -o /dev/null --max-time 2 "$url" 2>/dev/null; then
      echo "    ${name} 就绪：${url}"
      return 0
    fi
    sleep 0.5
  done
  return 1
}

# ---------- 各步骤 ----------

check_prereq() {
  command -v docker >/dev/null 2>&1 || fail "找不到 docker（macOS 上先打开 Docker Desktop）"
  command -v uv     >/dev/null 2>&1 || fail "找不到 uv（brew install uv，或见 backend/README）"
  command -v node   >/dev/null 2>&1 || fail "找不到 node（需要 20+）"

  if [[ ! -f .env ]]; then
    fail ".env 不存在：先 cp .env.example .env，并填 DB_PASSWORD / APP_DB_PASSWORD / SECRET_KEY"
  fi

  if [[ ! -d frontend/node_modules ]]; then
    info "前端依赖没装，跑一次 pnpm install（约 1 分钟）"
    (cd frontend && pnpm install --silent)
  fi
}

start_db() {
  info "1/4 数据库（Docker 里的 PostgreSQL 17 + pgvector）"
  docker compose up -d db >/dev/null 2>&1 || fail "数据库起不来：先确认 Docker Desktop 在跑，再看 docker compose logs db"

  # 等 healthy，最多 30 秒
  local i
  for i in $(seq 1 30); do
    if docker compose ps db 2>/dev/null | grep -q "(healthy)"; then
      echo "    数据库就绪（127.0.0.1:5432）"
      return 0
    fi
    sleep 1
  done
  fail "数据库 30 秒内没变成 healthy，看 docker compose logs db"
}

migrate() {
  info "2/4 数据库迁移（alembic upgrade head）"
  (cd backend && uv run alembic upgrade head 2>&1 | tail -3)
}

start_backend() {
  info "3/4 后端（http://127.0.0.1:${BACKEND_PORT}，接口文档 /docs）"
  if alive "$BACKEND_PID"; then
    echo "    已在跑（pid $(cat "$BACKEND_PID")），跳过"
    return 0
  fi
  local busy
  busy=$(port_pid "$BACKEND_PORT")
  if [[ -n "$busy" ]]; then
    fail "端口 ${BACKEND_PORT} 被占用（pid ${busy}）：先 ./start.sh stop，或 BACKEND_PORT=8001 ./start.sh"
  fi

  (
    cd backend || exit 1
    nohup uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
      > "../$LOG_DIR/backend.log" 2>&1 &
    echo $! > "../$BACKEND_PID"
  )

  if ! wait_http "http://127.0.0.1:${BACKEND_PORT}/api/health" "后端"; then
    tail -20 "$LOG_DIR/backend.log" >&2 || true
    fail "后端 40 秒内没起来，日志见 ${LOG_DIR}/backend.log"
  fi
}

start_scheduler() {
  info "    + 每日任务调度器（每天 08:00 扫事件、生成并推送提醒）"
  if alive "$SCHEDULER_PID"; then
    echo "    已在跑（pid $(cat "$SCHEDULER_PID")），跳过"
    return 0
  fi
  (
    cd backend || exit 1
    nohup uv run python -m app.jobs.daily --serve \
      > "../$LOG_DIR/scheduler.log" 2>&1 &
    echo $! > "../$SCHEDULER_PID"
  )
  sleep 1
  if alive "$SCHEDULER_PID"; then
    echo "    调度器已起（日志 ${LOG_DIR}/scheduler.log）"
  else
    echo "    ⚠️  调度器没起来，不影响页面使用；日志见 ${LOG_DIR}/scheduler.log"
  fi
}

start_frontend() {
  info "4/4 前端（http://127.0.0.1:${FRONTEND_PORT}）"
  if alive "$FRONTEND_PID"; then
    echo "    已在跑（pid $(cat "$FRONTEND_PID")），跳过"
    return 0
  fi
  local busy
  busy=$(port_pid "$FRONTEND_PORT")
  if [[ -n "$busy" ]]; then
    fail "端口 ${FRONTEND_PORT} 被占用（pid ${busy}）：先 ./start.sh stop，或 FRONTEND_PORT=5174 ./start.sh"
  fi

  (
    cd frontend || exit 1
    nohup pnpm dev --port "$FRONTEND_PORT" --strictPort \
      > "../$LOG_DIR/frontend.log" 2>&1 &
    echo $! > "../$FRONTEND_PID"
  )

  if ! wait_http "http://127.0.0.1:${FRONTEND_PORT}/" "前端"; then
    tail -20 "$LOG_DIR/frontend.log" >&2 || true
    fail "前端 40 秒内没起来，日志见 ${LOG_DIR}/frontend.log"
  fi
}

# ---------- 命令 ----------

cmd_start() {
  check_prereq
  start_db
  migrate
  start_backend
  [[ "${WITH_SCHEDULER:-0}" == "1" ]] && start_scheduler
  start_frontend

  local lan_ip
  lan_ip=$(ipconfig getifaddr en0 2>/dev/null || echo "")

  echo
  echo "────────────────────────────────────────────"
  echo " 浏览器打开      http://127.0.0.1:${FRONTEND_PORT}"
  echo " 接口文档        http://127.0.0.1:${BACKEND_PORT}/docs"
  if [[ -n "$lan_ip" ]]; then
    echo " 手机同 WiFi     http://${lan_ip}:${FRONTEND_PORT}"
  fi
  echo " 离线演示        http://127.0.0.1:${FRONTEND_PORT}/?demo=1（不连后端、用预录数据）"
  echo " 停掉服务        ./start.sh stop"
  echo " 看日志          ./start.sh logs"
  echo "────────────────────────────────────────────"
  echo " 账号：Admin / adminadmin（本地开发用，上线前必须换）"
}

cmd_stop() {
  local stopped=0
  for pair in "backend:$BACKEND_PID" "frontend:$FRONTEND_PID" "scheduler:$SCHEDULER_PID"; do
    local name="${pair%%:*}" file="${pair##*:}"
    if alive "$file"; then
      local pid
      pid=$(cat "$file")
      kill "$pid" 2>/dev/null || true
      # uvicorn/vite 会带子进程，给 5 秒优雅退出再强杀
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
      echo "    已停 ${name}（pid ${pid}）"
      stopped=1
    fi
    rm -f "$file"
  done
  if [[ "$stopped" == "0" ]]; then
    echo "    没有在跑的服务"
  fi

  # 数据库容器默认不动：它不占 CPU，数据在卷里；要停用 docker compose stop db
  info "数据库容器保持运行（要停：docker compose stop db）"
}

cmd_status() {
  echo "服务状态："
  for pair in "后端:$BACKEND_PID" "前端:$FRONTEND_PID" "调度器:$SCHEDULER_PID"; do
    local name="${pair%%:*}" file="${pair##*:}"
    if alive "$file"; then
      echo "  ✅ ${name}  pid $(cat "$file")"
    else
      echo "  ⬜ ${name}  未运行"
    fi
  done

  echo
  echo "端口与连通性："
  for item in "后端 ${BACKEND_PORT} /api/health" "前端 ${FRONTEND_PORT} /"; do
    local name="${item%% *}"
    local rest="${item#* }"
    local port="${rest%% *}"
    local path="${rest##* }"
    if curl -fsS --noproxy '*' -o /dev/null --max-time 2 "http://127.0.0.1:${port}${path}" 2>/dev/null; then
      # 端口通但 pid 文件不在：说明进程不是本脚本起的（比如 dev.sh、或上次没写 pid）
      local owner
      owner=$(port_pid "$port")
      if [[ -n "$owner" ]]; then
        echo "  ✅ ${name}  http://127.0.0.1:${port}${path} 通（pid ${owner}，非本脚本记录）"
      else
        echo "  ✅ ${name}  http://127.0.0.1:${port}${path} 通"
      fi
    else
      echo "  ❌ ${name}  端口 ${port} 不通"
    fi
  done

  echo
  local healthy
  healthy=$(docker compose ps db 2>/dev/null | grep -c '(healthy)' || true)
  echo "数据库：${healthy:-0} 个 healthy 容器"

  # 手机怎么进：这台机器的内网 IP + 前端端口。后端只监听 127.0.0.1，
  # 手机不需要直连 8000——Vite 会把 /api 转给本机后端，所以只暴露 5173 一个端口。
  local lan_ip
  lan_ip=$(ipconfig getifaddr en0 2>/dev/null || echo "")
  if [[ -n "$lan_ip" ]]; then
    echo "手机访问（同一 WiFi）：http://${lan_ip}:${FRONTEND_PORT}"
  fi
}

cmd_logs() {
  local which="${1:-backend}"
  local file="$LOG_DIR/${which}.log"
  [[ -f "$file" ]] || fail "没有 ${file}（${which} 还没起过？）"
  echo "==> tail -f ${file}（Ctrl+C 退出，不影响服务）"
  tail -f "$file"
}

# ---------- 入口 ----------

if [[ "${1:-}" == "logs" ]]; then
  cmd_logs "${2:-backend}"
  exit 0
fi

WITH_SCHEDULER=0
ACTION="start"
for arg in "$@"; do
  case "$arg" in
    start)          ACTION="start" ;;
    stop)           ACTION="stop" ;;
    restart)        ACTION="restart" ;;
    status)         ACTION="status" ;;
    --scheduler)    WITH_SCHEDULER=1 ;;
    -h|--help)      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)              fail "不认识的参数：$arg（可用：start / stop / restart / status / logs / --scheduler）" ;;
  esac
done
export WITH_SCHEDULER

case "$ACTION" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; echo; cmd_start ;;
  status)  cmd_status ;;
esac
