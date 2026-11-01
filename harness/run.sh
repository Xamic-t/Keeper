#!/usr/bin/env bash
# 起 Harness 侧车（Web UI，默认 http://127.0.0.1:3080）。
#
# 三件事：装依赖（第一次）→ 凑齐两个环境变量 → 带覆盖层启动。
# 不碰主服务：这个进程死了、删了，Keeper 的投喂/提醒/待办全都照跑。
set -euo pipefail

cd "$(dirname "$0")"

# dsh 是 developer preview，版本钉死；别用 latest，插件 API 会变
if [ ! -d node_modules/@deepseek-ai/dsh ]; then
  echo "==> 第一次跑，装依赖（约 600 个包，几分钟）"
  npm install --no-audit --no-fund
fi

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
: "${KEEPER_ASSISTANT_TOKEN:?harness/.env 里缺 KEEPER_ASSISTANT_TOKEN，先跑一次 backend 的 setup 或手填}"
echo "==> 主服务地址 ${KEEPER_API:-http://127.0.0.1:8000}"

# 模型 key 不另存一份：直接从主服务配置里取，只存在于本进程环境里
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  DEEPSEEK_API_KEY=$(
    cd ../backend && uv run python -c \
      "from app.config import settings; print(settings.deepseek_api_key.get_secret_value() if settings.deepseek_api_key else '')"
  )
  export DEEPSEEK_API_KEY
fi
[ -n "${DEEPSEEK_API_KEY:-}" ] || { echo "没拿到 DEEPSEEK_API_KEY，检查项目根 .env"; exit 1; }

echo "==> 起 Harness：http://127.0.0.1:3080"
exec npx dsh web --patch ./cordis.patch.yml
