#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — 从 gitea 仓库一条命令完成服务器安装
# 前提: 服务器能访问 gitea(http://10.147.17.41:3000), 已装 git(未装会自动装)。
#
# 用法(服务器上, root 或 sudo):
#   普通安装:
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/bootstrap.sh | sudo bash
#   首次部署含数据导入(推荐, 位置参数经 bash -s 透传, 不受 sudo 清环境变量影响):
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/bootstrap.sh | \
#       sudo bash -s -- --env-file /root/csctalon-prod.env --restore /opt/csctalon/packages/csctalon-backup-*.tar.gz
#
# 支持的位置参数(透传给 install-server.sh):
#   --env-file <路径>   现成 .env 绝对路径(推荐: 本地 .env 按部署手册改好后上传)
#   --restore <备份包>  数据备份包绝对路径(首次迁移用 backup.sh 的产物)
#   --skip-install-docker 已装 docker 时跳过安装
#
# 兼容旧用法(环境变量方式, 注意 sudo 会清空环境, 建议改用上面 bash -s 方式):
#   REPO=... BRANCH=... ENV_FILE=... RESTORE_FILE=... SKIP_DOCKER=1
# =============================================================================
set -euo pipefail

REPO="${REPO:-http://10.147.17.41:3000/tanzk/csctalon.git}"
BRANCH="${BRANCH:-main}"

# ---------- 参数解析(支持 bash -s -- 透传 + 环境变量兼容) ----------
ENV_FILE="${ENV_FILE:-}"
RESTORE_FILE="${RESTORE_FILE:-}"
SKIP_DOCKER="${SKIP_DOCKER:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="${2:?--env-file 需要路径}"; shift 2 ;;
    --restore) RESTORE_FILE="${2:?--restore 需要备份包路径}"; shift 2 ;;
    --skip-install-docker) SKIP_DOCKER=1; shift ;;
    --repo) REPO="${2:?--repo 需要仓库地址}"; shift 2 ;;
    --branch) BRANCH="${2:?--branch 需要分支名}"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "✘ 未知参数: $1" >&2; exit 1 ;;
  esac
done

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

# ---------- 0. 前置: git / root ----------
if [[ $EUID -ne 0 ]]; then
  err "请用 root 或 sudo 执行 (curl ... | sudo bash)"
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  c "未检测到 git, 安装..."
  apt-get update -y && apt-get install -y git
fi

# ---------- 1. clone 代码 ----------
TMP_SRC="$(mktemp -d)/csctalon"
c "克隆仓库: $REPO (branch=$BRANCH)"
git clone --depth 1 -b "$BRANCH" "$REPO" "$TMP_SRC" || {
  err "git clone 失败。请确认服务器能访问 $REPO, 或改用 tar 包方式(deploy/README.md §2)"
  exit 1
}

# ---------- 2. 透传参数调用 install-server.sh ----------
ARGS=(--src-dir "$TMP_SRC")
[[ -n "${ENV_FILE:-}" ]]    && ARGS+=(--env-file "$ENV_FILE")
[[ -n "${RESTORE_FILE:-}" ]] && ARGS+=(--restore "$RESTORE_FILE")
[[ -n "${SKIP_DOCKER:-}" ]]  && ARGS+=(--skip-install-docker)

c "执行安装: install-server.sh ${ARGS[*]}"
bash "$TMP_SRC/deploy/install-server.sh" "${ARGS[@]}"
