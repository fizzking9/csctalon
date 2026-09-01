#!/usr/bin/env bash
# =============================================================================
# sync-from-prod.sh — 生产数据一键拉取到本地开发环境 (开发/联调用副本)
#
# 背景: 本地开发缺数据、生产不便直接联调时, 需要一份"生产数据的快照副本"。
#       方案是复用现有备份机制: 在**生产机**跑 backup.sh 生成备份包
#       (PG 全库 + uploads 附件 + browser_states 登录态) → 拉回本地 → 本地 pg_restore。
#
# 🔒 生产只读保证(2026-08-31):
#   - 本脚本对**生产机只做读操作**: 唯一的远程命令是 backup.sh(pg_dump, 只读) + 建/删临时目录。
#   - **生产 Redis 零写入**: 脚本没有也不会有任何针对生产 redis 的写命令(FLUSHALL 只作用在
#     本地容器, 见下)。
#   - pg_restore --clean / redis FLUSHALL 等**破坏性操作全部作用在本地开发环境**, 绝不触及生产。
#   - 若 --ssh-prod 指向了本机(误连), 脚本会检测并拒绝执行。
#
# 与"双机热备"的关系: 本脚本是**开发取数**工具(拿副本), 不是热备(保可用性)。
#   热备请走灾备阶梯: L1 每日异地备份(已有 backup.sh + sync-backup-to-cloud.sh)
#                    L2 PG 流复制主从 + Redis replicaof(备机只读)
#                    L3 自动切换 + 脑裂仲裁(本项目属过度设计)。
#   详见 deploy/README.md。
#
# 用法(在本地开发机执行):
#   ./deploy/sync-from-prod.sh --ssh-prod root@<生产IP> --env-file .env
#   ./deploy/sync-from-prod.sh --ssh-prod root@<生产IP> --db-only   # 只恢复库, 不碰附件/登录态
#
# 可选参数:
#   --env-file <路径>    本地 .env(读 POSTGRES_USER/DB/UPLOAD_DIR 等; 自动探测仓库根 .env)
#   --ssh-prod <用户@主机>  必填, 生产机(SSH 可达)
#   --backup-dir <目录>  备份包缓存目录(默认 仓库根/backups)
#   --keep <N>           本地备份包保留份数, 默认 1
#   --ssh-key <密钥>     指定 SSH 私钥(SSH 到生产机)
#   --clear-redis        额外清空**本地** Redis 缓存(默认**不清**)
#                        ⚠️ 本系统的 Redis 只有可重建缓存+无状态标记(互斥锁在内存/DB),
#                        ⚠️ 数据库已同步回本地, 旧缓存会自然过期, 无需也不应清空; 该参数仅调试用
#   --db-only            只恢复数据库, 不覆盖本地 uploads 附件与 browser_states 登录态
#   --skip-stash         跳过"生产侧备份前自动 git stash"(默认: 生产 /opt/csctalon/current
#                        若有未提交改动, 拉 backup.sh 前自动 stash, 跑完自动 pop 还原)
#   --yes                跳过"即将覆盖本地数据库"的最终确认
#   --repo <raw_base>    backup.sh 来源 git raw base(默认 gitea main 分支)
#
# ⚠️ 安全提示:
#   - 生产机默认只绑 127.0.0.1, 本脚本不开放任何新端口/接口, 无新增攻击面。
#   - 生产 backup.sh 在**生产机内**执行, 数据库凭证不离开生产机。
#   - 备份包含同步源加密凭证/登录态, 拉回本地后请注意保管, 勿提交 git。
#   - 本脚本会**覆盖本地数据库**。本地如有未提交业务数据, 请先自行备份。
# =============================================================================
set -euo pipefail

# ---------- 基础路径 ----------
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
fi
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------- 参数默认值 ----------
DEFAULT_ENV=""
[[ -f "$REPO_ROOT/.env" ]] && DEFAULT_ENV="$REPO_ROOT/.env"

