#!/usr/bin/env bash
# 上线前的预检：只检查、不改任何配置、不启动任何容器。
#
# 为什么需要：`deploy.sh` 会直接把服务拉起来。等它报错时你已经在一个半上线的状态里，
# 而且错误信息通常是 "container unhealthy" 这种说不出所以然的话。
# 这个脚本把"能不能上线"拆成一条条可在 10 秒内跑完的判定。
#
# 用法：
#   ./scripts/preflight_deploy.sh
#
# 判定分两级：
#   ❌ 关键项失败 → 退出码 1（现在上线会失败或不安全）
#   ⚠️  警告项   → 只打印（能上线，但有代价）
#
# 隐私红线：本脚本**绝不打印任何密钥原文**，只打印"已填/未填""长度够/不够"。
# 不做的事：不 docker compose up、不写 .env、不改 Caddyfile、不申请域名。
set -uo pipefail

cd "$(dirname "$0")/.."   # 仓库根

FAIL=0
CRITICAL=0
WARN=0

ok()   { echo "✅ $*"; }
bad()  { echo "❌ $*"; FAIL=$((FAIL + 1)); CRITICAL=$((CRITICAL + 1)); }
warn() { echo "⚠️  $*"; WARN=$((WARN + 1)); }
info() { echo "==> $*"; }

# ---------- 1. .env ----------
info "1/8 .env 与必填项"
if [[ ! -f .env ]]; then
  bad ".env 不存在（从 .env.example 复制一份：cp .env.example .env）"
else
  ok ".env 存在"

  # 取某个 key 的值（去掉可能的 export 前缀与包裹引号）。**只用于判空，不回显。**
  env_value() {
    local key="$1"
    sed -n "s/^\(export \)\{0,1\}${key}=//p" .env | tail -1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
  }

  is_placeholder() {
    local value
    value=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    case "$value" in
      *change-me*|*changeme*|*please-change*|*your-*|*secret*|*password*|*keeper*|*admin*|*test*) return 0 ;;
      *) return 1 ;;
    esac
  }

  app_env=$(env_value APP_ENV)
  [[ "$app_env" == "prod" ]] || warn "APP_ENV=${app_env:-未设置}（上线应为 prod：关 /docs、cookie 加 secure）"

  for key in DB_PASSWORD APP_DB_PASSWORD SECRET_KEY; do
    value=$(env_value "$key")
    if [[ -z "${value// /}" ]]; then
      bad "$key 未填（值不打印）"
    else
      ok "$key 已填"
    fi
  done

  # 模型 Key：没有它投喂识别不出内容（界面不崩，但产品等于废了）
  if [[ -z "$(env_value DEEPSEEK_API_KEY)" ]]; then
    warn "DEEPSEEK_API_KEY 未填：投喂的截图不会被识别（材料会落 failed）"
  else
    ok "DEEPSEEK_API_KEY 已填"
  fi

  # 两个数据库密码必须不同：应用角色若和 owner 同口令，等于把 RLS 的门钥匙交出去
  db_pwd=$(env_value DB_PASSWORD)
  app_pwd=$(env_value APP_DB_PASSWORD)
  if [[ -n "$db_pwd" && -n "$app_pwd" && "$db_pwd" == "$app_pwd" ]]; then
    bad "DB_PASSWORD 与 APP_DB_PASSWORD 相同（应用角色必须与 owner 不同口令）"
  fi
fi

