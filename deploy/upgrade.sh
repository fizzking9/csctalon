#!/usr/bin/env bash
# =============================================================================
# upgrade.sh — 服务器版本升级脚本
# 在**服务器上**以 root 执行。流程: 升级前自动备份 → 解压新包 → 切换 current
# → 重建重启(后端启动自动执行 alembic upgrade head, 无需手动迁移) → 健康检查,
# 失败自动回滚到上一个版本。
#
# 用法:
#   sudo ./deploy/upgrade.sh /opt/csctalon/packages/csctalon-v1.1.0-xxxx.tar.gz
#   sudo ./deploy/upgrade.sh /opt/csctalon/packages/latest.tar.gz
#   sudo ./deploy/upgrade.sh <包> --no-backup      # 跳过升级前自动备份(不推荐)
#   sudo ./deploy/upgrade.sh --git                 # 直接从 gitea 拉取最新 main 升级
#   sudo ./deploy/upgrade.sh --git --branch dev    # 指定分支
#
# 备份产物: /opt/csctalon/backups/  (供 rollback.sh 恢复)
# =============================================================================
set -euo pipefail

APP_DIR="/opt/csctalon"
CURRENT="$APP_DIR/current"
ENV_FILE="$APP_DIR/.env"
DO_BACKUP=1
GIT_MODE=0
REPO="http://10.147.17.41:3000/tanzk/csctalon.git"
BRANCH="main"

PKG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-backup) DO_BACKUP=0; shift ;;
    --git) GIT_MODE=1; shift ;;
    --repo) REPO="${2:?--repo 需要仓库地址}"; shift 2 ;;
    --branch) BRANCH="${2:?--branch 需要分支名}"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) PKG="$1"; shift ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "✘ 请用 root 或 sudo 执行" >&2; exit 1; fi
if [[ $GIT_MODE -eq 0 && ( -z "$PKG" || ! -f "$PKG" ) ]]; then
  echo "✘ 用法: sudo ./deploy/upgrade.sh <发布包路径>  或  sudo ./deploy/upgrade.sh --git" >&2; exit 1
fi
if [[ ! -L "$CURRENT" ]]; then
  echo "✘ 未检测到 $CURRENT 软链, 请确认已执行过 install-server.sh" >&2; exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "✘ 未找到 $ENV_FILE" >&2; exit 1
fi

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

if [[ $GIT_MODE -eq 1 ]]; then
  PKG=""
  NEW_VER="$(git ls-remote --tags "$REPO" "refs/tags/$BRANCH" 2>/dev/null | head -1 | cut -f1 | cut -c1-12 || true)"
  [[ -z "$NEW_VER" ]] && NEW_VER="git-$(date +%Y%m%d%H%M%S)"
else
  PKG="$(realpath "$PKG")"
  NEW_VER="$(tar xzf "$PKG" -O VERSION.tmp 2>/dev/null || basename "$PKG" .tar.gz | sed 's/^csctalon-//')"
fi
OLD_VER="$(cat "$CURRENT/VERSION.tmp" 2>/dev/null || basename "$(readlink "$CURRENT")")"
RELEASE_DIR="$APP_DIR/releases/$NEW_VER"
if [[ -e "$RELEASE_DIR" ]]; then
  RELEASE_DIR="$APP_DIR/releases/${NEW_VER}-$(date +%Y%m%d%H%M%S)"
fi

c "当前版本: $OLD_VER"
c "升级版本: $NEW_VER"

# ---------- 1. 升级前自动备份 ----------
if [[ $DO_BACKUP -eq 1 ]]; then
  c "升级前自动备份(数据库+附件)..."
  bash "$CURRENT/deploy/backup.sh" --env-file "$ENV_FILE" --output "$APP_DIR/backups" --keep 7
fi

# ---------- 2. 获取新代码(tar 解包 或 git clone) ----------
mkdir -p "$RELEASE_DIR"
if [[ $GIT_MODE -eq 1 ]]; then
  c "从 gitea 拉取代码: $REPO ($BRANCH) → $RELEASE_DIR"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$RELEASE_DIR" || {
    err "git clone 失败(确认服务器能访问 $REPO), 已中止, 未做任何改动"
    rm -rf "$RELEASE_DIR"
    exit 1
  }
else
  c "解压发布包 → $RELEASE_DIR"
  tar xzf "$PKG" -C "$RELEASE_DIR"
fi

# ---------- 3. 切换软链并重建 ----------
ln -sfn "$CURRENT" "$APP_DIR/current.old"   # 回滚锚点
ln -sfn "$RELEASE_DIR" "$CURRENT"
ln -sf "$ENV_FILE" "$CURRENT/.env" 2>/dev/null || true   # 兜底: compose 相对目录找 .env
c "重建并启动服务 (后端启动自动执行 alembic 迁移)..."
cd "$CURRENT"
if ! docker compose --env-file "$ENV_FILE" up -d --build; then
  err "启动失败, 自动回滚..."
  ln -sfn "$(readlink "$APP_DIR/current.old")" "$CURRENT"
  ln -sf "$ENV_FILE" "$CURRENT/.env" 2>/dev/null || true
  cd "$CURRENT"
  docker compose --env-file "$ENV_FILE" up -d --build || true
  exit 1
fi

# ---------- 4. 健康检查 ----------
c "等待服务健康(最多 120s)..."
HEALTHY=0
for _ in $(seq 1 40); do
  if docker compose --env-file "$ENV_FILE" ps --status running 2>/dev/null | grep -q backend \
     && curl -sf -o /dev/null "http://127.0.0.1:8000/health"; then
    HEALTHY=1; break
  fi
  sleep 3
done

if [[ $HEALTHY -eq 0 ]]; then
  err "升级后服务未健康, 自动回滚到 $OLD_VER ..."
  ln -sfn "$(readlink "$APP_DIR/current.old")" "$CURRENT"
  cd "$CURRENT"
  docker compose --env-file "$ENV_FILE" up -d --build || true
  ok "已回滚到 $OLD_VER(数据为升级前备份状态, 如需完全一致请用 rollback.sh 恢复备份)"
  exit 1
fi

ok "升级完成: $OLD_VER → $NEW_VER"
echo "  数据目录不变: $APP_DIR/data"

# ---------- 5. 清理旧版本(保留最近 5 个) ----------
cd "$APP_DIR/releases" || true
KEEP=5
ls -1dt "$APP_DIR"/releases/* 2>/dev/null | tail -n +$((KEEP + 2)) | while read -r old; do
  if [[ "$old" != "$(readlink "$APP_DIR/current")" && "$old" != "$(readlink "$APP_DIR/current.old" 2>/dev/null)" ]]; then
    c "清理旧版本: $old"
    rm -rf "$old"
  fi
done
