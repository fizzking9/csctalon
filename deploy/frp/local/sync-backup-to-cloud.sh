#!/usr/bin/env bash
# =============================================================================
# sync-backup-to-cloud.sh — 本地备份 → 云端异地备份 (frp 方案的灾备闭环)
#
# 背景: frp 方案下业务数据全部在本地, 云端只做转发。若本地磁盘损坏/整机丢失,
#       数据就没了。因此**必须**把备份推到异地(云端对象存储/另一台机器)。
#       本脚本负责: ① 先跑 backup.sh 生成最新备份包 → ② rsync 增量推到云端。
#
# 用法(在本地服务器, 可配置 cron 每日):
#   方式一: git raw 一条命令(gitea 默认; 需要 --repo 拉取 backup.sh)
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/sync-backup-to-cloud.sh | \
#       sudo bash -s -- --dest root@<云端IP>:/backup/csctalon --env-file /opt/csctalon/.env
#   方式二: 本地脚本(已部署仓库, 推荐 cron 用)
#     bash deploy/frp/local/sync-backup-to-cloud.sh \
#       --dest root@<云端IP>:/backup/csctalon \
#       --env-file /opt/csctalon/.env \
#       --output /opt/csctalon/backups --keep 14
#
# 可选:
#   --repo <raw_base>       backup.sh 来源 git raw base(默认 gitea main 分支)
#   --ssh-key ~/.ssh/cloud_backup   指定 SSH 密钥
#   --no-local-backup               跳过本地 backup.sh, 只推送已有备份(已有备份时用)
#   --prune                         同步时删除云端多余旧备份(与本地 keep 保持一致)
#
# cron 示例(本地 crontab -e):
#   30 3 * * * bash /opt/csctalon/current/deploy/frp/local/sync-backup-to-cloud.sh \
#     --dest root@<云端IP>:/backup/csctalon --env-file /opt/csctalon/.env >> /var/log/csctalon-syncbackup.log 2>&1
#
# 恢复: 从云端拉回备份包 → 本地 deploy/restore.sh <备份包> 即可。
# =============================================================================
set -euo pipefail

# 脚本所在目录: 正常执行用 BASH_SOURCE; curl 管道时 BASH_SOURCE 为空 → 用 $0 兜底
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
fi
# 默认 env: 生产机优先读 /opt/csctalon/.env, 本地开发回退当前目录 .env
DEFAULT_ENV=""
[[ -f "/opt/csctalon/.env" ]] && DEFAULT_ENV="/opt/csctalon/.env"
[[ -z "$DEFAULT_ENV" && -f "$(pwd)/.env" ]] && DEFAULT_ENV="$(pwd)/.env"

DEST=""
ENV_FILE="$DEFAULT_ENV"
OUTPUT_DIR=""
KEEP=7
SSH_KEY=""
DO_LOCAL_BACKUP=1
PRUNE=0
# backup.sh 来源 git raw base(默认 gitea main 分支)
REPO_BASE="http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main"

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST="${2:?--dest 需要 用户@主机:路径 (如 root@1.2.3.4:/backup/csctalon)}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    --output) OUTPUT_DIR="${2:?}"; shift 2 ;;
    --keep) KEEP="${2:?}"; shift 2 ;;
    --ssh-key) SSH_KEY="${2:?}"; shift 2 ;;
    --repo) REPO_BASE="${2:?--repo 需要 raw base url}"; shift 2 ;;
    --no-local-backup) DO_LOCAL_BACKUP=0; shift ;;
    --prune) PRUNE=1; shift ;;
    -h|--help)
      cat >&2 <<'HELP'
用法: sync-backup-to-cloud.sh  --dest <用户@主机:路径> [--env-file .env] [--output 备份目录] [--keep N]

本地备份推送到云端异地备份(frp 方案灾备闭环)。内部: 先 backup.sh → rsync 增量推送。

git raw 一条命令:
  curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/sync-backup-to-cloud.sh | \
    sudo bash -s -- --dest root@<云端IP>:/backup/csctalon --env-file /opt/csctalon/.env

