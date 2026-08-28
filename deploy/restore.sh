#!/usr/bin/env bash
# =============================================================================
# restore.sh — 数据库 + 附件恢复脚本(服务器侧)
# 用于: ① 首次部署时把本地迁移数据导入阿里云; ② 故障后恢复 / 回滚数据。
#
# 用法:
#   sudo ./deploy/restore.sh /opt/csctalon/backups/csctalon-backup-xxx.tar.gz
#   sudo ./deploy/restore.sh <备份包> --env-file /opt/csctalon/.env
#   sudo ./deploy/restore.sh <备份包> --db-only      # 只恢复数据库, 不覆盖附件
#
# ⚠️ 警告: 恢复会覆盖当前数据库与附件, 恢复到"备份生成时刻"的数据状态,
#    备份之后产生的数据会丢失。恢复前建议先执行 backup.sh 保留现场。
# =============================================================================
set -euo pipefail

APP_DIR="/opt/csctalon"
ENV_FILE="$APP_DIR/.env"
DB_ONLY=0
COMPOSE_DIR="$(pwd)"

RESTORE_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    --db-only) DB_ONLY=1; shift ;;
    --pkg-dir) COMPOSE_DIR="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) RESTORE_FILE="$1"; shift ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "✘ 请用 root 或 sudo 执行" >&2; exit 1; fi
if [[ -z "$RESTORE_FILE" || ! -f "$RESTORE_FILE" ]]; then
  echo "✘ 用法: sudo ./deploy/restore.sh <备份包路径> [--env-file <env>] [--db-only]" >&2
  exit 1
fi
RESTORE_FILE="$(realpath "$RESTORE_FILE")"

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

var() { grep -E "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || echo "$3"; }
PG_USER="$(var "$ENV_FILE" POSTGRES_USER csctalon)"
PG_DB="$(var "$ENV_FILE" POSTGRES_DB csctalon)"
UPLOADS_DIR="$(var "$ENV_FILE" UPLOADS_DIR "$COMPOSE_DIR/backend/uploads")"
BROWSER_STATES_DIR="$(var "$ENV_FILE" BROWSER_STATES_DIR "$COMPOSE_DIR/data/browser_states")"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

c "解包备份: $RESTORE_FILE"
tar xzf "$RESTORE_FILE" -C "$STAGE"
[[ -f "$STAGE/csctalon.dump" ]] || { err "备份包缺少 csctalon.dump"; exit 1; }
[[ -f "$STAGE/backup.info" ]] && cat "$STAGE/backup.info"

# ---------- 1. 停应用(保留 db/redis) ----------
c "停止 web/backend 以安全恢复..."
if docker ps --format '{{.Names}}' | grep -q '^csctalon-web$'; then docker stop csctalon-web; fi
if docker ps --format '{{.Names}}' | grep -q '^csctalon-backend$'; then docker stop csctalon-backend; fi

# ---------- 2. 等 db 健康 ----------
for _ in $(seq 1 20); do
  docker exec csctalon-db pg_isready -U "$PG_USER" >/dev/null 2>&1 && break
  sleep 2
done
docker exec csctalon-db pg_isready -U "$PG_USER" >/dev/null 2>&1 || { err "数据库容器不可用"; exit 1; }

# ---------- 3. 恢复 PostgreSQL ----------
c "pg_restore 恢复数据库 ${PG_DB} (将覆盖现有数据)..."
docker cp "$STAGE/csctalon.dump" "csctalon-db:/tmp/csctalon_restore.dump"
# --clean --if-exists: 先清空再重建, 与备份结构保持一致
#
# ⚠️ pg_restore 已知无害错误容忍(2026-08-28):
#   groups/specialties 表的 ix_groups_name / ix_specialties_code / ix_specialties_name
#   在"create_all 环境"(本地/开发机, 唯一索引)与"alembic 迁移环境"(全新服务器, 唯一约束)
#   之间命名相同但对象类型不同。恢复时 --clean 阶段执行 DROP INDEX 会报:
#     "cannot drop index ... because constraint ... requires it"
#   这 3 个错误发生在 DROP 清理阶段, 数据导入(CREATE TABLE + COPY)不受影响,
#   恢复后对象会被正确重建为唯一索引。但 pg_restore 因此返回非 0 退出码,
#   在 set -e 下会中断脚本 → 后续"启动服务"不执行。
#   处理: 不因 pg_restore 退出码中断, 改为恢复后做数据完整性校验(见下)。
docker exec csctalon-db pg_restore -U "$PG_USER" -d "$PG_DB" --clean --if-exists --no-owner \
  /tmp/csctalon_restore.dump || true
docker exec csctalon-db rm -f /tmp/csctalon_restore.dump

# 数据完整性校验: 确认关键表确实恢复(而非因真实错误半途而废)
# 校验 users(账号) + refunds(核心业务表) 均有数据; 失败则中止, 避免"假成功"
_PGCHECK() { docker exec csctalon-db psql -U "$PG_USER" -d "$PG_DB" -tA -c "$1" 2>/dev/null | tr -d '[:space:]'; }
_CK_USERS="$(_PGCHECK 'SELECT count(*) FROM users;' 2>/dev/null || echo 0)"
_CK_REFS="$(_PGCHECK 'SELECT count(*) FROM refunds;' 2>/dev/null || echo 0)"
if [[ -z "$_CK_USERS" || "$_CK_USERS" -eq 0 || -z "$_CK_REFS" || "$_CK_REFS" -eq 0 ]]; then
  err "数据库恢复校验失败: users=$_CK_USERS refunds=$_CK_REFS (疑似 pg_restore 真实错误, 非已知 DROP 警告)"
  err "  查看完整 pg_restore 输出排查; 若为历史遗留脏库, 可先手动清空后重试:"
  err "  docker exec csctalon-db psql -U $PG_USER -d $PG_DB -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'"
  exit 1
fi
ok "数据库恢复完成 (users=$_CK_USERS, refunds=$_CK_REFS)"

# ---------- 4. 恢复附件(可选) ----------
if [[ $DB_ONLY -eq 0 ]]; then
  if [[ -d "$STAGE/uploads" && -n "$(ls -A "$STAGE/uploads" 2>/dev/null)" ]]; then
    c "恢复附件 → $UPLOADS_DIR"
    mkdir -p "$UPLOADS_DIR"
    cp -a "$STAGE/uploads/." "$UPLOADS_DIR/"
  fi
  if [[ -d "$STAGE/browser_states" && -n "$(ls -A "$STAGE/browser_states" 2>/dev/null)" ]]; then
    c "恢复登录态 → $BROWSER_STATES_DIR"
    mkdir -p "$BROWSER_STATES_DIR"
    cp -a "$STAGE/browser_states/." "$BROWSER_STATES_DIR/"
  fi
fi

# ---------- 5. 启动应用 ----------
c "启动服务..."
cd "$COMPOSE_DIR"
docker compose --env-file "$ENV_FILE" up -d

c "等待服务健康..."
HEALTHY=0
for _ in $(seq 1 40); do
  if docker ps --format '{{.Names}}' | grep -q '^csctalon-backend$' \
     && curl -sf -o /dev/null "http://127.0.0.1:8000/health"; then
    HEALTHY=1; break
  fi
  sleep 3
done
[[ $HEALTHY -eq 0 ]] && { err "服务未健康, 查看: docker compose --env-file $ENV_FILE logs"; exit 1; }

ok "恢复完成! 系统已运行在备份时间点数据上"