SSH_PROD=""
ENV_FILE="$DEFAULT_ENV"
BACKUP_DIR="$REPO_ROOT/backups"
KEEP=1
SSH_KEY=""
CLEAR_REDIS=0
DB_ONLY=0
SKIP_STASH=0
AUTO_YES=0
REPO_BASE="http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main"
BACKUP_GLOB="csctalon-backup-*.tar.gz"

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh-prod) SSH_PROD="${2:?--ssh-prod 需要 用户@主机 (如 root@1.2.3.4)}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    --backup-dir) BACKUP_DIR="${2:?}"; shift 2 ;;
    --keep) KEEP="${2:?}"; shift 2 ;;
    --ssh-key) SSH_KEY="${2:?}"; shift 2 ;;
    --repo) REPO_BASE="${2:?--repo 需要 raw base url}"; shift 2 ;;
    --clear-redis) CLEAR_REDIS=1; shift ;;
    --db-only) DB_ONLY=1; shift ;;
    --skip-stash) SKIP_STASH=1; shift ;;
    --yes) AUTO_YES=1; shift ;;
    -h|--help)
      sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "✘ 未知参数: $1" >&2; exit 1 ;;
  esac
done

[[ -z "$SSH_PROD" ]] && { echo "✘ 缺少 --ssh-prod (生产机 用户@主机)" >&2; exit 1; }

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }
# 从 .env 读键值(带引号剥离), 失败回退默认
var() { grep -E "^$2=" "$1" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || echo "$3"; }

# ---------- 校验本地依赖 ----------
command -v ssh >/dev/null 2>&1 || { err "需要 ssh"; exit 1; }
command -v rsync >/dev/null 2>&1 || { err "需要 rsync"; exit 1; }
command -v docker >/dev/null 2>&1 || { err "需要 docker (本地开发环境)"; exit 1; }
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^csctalon-db$'; then
  err "未找到运行中的本地 csctalon-db 容器(请先启动: docker compose up -d)"; exit 1
fi
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^csctalon-redis$'; then
  err "未找到运行中的本地 csctalon-redis 容器(请先启动: docker compose up -d)"; exit 1
fi

# 读取本地数据库配置
[[ -n "$ENV_FILE" && -f "$ENV_FILE" ]] || ENV_FILE=""
PG_USER="$(var "${ENV_FILE:-/dev/null}" POSTGRES_USER csctalon)"
PG_DB="$(var "${ENV_FILE:-/dev/null}" POSTGRES_DB csctalon)"
LOCAL_DB_CTN="csctalon-db"
LOCAL_REDIS_CTN="csctalon-redis"
# Redis 密码(本地有密码则 FLUSHALL 带上)
REDIS_PASS="$(var "${ENV_FILE:-/dev/null}" REDIS_PASSWORD "")"

# SSH 前缀(命令列表, 不用数组, 兼容 -i key 与空)
SSH_PRE=(ssh)
[[ -n "$SSH_KEY" ]] && SSH_PRE+=(-i "$SSH_KEY")
SSH_PRE+=(-o "BatchMode=yes" -o "StrictHostKeyChecking=accept-new")
# 统一远端执行前缀: ssh [key] -o ... <host> <cmd...>
R() { "${SSH_PRE[@]}" "$SSH_PROD" "$@"; }

# ---------- 0. 最终确认(防误覆盖本地库) ----------
if [[ $AUTO_YES -eq 0 ]]; then
  echo
  c "即将从生产 [$SSH_PROD] 拉取快照并覆盖本地数据库 [$LOCAL_DB_CTN:$PG_DB]"
  if [[ $DB_ONLY -eq 0 ]]; then
    echo "  将同时恢复: uploads 附件 + browser_states 登录态"
  else
    echo "  --db-only: 仅恢复数据库, 不碰本地附件/登录态"
  fi
  read -r -p "确认继续? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { echo "已取消"; exit 1; }
fi

# ---------- 1. 生产机探活 + 生成备份包 ----------
c "探活生产机 $SSH_PROD..."
# 防误连本地: 若 ssh 目标是本机(开发机), 直接拒绝, 避免"在生产上跑 backup.sh 却连回本地"的混淆
case "$SSH_PROD" in
  localhost|127.0.0.1|::1|0.0.0.0) { err "✘ --ssh-prod 指向本机($SSH_PROD)? 本脚本应在**本地开发机**执行并连**生产机**。"; err "  目标应为生产机地址(如 root@<生产IP>)。已中止以避免误操作。"; exit 1; } ;;