参数:
  --dest <用户@主机:路径>  必填, 推送目标(如 root@1.2.3.4:/backup/csctalon)
  --env-file <路径>        .env 路径(自动探测 /opt/csctalon/.env 或当前目录 .env)
  --output <目录>          本地备份输出目录(默认 仓库根/backups)
  --keep <N>               本地保留份数, 默认 7
  --ssh-key <密钥>         指定 SSH 密钥
  --no-local-backup        跳过本地 backup.sh, 只推送已有备份
  --prune                  清理云端多余旧备份(与 keep 一致)
  --repo <raw_base>        backup.sh 来源 git raw base(默认 gitea main 分支)
HELP
      exit 0 ;;
    *) echo "✘ 未知参数: $1"; exit 1 ;;
  esac
done

[[ -z "$DEST" ]] && { echo "✘ 缺少 --dest (目标 用户@主机:路径)"; exit 1; }
[[ -z "$OUTPUT_DIR" ]] && OUTPUT_DIR="$SCRIPT_DIR/../../../backups"

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

# ---------- 1. 定位 backup.sh(本地部署目录 或 git raw 拉取) ----------
BACKUP_SCRIPT="$SCRIPT_DIR/../../../deploy/backup.sh"
if [[ ! -f "$BACKUP_SCRIPT" ]]; then
  c "本地未找到 backup.sh, 从仓库拉取: $REPO_BASE"
  TMP_DIR="$(mktemp -d)"
  trap '[[ -n "${TMP_DIR:-}" ]] && rm -rf "$TMP_DIR"' EXIT
  curl -fsSL "$REPO_BASE/deploy/backup.sh" -o "$TMP_DIR/backup.sh" || { err "拉取 backup.sh 失败(--repo 是否可访问?)"; exit 1; }
  BACKUP_SCRIPT="$TMP_DIR/backup.sh"
fi

# ---------- 2. 先跑本地备份(可选) ----------
if [[ $DO_LOCAL_BACKUP -eq 1 ]]; then
  c "先生成最新本地备份 (backup.sh)..."
  env_args=()
  [[ -n "$ENV_FILE" ]] && env_args+=(--env-file "$ENV_FILE")
  bash "$BACKUP_SCRIPT" --output "$OUTPUT_DIR" --keep "$KEEP" "${env_args[@]}"
fi
[[ -d "$OUTPUT_DIR" ]] || { err "备份目录不存在: $OUTPUT_DIR"; exit 1; }

# ---------- 2. 校验有备份包 ----------
LATEST="$(ls -1t "$OUTPUT_DIR"/csctalon-backup-*.tar.gz 2>/dev/null | head -1 || true)"
if [[ -z "$LATEST" ]]; then
  err "未找到备份包: $OUTPUT_DIR/csctalon-backup-*.tar.gz (先跑 backup.sh)"; exit 1
fi
c "最新备份: $(basename "$LATEST")"

# ---------- 3. rsync 增量推送到云端 ----------
# SSH 参数: 有 --ssh-key 则携带; 兼容旧版 bash 空数组展开
SSH_ARGS=()
[[ -n "$SSH_KEY" ]] && SSH_ARGS=(-e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new")
c "rsync 推送到: $DEST"
if [[ ${#SSH_ARGS[@]} -gt 0 ]]; then
  rsync -avz --progress "${SSH_ARGS[@]}" "$OUTPUT_DIR/"/csctalon-backup-*.tar.gz "$DEST/"
else
  rsync -avz --progress "$OUTPUT_DIR/"/csctalon-backup-*.tar.gz "$DEST/"
fi

# ---------- 4. 可选: 清理云端多余旧备份 ----------
if [[ $PRUNE -eq 1 ]]; then
  c "清理云端旧备份(保留最近 ${KEEP} 份)..."
  DEST_HOST="${DEST%%:*}"
  DEST_PATH="${DEST#*:}"
  if [[ ${#SSH_ARGS[@]} -gt 0 ]]; then
    ssh "${SSH_ARGS[@]}" "$DEST_HOST" \
      "ls -1t ${DEST_PATH}/csctalon-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f"
  else
    ssh "$DEST_HOST" \
      "ls -1t ${DEST_PATH}/csctalon-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f"
  fi
fi

ok "异地备份完成: $(basename "$LATEST") → $DEST"
echo "  本地保留: $OUTPUT_DIR (最近 ${KEEP} 份)"
echo "  建议 cron: 30 3 * * * 每日执行本脚本(见脚本头部注释)"
