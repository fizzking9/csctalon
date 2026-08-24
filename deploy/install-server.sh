#!/usr/bin/env bash
# =============================================================================
# install-server.sh — 阿里云服务器首次部署脚本
# 在**服务器上**以 root 执行。完成: 装 Docker → 建目录 → 解包代码 → 准备 .env
# → 构建启动 → (可选) 恢复数据库备份, 实现"数据导入即可用, 无需重新同步"。
#
# 用法:
#   sudo ./deploy/install-server.sh                                # 自动找 packages 里最新发布包
#   sudo ./deploy/install-server.sh --pkg /opt/csctalon/packages/csctalon-xxx.tar.gz
#   sudo ./deploy/install-server.sh --env-file /root/csctalon.env  # 使用现成 .env(推荐: 从本地 .env 改)
#   sudo ./deploy/install-server.sh --restore /root/csctalon-backup-xxx.tar.gz  # 同时恢复数据
#   sudo ./deploy/install-server.sh --skip-install-docker          # Docker 已装好时跳过安装
#
# 目录约定(数据固定, 代码随版本切换):
#   /opt/csctalon/releases/<版本>/   每版代码(解压发布包)
#   /opt/csctalon/current            → 指向当前版本软链
#   /opt/csctalon/packages           上传的发布包
#   /opt/csctalon/backups            备份文件(backup.sh 产物)
#   /opt/csctalon/data               数据库/Redis 数据(挂载)
#   /opt/csctalon/logs               nginx/backend 日志
#   /opt/csctalon/uploads            后端附件
#   /opt/csctalon/browser_states     Playwright 登录态
#   /opt/csctalon/.env               生产环境变量
# =============================================================================
set -euo pipefail

APP_DIR="/opt/csctalon"
PKG=""
ENV_FILE=""
RESTORE_FILE=""
SKIP_DOCKER=0

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pkg) PKG="${2:?--pkg 需要发布包路径}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file 需要 .env 路径}"; shift 2 ;;
    --restore) RESTORE_FILE="${2:?--restore 需要备份包路径}"; shift 2 ;;
    --skip-install-docker) SKIP_DOCKER=1; shift ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "✘ 未知参数: $1"; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "✘ 请用 root 或 sudo 执行" >&2; exit 1
fi

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

# ---------- 1. 安装 Docker ----------
if [[ $SKIP_DOCKER -eq 0 ]] && ! command -v docker >/dev/null 2>&1; then
  c "未检测到 Docker, 开始安装 (Ubuntu/Debian)..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
  ok "Docker 安装完成: $(docker --version)"
fi
if [[ $SKIP_DOCKER -eq 0 ]] && ! docker compose version >/dev/null 2>&1; then
  c "安装 Docker Compose 插件..."
  apt-get update -y && apt-get install -y docker-compose-plugin
fi
docker compose version >/dev/null 2>&1 || { err "Docker Compose 不可用, 请手动安装"; exit 1; }
ok "Docker 环境就绪"

# ---------- 2. 定位发布包 ----------
if [[ -z "$PKG" ]]; then
  PKG="$(ls -t "$APP_DIR/packages/"*.tar.gz 2>/dev/null | head -1 || true)"
fi
if [[ -z "$PKG" || ! -f "$PKG" ]]; then
  err "未找到发布包。请先用 upload.sh 上传, 或用 --pkg 指定路径"
  exit 1
fi
PKG="$(realpath "$PKG")"

# ---------- 3. 建目录 ----------
mkdir -p "$APP_DIR"/{releases,packages,backups,data,logs,uploads,browser_states}
mkdir -p "$APP_DIR/data/postgres" "$APP_DIR/data/redis"
mkdir -p "$APP_DIR/logs/nginx" "$APP_DIR/logs/backend"

# ---------- 4. 解压代码到版本目录 ----------
VER="$(tar xzf "$PKG" -O VERSION.tmp 2>/dev/null || basename "$PKG" .tar.gz | sed 's/^csctalon-//')"
RELEASE_DIR="$APP_DIR/releases/$VER"
if [[ -e "$RELEASE_DIR" ]]; then
  # 同版本重复发布 → 追加时间戳
  RELEASE_DIR="$APP_DIR/releases/${VER}-$(date +%Y%m%d%H%M%S)"
fi
c "解压发布包 → $RELEASE_DIR"
mkdir -p "$RELEASE_DIR"
tar xzf "$PKG" -C "$RELEASE_DIR"

# ---------- 5. 准备 .env ----------
ENV_TARGET="$APP_DIR/.env"
if [[ -n "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$ENV_TARGET"
  ok "已使用 --env-file 提供的 .env"
else
  if [[ -f "$ENV_TARGET" ]]; then
    ok "已存在 $APP_DIR/.env, 沿用"
  else
    cp "$RELEASE_DIR/.env.example" "$ENV_TARGET"
    err "$APP_DIR/.env 已从模板生成, 必须先编辑后再继续"
    echo ""
    echo "  请编辑 $APP_DIR/.env, 重点:"
    echo "  ① 密钥/密码(见 deploy/README.md §3.1, 迁移时 JWT_SECRET_KEY/CONFIG_ENCRYPTION_KEY 须沿用本地值)"
    echo "  ② 数据目录(建议):"
    echo "      DB_DATA_DIR=$APP_DIR/data/postgres"
    echo "      REDIS_DATA_DIR=$APP_DIR/data/redis"
    echo "      UPLOADS_DIR=$APP_DIR/uploads"
    echo "      BROWSER_STATES_DIR=$APP_DIR/browser_states"
    echo "      BACKEND_LOGS_DIR=$APP_DIR/logs/backend"
    echo "      NGINX_LOGS_DIR=$APP_DIR/logs/nginx"
    echo "  编辑完成后重新执行本脚本(--pkg $PKG)"
    exit 1
  fi
fi

# ---------- 6. 软链 current + 构建启动 ----------
ln -sfn "$RELEASE_DIR" "$APP_DIR/current"
c "启动服务 (docker compose up -d --build, 首次构建较慢)..."
cd "$APP_DIR/current"
# compose 自动加载同目录 .env; 生产 .env 在 $APP_DIR/.env, 用 env-file 方式指过去
docker compose --env-file "$APP_DIR/.env" up -d --build

# ---------- 7. 健康检查 ----------
c "等待服务健康(最多 120s)..."
HEALTHY=0
for _ in $(seq 1 40); do
  if docker compose --env-file "$APP_DIR/.env" ps --status running 2>/dev/null | grep -q backend \
     && curl -sf -o /dev/null "http://127.0.0.1:8000/health"; then
    HEALTHY=1; break
  fi
  sleep 3
done
if [[ $HEALTHY -eq 0 ]]; then
  err "服务未健康。查看日志: docker compose -f $APP_DIR/current/docker-compose.yml logs"
  exit 1
fi
ok "服务已启动"

# ---------- 8. 可选: 恢复数据备份(数据库+附件) ----------
if [[ -n "$RESTORE_FILE" ]]; then
  c "恢复数据备份: $RESTORE_FILE"
  bash "$RELEASE_DIR/deploy/restore.sh" --pkg-dir "$RELEASE_DIR" --env-file "$APP_DIR/.env" "$RESTORE_FILE"
fi

# ---------- 9. 完成提示 ----------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
ok "部署完成!"
echo "  访问地址 : http://${IP:-<服务器IP>}/"
echo "  代码目录 : $APP_DIR/current  ($VER)"
echo "  数据目录 : $APP_DIR/data"
echo "  下一步   : 每日自动备份可配置 cron, 见 deploy/README.md §5"
