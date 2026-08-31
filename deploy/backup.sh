#!/usr/bin/env bash
# =============================================================================
# backup.sh — 数据库 + 附件备份脚本(本地导出 / 服务器定时备份通用)
#
# 在**本地开发机**执行 = 导出迁移数据(再交给 upload.sh/restore.sh 导入服务器);
# 在**阿里云服务器**执行 = 日常/升级前备份(建议 cron 每日跑)。
#
# 备份内容:
#   ① PostgreSQL 全库 (pg_dump -Fc 自定义格式, 可增量恢复)
#   ② 后端附件 backend/uploads (审计贴图等)
#   ③ Playwright 登录态 browser_states (BMS/SCRM 登录, 避免重新扫码)
#
# 用法:
#   ./deploy/backup.sh                               # 输出到 ./backups, 保留 7 份
#   ./deploy/backup.sh --output /opt/csctalon/backups --keep 14
#   ./deploy/backup.sh --env-file /opt/csctalon/.env # 服务器场景: 读生产 .env 找目录/账号
#
# 产物: <output>/csctalon-backup-YYYYmmdd-HHMMSS.tar.gz
# 恢复: ./deploy/restore.sh <备份包>
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ENV=""
if [[ -f "/opt/csctalon/.env" ]]; then
  DEFAULT_ENV="/opt/csctalon/.env"
elif [[ -f "$(pwd)/.env" ]]; then
  DEFAULT_ENV="$(pwd)/.env"
fi

OUTPUT_DIR=""
KEEP=7
ENV_FILE="$DEFAULT_ENV"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT_DIR="${2:?}"; shift 2 ;;
    --keep) KEEP="${2:?}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "✘ 未知参数: $1" >&2; exit 1 ;;
  esac
done

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

# ---------- 参数默认值 ----------
if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="${APP_OUTPUT_DIR:-$SCRIPT_DIR/../backups}"
fi
mkdir -p "$OUTPUT_DIR"

# 从 .env 读取生产配置(找不到时用本地默认相对路径)
pg_user() { grep -E "^POSTGRES_USER=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || echo csctalon; }
pg_db()   { grep -E "^POSTGRES_DB="   "$1" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || echo csctalon; }
var()     { grep -E "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || echo "$3"; }

PG_USER="$(pg_user "${ENV_FILE:-/dev/null}")"
PG_DB="$(pg_db "${ENV_FILE:-/dev/null}")"
# ⚠️ 读取单数 UPLOAD_DIR/BROWSER_STATE_DIR(与 docker-compose 挂载变量/后端 settings 一致,
#    2026-08-30 已废弃复数 UPLOADS_DIR/BROWSER_STATES_DIR; 用旧名读不到 → 回退相对路径、备份漏附件)
UPLOAD_DIR="$(var "${ENV_FILE:-/dev/null}" UPLOAD_DIR "$SCRIPT_DIR/../backend/uploads")"
BROWSER_STATE_DIR="$(var "${ENV_FILE:-/dev/null}" BROWSER_STATE_DIR "$SCRIPT_DIR/../data/browser_states")"

# ---------- 前置检查 ----------
command -v docker >/dev/null 2>&1 || { err "需要 docker"; exit 1; }
if ! docker ps --format '{{.Names}}' | grep -q '^csctalon-db$'; then
  err "未找到运行中的 csctalon-db 容器(服务是否已启动?)"; exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
STAGE="$(mktemp -d)"
BACKUP_FILE="$OUTPUT_DIR/csctalon-backup-${TS}.tar.gz"

# ---------- 1. PG dump ----------
c "pg_dump 数据库 ${PG_DB} (user=${PG_USER})..."
docker exec csctalon-db pg_dump -U "$PG_USER" -d "$PG_DB" -Fc -f "/tmp/csctalon_${TS}.dump"
docker cp "csctalon-db:/tmp/csctalon_${TS}.dump" "$STAGE/csctalon.dump"
docker exec csctalon-db rm -f "/tmp/csctalon_${TS}.dump"
ok "数据库导出完成: $(du -h "$STAGE/csctalon.dump" | cut -f1)"

# ---------- 2. 附件打包 ----------
c "打包附件 uploads=${UPLOAD_DIR} browser_states=${BROWSER_STATE_DIR}"
mkdir -p "$STAGE/uploads" "$STAGE/browser_states"
if [[ -d "$UPLOAD_DIR" && -n "$(ls -A "$UPLOAD_DIR" 2>/dev/null)" ]]; then
  cp -a "$UPLOAD_DIR/." "$STAGE/uploads/"
fi
if [[ -d "$BROWSER_STATE_DIR" && -n "$(ls -A "$BROWSER_STATE_DIR" 2>/dev/null)" ]]; then
  cp -a "$BROWSER_STATE_DIR/." "$STAGE/browser_states/"
fi

# 元信息
{
  echo "created_at=$(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "pg_user=$PG_USER"
  echo "pg_db=$PG_DB"
  echo "version=$(git -C "$SCRIPT_DIR/.." describe --tags --always 2>/dev/null || cat "/opt/csctalon/current/VERSION.tmp" 2>/dev/null || echo '')"
} > "$STAGE/backup.info"

# ---------- 3. 打包输出 ----------
c "生成备份包: $BACKUP_FILE"
tar czf "$BACKUP_FILE" -C "$STAGE" csctalon.dump uploads browser_states backup.info
rm -rf "$STAGE"
ok "备份完成: $(du -h "$BACKUP_FILE" | cut -f1)"

# ---------- 4. 保留最近 KEEP 份 ----------
cd "$OUTPUT_DIR"
ls -1t csctalon-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  c "清理过期备份: $old"
  rm -f "$old"
done
ok "当前保留 ${KEEP} 份最近备份于 $OUTPUT_DIR"