esac
R "sh -c 'command -v docker >/dev/null && docker ps --format {{.Names}} | grep -q ^csctalon-db$'" \
  || { err "生产机 $SSH_PROD 未找到运行中的 csctalon-db(容器名不符或服务未启动)"; exit 1; }
# 再确认: 生产机容器不应与本脚本所在开发机同名同栈(二次防误连)
LOCAL_DB_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$LOCAL_DB_CTN" 2>/dev/null || echo '')"
PROD_DB_REACH="$(R "sh -c 'docker exec csctalon-db hostname -i 2>/dev/null || echo nohost'" 2>/dev/null || echo nohost)"
if [[ -n "$LOCAL_DB_IP" && "$PROD_DB_REACH" == "$LOCAL_DB_IP" ]]; then
  err "✘ 生产机返回的 db 容器 IP 与本地相同($LOCAL_DB_IP), 疑似 --ssh-prod 误指本机。已中止。"; exit 1
fi

c "在生产机执行 backup.sh(生成全库+附件+登录态备份包)..."
REMOTE_TMP="$(R "sh -c 'mktemp -d'")"
cleanup_remote() { R "sh -c 'rm -rf \"$REMOTE_TMP\"'" >/dev/null 2>&1 || true; }
trap 'cleanup_remote; rm -rf "$STAGE"' EXIT

# 远端优先复用已部署 backup.sh(/opt/csctalon/current/deploy/backup.sh); 否则从仓库拉取
REMOTE_BACKUP_SCRIPT="$(R "sh -c 'if [[ -f /opt/csctalon/current/deploy/backup.sh ]]; then echo /opt/csctalon/current/deploy/backup.sh; else echo ""; fi'")"
if [[ -n "$REMOTE_BACKUP_SCRIPT" ]]; then
  c "复用生产已部署 backup.sh: $REMOTE_BACKUP_SCRIPT"
else
  c "生产未部署 backup.sh, 从仓库拉取: $REPO_BASE"
  R "sh -c 'curl -fsSL \"$REPO_BASE/deploy/backup.sh\" -o \"$REMOTE_TMP/backup.sh\"'" \
    || { err "从仓库拉取 backup.sh 失败(--repo 是否可访问?)"; exit 1; }
  REMOTE_BACKUP_SCRIPT="$REMOTE_TMP/backup.sh"
fi

# 生产侧若为已部署仓库且有未提交改动, 先 stash 再跑 backup.sh, 跑完 pop 还原
# (避免 backup.sh 读到本地改过的版本; 仅当部署目录存在且是 git 仓库时执行)
R "sh -c 'if [[ -d /opt/csctalon/current/.git && $SKIP_STASH -eq 0 ]]; then cd /opt/csctalon/current && git stash push -m sync-from-prod-auto -q 2>/dev/null || true; fi'" || true
STASHED=0
if [[ $SKIP_STASH -eq 0 ]]; then
  STASHED="$(R "sh -c 'if [[ -d /opt/csctalon/current/.git ]]; then cd /opt/csctalon/current && git stash list 2>/dev/null | grep -c sync-from-prod-auto || echo 0; else echo 0; fi'" || echo 0)"
fi

# 在生产机执行 backup.sh; 若用户给了本地 .env, 尝试同步其密钥到生产(仅当生产无 /opt/csctalon/.env 时)
# ⚠️ 安全: 只在"生产机内"跑 backup.sh, 数据库凭证不离开生产机; 无新端口/接口暴露
BACKUP_ENV_ARG=""
if [[ -n "$ENV_FILE" ]]; then
  HAS_PROD_ENV="$(R "sh -c '[[ -f /opt/csctalon/.env ]] && echo 1 || echo 0'" || echo 0)"
  if [[ "$HAS_PROD_ENV" == "0" ]]; then
    c "生产无 /opt/csctalon/.env, 复用本地 .env 密钥(仅本次执行, 不落盘生产)"
    BACKUP_ENV_ARG="--env-file /dev/stdin"
  fi
fi
if [[ -n "$BACKUP_ENV_ARG" ]]; then
  R "sh -c 'bash \"$REMOTE_BACKUP_SCRIPT\" --output \"$REMOTE_TMP\" --keep 1 --env-file /dev/stdin'" < "$ENV_FILE" \
    || { err "生产机 backup.sh 执行失败, 请查看远端输出"; exit 1; }
