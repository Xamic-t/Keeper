#!/usr/bin/env bash
# 备份恢复演练：把一份 pg_dump 产物恢复到一个**临时库**，验证它能用。
#
# 为什么要有这个脚本：`backup.sh` 只证明"能导出来"，不证明"能恢复回去"。
# 真正出事那天你才会第一次跑恢复——那是最不该第一次跑的时机。
#
# 用法：
#   ./backend/scripts/verify_backup.sh backup/keeper-20261103-005115.sql
#   ./backend/scripts/verify_backup.sh <备份文件> <目标库名>
#
# 安全设计（三条，都是硬性的）：
#   ① 目标库名必须含 `_restore_check`，否则拒绝执行——防手滑恢复覆盖生产库
#   ② 目标库名等于 `keeper` 时直接退出
#   ③ 无论成功失败，临时库都会被 drop（trap）
#
# 只读生产：全程只读备份文件，只在临时库里写。
set -euo pipefail

cd "$(dirname "$0")/../.."   # 仓库根

BACKUP_FILE="${1:-}"
TARGET_DB="${2:-keeper_restore_check_$(date +%Y%m%d-%H%M%S)}"

# 13 张表：users + 12 张业务表（docs/06 5.x）。少一张说明备份不完整。
EXPECTED_TABLES=(
  users persons relationships facts tags events reminders todos ledger
  raw_inputs chat_segments extraction_logs notification_channels
)
# 12 张必须开 RLS 的表（users 按设计不开，见 ARCHITECTURE 15.5）
RLS_TABLES=(
  persons relationships facts tags events reminders todos ledger
  raw_inputs chat_segments extraction_logs notification_channels
)

fail() { echo "❌ $*" >&2; exit 1; }
info() { echo "==> $*"; }

# ---------- 入参与安全闸门 ----------
[[ -n "$BACKUP_FILE" ]] || fail "用法：$0 <备份文件> [目标库名（必须含 _restore_check）]"
[[ -f "$BACKUP_FILE" ]] || fail "备份文件不存在：$BACKUP_FILE"
[[ -s "$BACKUP_FILE" ]] || fail "备份文件是空的：$BACKUP_FILE"

[[ "$TARGET_DB" == *"_restore_check"* ]] || \
  fail "拒绝执行：目标库名 '$TARGET_DB' 不含 _restore_check（这里只允许往临时库恢复）"
[[ "$TARGET_DB" != "keeper" ]] || fail "拒绝执行：不允许把生产库 keeper 当恢复目标"
[[ "$TARGET_DB" != "postgres" ]] || fail "拒绝执行：不允许往 postgres 维护库恢复"

# 备份文件得像一份 pg_dump 产物，别把别的 sql 灌进来
head -n 20 "$BACKUP_FILE" | grep -q "PostgreSQL database dump" || \
  fail "这个文件不像 pg_dump 产物（缺 'PostgreSQL database dump' 头）"

PSQL_BASE=(docker compose exec -T db psql -U keeper)
# 变量后面紧跟中文标点时必须用 ${}：不加的话 bash 会把「（」并进变量名，
# 报 "BACKUP_FILE（: unbound variable"（backup.sh 2026-11-02 踩过同一个坑）
info "备份文件：${BACKUP_FILE}（$(du -h "$BACKUP_FILE" | cut -f1)）"
info "临时库：  $TARGET_DB"

cleanup() {
  # 无论成败都要清掉临时库，否则每跑一次就多一个库，最后磁盘满
  "${PSQL_BASE[@]}" -d postgres -q -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE)" >/dev/null 2>&1 || \
    echo "⚠️  临时库 $TARGET_DB 没删掉，请手工 drop" >&2
}
trap cleanup EXIT

# ---------- 建库 + 恢复 ----------
"${PSQL_BASE[@]}" -d postgres -q -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE \"$TARGET_DB\"" || fail "建临时库失败（名字重复？换个库名再试）"

info "恢复中（ON_ERROR_STOP=1，任何一条语句报错就中止）…"
# -o /dev/null：恢复时 pg_dump 的 setval 结果会刷屏，错误仍然走 stderr
if ! "${PSQL_BASE[@]}" -d "$TARGET_DB" -q -o /dev/null -v ON_ERROR_STOP=1 < "$BACKUP_FILE"; then
  fail "恢复失败：见上面的 psql 报错原文"
fi

# ---------- 校验 ----------
info "校验表清单"
MISSING=()
for table in "${EXPECTED_TABLES[@]}"; do
  exists=$("${PSQL_BASE[@]}" -d "$TARGET_DB" -tAc \
    "SELECT to_regclass('public.$table') IS NOT NULL")
  [[ "$exists" == "t" ]] || MISSING+=("$table")
done
[[ ${#MISSING[@]} -eq 0 ]] || fail "恢复后缺表：${MISSING[*]}"

info "校验 RLS（12 张业务表必须 ENABLE + FORCE）"
RLS_BAD=()
for table in "${RLS_TABLES[@]}"; do
  state=$("${PSQL_BASE[@]}" -d "$TARGET_DB" -tAc \
    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
     WHERE relname = '$table' AND relkind = 'r'")
  [[ "$state" == "t" ]] || RLS_BAD+=("$table")
done
[[ ${#RLS_BAD[@]} -eq 0 ]] || fail "恢复后 RLS 不完整：${RLS_BAD[*]}"

info "校验策略数量"
POLICY_COUNT=$("${PSQL_BASE[@]}" -d "$TARGET_DB" -tAc \
  "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")
info "策略条数：${POLICY_COUNT}（期望 ≥ ${#RLS_TABLES[@]}）"
[[ "$POLICY_COUNT" -ge ${#RLS_TABLES[@]} ]] || fail "策略条数少于业务表数，备份可能来自旧版本"

info "关键表行数（与生产对比是否一致由你判断，这里只打印）"
"${PSQL_BASE[@]}" -d "$TARGET_DB" -c \
  "SELECT 'users' AS table_name, count(*) FROM users
   UNION ALL SELECT 'persons', count(*) FROM persons
   UNION ALL SELECT 'facts', count(*) FROM facts
   UNION ALL SELECT 'events', count(*) FROM events
   UNION ALL SELECT 'reminders', count(*) FROM reminders
   ORDER BY table_name"

# 迁移版本：恢复回来的 alembic_version 必须和生产一致，否则说明备份比代码老
REV=$("${PSQL_BASE[@]}" -d "$TARGET_DB" -tAc "SELECT version_num FROM alembic_version")
LIVE_REV=$("${PSQL_BASE[@]}" -d keeper -tAc "SELECT version_num FROM alembic_version")
info "备份里的迁移版本：$REV"
info "生产当前迁移版本：$LIVE_REV"
if [[ "$REV" != "$LIVE_REV" ]]; then
  echo "⚠️  迁移版本不一致：备份比代码旧，恢复后要先跑 alembic upgrade head" >&2
fi

echo "✅ 恢复演练通过（临时库已清理）"