# ---------- 2. SECRET_KEY 强度 ----------
info "2/8 SECRET_KEY 强度"
if [[ -f .env ]]; then
  secret_value=$(sed -n 's/^\(export \)\{0,1\}SECRET_KEY=//p' .env | tail -1 | tr -d '"'"'"'')
  secret_len=${#secret_value}
  if [[ "$secret_len" -eq 0 ]]; then
    bad "SECRET_KEY 为空（生产会直接拒启动）"
  elif [[ "$secret_len" -lt 32 ]]; then
    bad "SECRET_KEY 长度 ${secret_len} < 32（只报长度，不打印内容）"
  else
    ok "SECRET_KEY 长度 ${secret_len} ≥ 32"
  fi
  secret_lower=$(printf '%s' "$secret_value" | tr '[:upper:]' '[:lower:]')
  hit=""
  for word in change-me changeme please-change your-secret secret-key 123456 password; do
    if [[ "$secret_lower" == *"$word"* ]]; then
      hit="$word"
      break
    fi
  done
  if [[ -n "$hit" ]]; then
    # 只报命中的是哪一类词，不打印密钥本身
    bad "SECRET_KEY 命中占位词「${hit}」（内容不打印；用 python3 -c \"import secrets;print(secrets.token_urlsafe(32))\" 生成一个）"
  fi
  unset secret_value
fi

# ---------- 3. compose 能否解析 ----------
info "3/8 docker compose 配置可解析"
if ! command -v docker >/dev/null 2>&1; then
  bad "找不到 docker 命令"
elif docker compose config -q >/dev/null 2>&1; then
  ok "docker compose config 通过"
else
  bad "docker compose config 失败（缺变量/语法错，见 docker compose config 的报错）"
fi

# ---------- 4. Caddyfile 域名 ----------
info "4/8 Caddyfile 域名"
if [[ ! -f Caddyfile ]]; then
  bad "Caddyfile 不存在"
else
  domain=$(awk '/^[A-Za-z0-9*][A-Za-z0-9*.-]*[[:space:]]*\{/{print $1; exit}' Caddyfile)
  if [[ -z "$domain" ]]; then
    bad "Caddyfile 里没找到站点块（第一行应是 yourdomain.com {）"
  elif [[ "$domain" == "yourdomain.com" || "$domain" == "example.com" ]]; then
    bad "Caddyfile 域名还是占位值 ${domain}（备案通过后换成真实域名，Caddy 才能签证书）"
  else
    ok "域名已配置：${domain}"
  fi
fi

# ---------- 5. 数据库连接角色 ----------
info "5/8 数据库连接角色（superuser / BYPASSRLS 会静默绕过 RLS）"
CHECK_PY=$(mktemp)
trap 'rm -f "$CHECK_PY"' EXIT
cat > "$CHECK_PY" <<'PY'
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


async def main() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT current_user, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
    finally:
        await engine.dispose()
    role, is_super, bypass = row
    print(f"{role}|{str(is_super).lower()}|{str(bypass).lower()}")
    sys.exit(0 if not is_super and not bypass else 3)


asyncio.run(main())
PY

db_output=""
db_state="unreachable"
if docker compose ps app 2>/dev/null | grep -q "running"; then
  # 服务器上优先用容器里的环境（那就是线上真实用的配置）
  db_output=$(docker compose exec -T app uv run python - < "$CHECK_PY" 2>/dev/null) && db_state="ok"
elif command -v uv >/dev/null 2>&1 && (cd backend && uv run python - < "$CHECK_PY" 2>/dev/null > /tmp/preflight_db.out); then
  db_output=$(cat /tmp/preflight_db.out)
  db_state="ok"
fi
rm -f /tmp/preflight_db.out

if [[ "$db_state" != "ok" ]]; then
  warn "无法连库验证角色（库没起或本机没有 uv）——上线前请在服务器上再跑一次本脚本"
else
  role=${db_output%%|*}
  rest=${db_output#*|}
  super=${rest%%|*}
  bypass=${rest#*|}
  if [[ "$super" == "true" ]]; then
    bad "应用连库角色 ${role} 是 superuser，RLS 会被完全绕过"
  elif [[ "$bypass" == "true" ]]; then
    bad "应用连库角色 ${role} 带 BYPASSRLS，RLS 会被完全绕过"
  else
    ok "应用连库角色 ${role}（非 superuser、非 BYPASSRLS）"
  fi
fi

# ---------- 6. 备份 ----------
info "6/8 最近备份"
if [[ ! -d backup ]]; then
  warn "backup/ 不存在：先在本地跑 ./backup.sh，否则出事没有回退点"
else
  recent=$(find backup -maxdepth 1 -name 'keeper-*.sql' -mtime -7 2>/dev/null | head -1)
  if [[ -z "$recent" ]]; then
    warn "backup/ 里没有 7 天内的备份：跑 ./backup.sh，并用 backend/scripts/verify_backup.sh 验一次能恢复"
  else
    mtime=$(stat -c %Y "$recent" 2>/dev/null || stat -f %m "$recent" 2>/dev/null || echo 0)
    age_h=$(( ( $(date +%s) - mtime ) / 3600 ))
    ok "最近备份：$(basename "$recent")（${age_h} 小时前）"
  fi
fi

# ---------- 7. 前端 PWA 产物 ----------
info "7/8 前端构建产物（PWA）"
missing=()
for artifact in frontend/dist/index.html frontend/dist/sw.js frontend/dist/manifest.webmanifest; do
  [[ -f "$artifact" ]] || missing+=("$artifact")
done
if [[ ${#missing[@]} -eq 0 ]]; then
  ok "dist 里 index.html / sw.js / manifest.webmanifest 都在"
else
  warn "缺构建产物：${missing[*]}（先在 frontend/ 跑 npm run build）"
fi

# ---------- 8. 端口暴露 ----------
info "8/8 数据库端口暴露"
if [[ -f docker-compose.yml ]] && grep -Eq '^[[:space:]]*-[[:space:]]*"?127\.0\.0\.1:5432:5432' docker-compose.yml; then
  warn "compose 仍把 5432 映射到本机（生产环境应删掉这段映射）"
else
  ok "数据库端口未对外映射"
fi

# ---------- 汇总 ----------
echo
echo "================ 预检结果 ================"
echo "关键失败：${CRITICAL}    警告：${WARN}"
if [[ "$FAIL" -gt 0 ]]; then
  echo "结论：❌ 现在上线会失败或不安全，先修上面标 ❌ 的项。"
  echo "（需要你本人操作的事项记在 docs/29-待决策清单.md）"
  exit 1
fi
echo "结论：✅ 关键项全部通过，可以执行 ./deploy.sh。"
exit 0
