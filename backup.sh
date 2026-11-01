#!/usr/bin/env bash
# 备份数据库。产物在 backup/，不进 git。
#
# 用法：./backup.sh [输出目录]
set -euo pipefail

cd "$(dirname "$0")"

OUT_DIR="${1:-backup}"
STAMP=$(date +%Y%m%d-%H%M%S)
FILE="$OUT_DIR/keeper-$STAMP.sql"

mkdir -p "$OUT_DIR"

echo "==> 导出数据库到 $FILE"
docker compose exec -T db pg_dump -U keeper -d keeper --clean --if-exists > "$FILE"

# 顺手清掉 30 天前的旧备份，只留最近的
find "$OUT_DIR" -name 'keeper-*.sql' -mtime +30 -delete

SIZE=$(du -h "$FILE" | cut -f1)
# 变量名后面紧跟中文标点时必须加花括号：不加的话 bash 会把「（」当成变量名的一部分，
# 报 "FILE（: unbound variable"，备份脚本自己先挂掉。（2026-11-02 实测踩到）
echo "完成：${FILE}（${SIZE}）"
echo "恢复命令：docker compose exec -T db psql -U keeper -d keeper < ${FILE}"