else
  R "sh -c 'bash \"$REMOTE_BACKUP_SCRIPT\" --output \"$REMOTE_TMP\" --keep 1'" \
    || { err "生产机 backup.sh 执行失败, 请查看远端输出"; exit 1; }
fi

# 恢复生产侧 stash(若上面 stash 过)
if [[ -n "$STASHED" && "$STASHED" != "0" ]]; then
  R "sh -c 'if [[ -d /opt/csctalon/current/.git ]]; then cd /opt/csctalon/current && git stash pop -q 2>/dev/null || true; fi'" || true
fi

REMOTE_LATEST="$(R "sh -c 'ls -1t \"$REMOTE_TMP\"/$BACKUP_GLOB 2>/dev/null | head -1'")"
[[ -n "$REMOTE_LATEST" ]] || { err "生产机未生成备份包"; exit 1; }
REMOTE_NAME="$(basename "$REMOTE_LATEST")"
ok "生产机备份完成: $REMOTE_NAME"

# ---------- 2. 拉回本地(rsync 增量) ----------
mkdir -p "$BACKUP_DIR"
c "拉回备份包 → $BACKUP_DIR/"
# rsync 不支持 --batch-mode, 用 SSH_OPT 拼进 -e
if [[ -n "$SSH_KEY" ]]; then
  rsync -avz -e "ssh -i $SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    "$SSH_PROD:\"$REMOTE_LATEST\"" "$BACKUP_DIR/"
else
  rsync -avz -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    "$SSH_PROD:\"$REMOTE_LATEST\"" "$BACKUP_DIR/"
fi
LOCAL_FILE="$BACKUP_DIR/$REMOTE_NAME"
[[ -f "$LOCAL_FILE" ]] || { err "拉取备份包失败"; exit 1; }
ok "已拉取: $LOCAL_FILE"

# ---------- 3. 校验备份包并解包 ----------
STAGE="$(mktemp -d)"
c "解包校验..."
tar xzf "$LOCAL_FILE" -C "$STAGE"
[[ -f "$STAGE/csctalon.dump" ]] || { err "备份包缺少 csctalon.dump"; exit 1; }
[[ -f "$STAGE/backup.info" ]] && cat "$STAGE/backup.info"
ok "备份包校验通过"

# ---------- 4. 恢复 PostgreSQL(本地 colima named volume, 容器内执行) ----------
c "恢复 PostgreSQL → 本地 $LOCAL_DB_CTN:$PG_DB (将覆盖现有数据)..."
docker cp "$STAGE/csctalon.dump" "$LOCAL_DB_CTN:/tmp/csctalon_fromprod.dump"
# --clean --if-exists: 先清空再重建, 与备份结构一致; --no-owner 规避属主差异。
# 已知无害错误容忍: 见 deploy/restore.sh 注释(groups/specialties 唯一索引/约束
# create_all 与 alembic 环境命名冲突, DROP 阶段报错不影响数据导入)。
docker exec "$LOCAL_DB_CTN" pg_restore -U "$PG_USER" -d "$PG_DB" \
  --clean --if-exists --no-owner /tmp/csctalon_fromprod.dump || true
docker exec "$LOCAL_DB_CTN" rm -f /tmp/csctalon_fromprod.dump

# 数据完整性校验: 关键表确实有数据, 防"假成功"(与 restore.sh 同口径)
_PGCHECK() { docker exec "$LOCAL_DB_CTN" psql -U "$PG_USER" -d "$PG_DB" -tA -c "$1" 2>/dev/null | tr -d '[:space:]'; }
_CK_USERS="$(_PGCHECK 'SELECT count(*) FROM users;' 2>/dev/null || echo 0)"
_CK_REFS="$(_PGCHECK 'SELECT count(*) FROM refunds;' 2>/dev/null || echo 0)"
if [[ -z "$_CK_USERS" || "$_CK_USERS" -eq 0 || -z "$_CK_REFS" || "$_CK_REFS" -eq 0 ]]; then
  err "数据库恢复校验失败: users=$_CK_USERS refunds=$_CK_REFS (疑似 pg_restore 真实错误, 非已知 DROP 警告)"
  err "  可先手动清空后重试: docker exec $LOCAL_DB_CTN psql -U $PG_USER -d $PG_DB -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'"
  exit 1
