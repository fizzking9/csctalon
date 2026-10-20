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
warn() { echo -e "\033[33m⚠ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

# current 自引用/循环自检(2026-09-04 事故: current→current 软链循环导致部署/回滚 cd 失败)。
# 若 current 指向自身 或 目标不存在 → 自动修正为 releases 最新真实版本目录并提示。
_cur_target="$(readlink "$CURRENT" 2>/dev/null || true)"
if [[ -z "$_cur_target" || "$_cur_target" == "$CURRENT" || "$_cur_target" == "$APP_DIR/current.old" || ! -d "$_cur_target" ]]; then
  warn "检测到 $CURRENT 软链异常(目标=${_cur_target:-空}/自引用/不存在), 自动修正为 releases 最新"
  _fix_target="$(ls -1dt "$APP_DIR"/releases/* 2>/dev/null | head -1 || true)"
  if [[ -z "$_fix_target" || ! -d "$_fix_target" ]]; then
    err "releases 为空, 无法自动修正; 请先手工恢复 /opt/csctalon/current"
    exit 1
  fi
  ln -sfn "$_fix_target" "$CURRENT"
  rm -f "$APP_DIR/current.old"   # 清掉可能的循环锚点(本次会重建)
  ok "已修正: current -> $_fix_target"
fi

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
# ⚠️ 发布纯净性: git 模式会原样拉取整个仓库, 必须清理开发/AI 工具产物与文档,
#    只保留运行期需要的 backend/web/deploy/docker-compose.yml/.env* 等,
#    否则杂质(.trae/.dsh/.gitea/.workbuddy/docs/scripts 等)随发布进入 current,
#    每次 release 多出 ~8.8MB 冗余, 也易把探针/调试脚本带入生产(2026-08-30 部署审计)。
mkdir -p "$RELEASE_DIR"
if [[ $GIT_MODE -eq 1 ]]; then
  c "从 gitea 拉取代码: $REPO ($BRANCH) → $RELEASE_DIR"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$RELEASE_DIR" || {
    err "git clone 失败(确认服务器能访问 $REPO), 已中止, 未做任何改动"
    rm -rf "$RELEASE_DIR"
    exit 1
  }
  # 清理 git 模式带入的开发杂质(与 package.sh 的排除清单保持同一口径)
  c "git 模式清理发布杂质(与 package.sh 排除清单一致)..."
  rm -rf \
    "$RELEASE_DIR/.git" \
    "$RELEASE_DIR/.gitignore" \
    "$RELEASE_DIR/.gitea" \
    "$RELEASE_DIR/.dsh" \
    "$RELEASE_DIR/.trae" \
    "$RELEASE_DIR/.workbuddy" \
    "$RELEASE_DIR/docs" \
    "$RELEASE_DIR/scripts" \
    "$RELEASE_DIR/backups" \
    "$RELEASE_DIR/backend/tests" \
    "$RELEASE_DIR/backend/uploads" \
    "$RELEASE_DIR/VERSION.tmp"
  # backend/scripts/ 清空所有调试/归档脚本(生产镜像不再从 scripts/ 取内容)
  # 登录脚本已于 2026-10-20 迁入 app/services/shared_doc_*_cli.py, 不再从 scripts/ 走
  rm -rf "$RELEASE_DIR/backend/scripts" 2>/dev/null || true
else
  c "解压发布包 → $RELEASE_DIR"
  tar xzf "$PKG" -C "$RELEASE_DIR"
fi

