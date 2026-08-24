#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — 从 gitea 仓库一条命令完成服务器安装
# 前提: 服务器能访问 gitea(http://10.147.17.41:3000), 已装 git(未装会自动装)。
#
# 用法(服务器上, root 或 sudo):
#   curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/bootstrap.sh | sudo bash
#
# 可传环境变量:
#   REPO=...       # git 仓库地址(默认 http://10.147.17.41:3000/tanzk/csctalon.git)
#   BRANCH=...     # 分支(默认 main)
#   ENV_FILE=...   # 现成 .env 绝对路径(推荐: 本地 backend/.env 按部署手册改好后上传)
#   RESTORE_FILE=...# 数据备份包绝对路径(首次迁移用 backup.sh 的产物)
#   SKIP_DOCKER=1  # 已装 docker 时跳过安装
#
# 示例(首次部署含数据导入):
#   curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/bootstrap.sh | \
#     ENV_FILE=/root/csctalon.env RESTORE_FILE=/root/csctalon-backup-20260824.tar.gz sudo bash
# =============================================================================
set -euo pipefail

REPO="${REPO:-http://10.147.17.41:3000/tanzk/csctalon.git}"
BRANCH="${BRANCH:-main}"

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