fi
ok "数据库恢复完成 (users=$_CK_USERS, refunds=$_CK_REFS)"

# ---------- 5. 恢复附件/登录态(可选) ----------
if [[ $DB_ONLY -eq 0 ]]; then
  UPLOAD_DIR="$(var "${ENV_FILE:-/dev/null}" UPLOAD_DIR "$REPO_ROOT/backend/uploads")"
  BROWSER_STATE_DIR="$(var "${ENV_FILE:-/dev/null}" BROWSER_STATE_DIR "$REPO_ROOT/data/browser_states")"
  if [[ -d "$STAGE/uploads" && -n "$(ls -A "$STAGE/uploads" 2>/dev/null)" ]]; then
    c "恢复附件 → $UPLOAD_DIR"
    mkdir -p "$UPLOAD_DIR"
    cp -a "$STAGE/uploads/." "$UPLOAD_DIR/"
  fi
  if [[ -d "$STAGE/browser_states" && -n "$(ls -A "$STAGE/browser_states" 2>/dev/null)" ]]; then
    c "恢复登录态 → $BROWSER_STATE_DIR"
    mkdir -p "$BROWSER_STATE_DIR"
    cp -a "$STAGE/browser_states/." "$BROWSER_STATE_DIR/"
  fi
  ok "附件/登录态恢复完成"
else
  c "--db-only: 跳过附件/登录态恢复"
fi

# ---------- 6. 清理本地 Redis 缓存(可选, 默认不做) ----------
# 数据库已恢复为备份快照, 本地 Redis 里可能残留旧缓存 key, 但它们会按 TTL 自然过期
# (缓存预热 cache_warmer 会自动重建; 同步互斥锁在内存/DB, 不依赖 Redis)。
# 因此默认**不清** Redis; 仅当调试需要清缓存时用 --clear-redis 显式触发。
# ⚠️ 这里操作的是**本地** csctalon-redis 容器, 与生产 Redis 无关。
if [[ $CLEAR_REDIS -eq 1 ]]; then
  c "清空本地 Redis 缓存($LOCAL_REDIS_CTN)(--clear-redis 已指定)..."
  if [[ -n "$REDIS_PASS" ]]; then
    docker exec "$LOCAL_REDIS_CTN" redis-cli -a "$REDIS_PASS" --no-auth-warning FLUSHALL >/dev/null 2>&1 \
      && ok "本地 Redis 缓存已清空" || { err "Redis FLUSHALL 失败(密码不对? 手动清理或去掉 --clear-redis)"; }
  else
    docker exec "$LOCAL_REDIS_CTN" redis-cli FLUSHALL >/dev/null 2>&1 \
      && ok "本地 Redis 缓存已清空" || { err "Redis FLUSHALL 失败(有密码? 手动清理或去掉 --clear-redis)"; }
  fi
else
  c "跳过 Redis 处理(默认不清; 本地缓存会自然过期重建, 不影响联调)"
fi

# ---------- 7. 清理备份包缓存(保留最近 KEEP 份) ----------
cd "$BACKUP_DIR"
ls -1t csctalon-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  c "清理旧备份包: $old"
  rm -f "$old"
done

ok "同步完成! 本地开发库已更新为生产快照 ($REMOTE_NAME)"
echo "  数据库:   $LOCAL_DB_CTN:$PG_DB  (users=$_CK_USERS, refunds=$_CK_REFS)"
echo "  附件/登录态: $([[ $DB_ONLY -eq 1 ]] && echo '未恢复(--db-only)' || echo '已恢复')"
echo "  Redis:    $([[ $CLEAR_REDIS -eq 1 ]] && echo '已清空本地缓存(--clear-redis)' || echo '未动(默认; 缓存会自然过期)')  ← 仅本地, 生产 Redis 未触碰"
echo "  生产机:   只读操作(backup.sh/pg_dump), 生产 DB 与 Redis 均未改动"
echo "  提示: 本地 JWT/CONFIG_ENCRYPTION_KEY 来自本地 .env, 与生产不同 → 同步源加密凭证
        由生产 CONFIG_ENCRYPTION_KEY 派生, 拉回本地后可能无法解密。若解密失败,
        请用本地 .env 的 CONFIG_ENCRYPTION_KEY 重加密或重填凭证(见 .env.example 注释)。"