# ---------- 3. 切换软链并重建 ----------
# 错误/废弃的上传路径变量守卫(2026-08-31 事故复发防护, 与 install-server.sh 一致):
# 服务器上 UPLOAD_DIR=./uploads 会挂到容器内空目录 /app/uploads, 上传/附件静默不可见。
# 复数 UPLOADS_DIR/BROWSER_STATES_DIR 已废弃, 出现即提示; 仅 warn 不自动改, 防止覆盖用户配置。
if grep -qE "^(UPLOADS_DIR|BROWSER_STATES_DIR)=" "$ENV_FILE" \
   || grep -qE "^UPLOAD_DIR=(\./|/app)" "$ENV_FILE" \
   || grep -qE "^BROWSER_STATE_DIR=(\./|/app)" "$ENV_FILE"; then
  warn "$ENV_FILE 存在已废弃/错误的上传路径变量"
  warn "  - 复数 UPLOADS_DIR/BROWSER_STATES_DIR 已废弃(2026-08-30 统一为单数), 删除或用单数"
  warn "  - UPLOAD_DIR/BROWSER_STATE_DIR 不能是相对路径(./)或 /app 开头(容器内空目录)"
  warn "  请改为绝对路径: UPLOAD_DIR=$APP_DIR/uploads, BROWSER_STATE_DIR=$APP_DIR/browser_states"
  warn "  否则 backend 会把 /app/uploads 挂到空目录, 上传/附件不可见"
fi
# .env 布尔变量合法性预检(2026-09-04 事故 H-3): EXTERNAL_API_ENABLED=tru 等非法值
# 会让 backend pydantic fail-fast 崩溃(unhealthy)再触发回滚; 这里在 compose up 前提前拦截。
# 规则: 变量名以 ENABLED/ALLOWED/AUTOSTART 结尾或等于 DEBUG 的, 值必须 true/false(忽略空/注释)。
_BOOL_KEYS=$(grep -oE "^(DEBUG|[A-Z_]+_(ENABLED|ALLOWED|AUTOSTART))=" "$ENV_FILE" | sed 's/=$//' | sort -u || true)
_BAD_BOOL=0
while IFS= read -r _bk; do
  [[ -z "$_bk" ]] && continue
  _bv="$(grep -E "^${_bk}=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
  if [[ -n "$_bv" && "$_bv" != "true" && "$_bv" != "false" ]]; then
    err "✘ .env 布尔变量非法: $_bk=$_bv (应为 true 或 false) → backend 会启动失败!"
    err "  请修正 $ENV_FILE 后重试(如: sudo sed -i 's/^$_bk=.*/$_bk=false/' $ENV_FILE)"
    _BAD_BOOL=1
  fi
done <<< "$_BOOL_KEYS"
if [[ $_BAD_BOOL -ne 0 ]]; then
  exit 1
fi
# 回滚辅助: 切回 current.old 指向的真实版本; 防 current/current.old 自引用循环(2026-09-04 事故)
rollback_current() {
  local target
  target="$(readlink "$APP_DIR/current.old" 2>/dev/null || true)"
  # 防自引用/循环: 目标等于 current 自身/current.old 或为空 → 退回 releases 最新目录
  if [[ -z "$target" || "$target" == "$CURRENT" || "$target" == "$APP_DIR/current.old" ]]; then
    warn "current.old 无效或自引用(${target:-空}), 回滚目标改为 releases 最新"
    target="$(ls -1dt "$APP_DIR"/releases/* 2>/dev/null | head -1 || true)"
  fi
  if [[ -z "$target" || ! -d "$target" ]]; then
    err "找不到可回滚的版本目录(releases 为空?), 请手工执行:"
    err "  sudo ln -sfn /opt/csctalon/releases/<版本> /opt/csctalon/current"
    exit 1
  fi
  ln -sfn "$target" "$CURRENT"
  ln -sf "$ENV_FILE" "$CURRENT/.env" 2>/dev/null || true
  cd "$CURRENT"
  docker compose --env-file "$ENV_FILE" up -d --build || true
}
ln -sfn "$CURRENT" "$APP_DIR/current.old"   # 回滚锚点
ln -sfn "$RELEASE_DIR" "$CURRENT"
ln -sf "$ENV_FILE" "$CURRENT/.env" 2>/dev/null || true   # 兜底: compose 相对目录找 .env
c "重建并启动服务 (后端启动自动执行 alembic 迁移)..."
cd "$CURRENT"
if ! docker compose --env-file "$ENV_FILE" up -d --build; then
  err "启动失败, 自动回滚..."
  rollback_current
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
  rollback_current
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
