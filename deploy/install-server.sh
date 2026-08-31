#!/usr/bin/env bash
# =============================================================================
# install-server.sh — 阿里云服务器首次部署脚本
# 在**服务器上**以 root 执行。完成: 装 Docker → 建目录 → 解包代码 → 准备 .env
# → 构建启动 → (可选) 恢复数据库备份, 实现"数据导入即可用, 无需重新同步"。
#
# 用法:
#   sudo ./deploy/install-server.sh                                # 自动找 packages 里最新发布包
#   sudo ./deploy/install-server.sh --pkg /opt/csctalon/packages/csctalon-xxx.tar.gz
#   sudo ./deploy/install-server.sh --src-dir /tmp/csctalon-src    # 代码来自已有目录(如 git clone 产物)
#   sudo ./deploy/install-server.sh --env-file /root/csctalon.env  # 使用现成 .env(推荐: 从本地 .env 改)
#   sudo ./deploy/install-server.sh --restore /root/csctalon-backup-xxx.tar.gz  # 同时恢复数据
#   sudo ./deploy/install-server.sh --skip-install-docker          # Docker 已装好时跳过安装
#   git 一键安装(服务器能访问 gitea 时):
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/bootstrap.sh | sudo bash
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
SRC_DIR=""
ENV_FILE=""
RESTORE_FILE=""
SKIP_DOCKER=0

# 生产数据/日志目录(install 脚本会创建; 与 deploy/env.production.example 保持一致)
# 用于"已有 .env 缺关键变量时自动补齐", 防止旧模板(根目录 .env.example 无这些变量)
# 导致数据落到代码目录内、升级切版本时数据"丢失"。
# ⚠️ 变量名与 docker-compose.yml 挂载变量、deploy/env.production.example 保持同名(单数):
#    UPLOAD_DIR/BROWSER_STATE_DIR(与后端代码 settings 一致), 不用复数 UPLOADS_DIR/BROWSER_STATES_DIR(2026-08-30 已统一)。
PROD_DIR_VARS=(
  "DB_DATA_DIR=$APP_DIR/data/postgres"
  "REDIS_DATA_DIR=$APP_DIR/data/redis"
  "UPLOAD_DIR=$APP_DIR/uploads"
  "BROWSER_STATE_DIR=$APP_DIR/browser_states"
  "BACKEND_LOGS_DIR=$APP_DIR/logs/backend"
  "NGINX_LOGS_DIR=$APP_DIR/logs/nginx"
  "ENV_FILE=$APP_DIR/.env"
)

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pkg) PKG="${2:?--pkg 需要发布包路径}"; shift 2 ;;
    --src-dir) SRC_DIR="${2:?--src-dir 需要代码目录路径}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file 需要 .env 路径}"; shift 2 ;;
    --restore) RESTORE_FILE="${2:?--restore 需要备份包路径}"; shift 2 ;;
    --skip-install-docker) SKIP_DOCKER=1; shift ;;
    -h|--help)
      sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "✘ 未知参数: $1"; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "✘ 请用 root 或 sudo 执行" >&2; exit 1
fi

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
warn() { echo -e "\033[33m⚠ $*\033[0m"; }
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

# ---------- 2. 定位代码来源(发布包 或 git clone 目录) ----------
if [[ -n "$SRC_DIR" ]]; then
  SRC_DIR="$(realpath "$SRC_DIR")"
  [[ -f "$SRC_DIR/docker-compose.yml" ]] || { err "--src-dir 不是有效代码目录(缺少 docker-compose.yml): $SRC_DIR"; exit 1; }
else
  if [[ -z "$PKG" ]]; then
    PKG="$(ls -t "$APP_DIR/packages/"*.tar.gz 2>/dev/null | head -1 || true)"
  fi
  if [[ -z "$PKG" || ! -f "$PKG" ]]; then
    err "未找到发布包。请先用 upload.sh 上传, 或用 --pkg/--src-dir 指定来源"
    exit 1
  fi
  PKG="$(realpath "$PKG")"
fi

# ---------- 3. 建目录 ----------
mkdir -p "$APP_DIR"/{releases,packages,backups,data,logs,uploads,browser_states}
mkdir -p "$APP_DIR/data/postgres" "$APP_DIR/data/redis"
mkdir -p "$APP_DIR/logs/nginx" "$APP_DIR/logs/backend"
# PG 数据目录属主固定为 postgres(uid 999): 容器内 db 以 999 运行(见 docker-compose.yml user: "999:999"),
# bind mount 目录若属主不对会导致 PG 无法写数据文件。显式 chown 后宿主侧可清晰看到 999 属主
# (而非 root 都难遍历的错位 uid), 便于 backup.sh/巡检直接读取(2026-08-30 部署审计)。
chown -R 999:999 "$APP_DIR/data/postgres" 2>/dev/null || true

# ---------- 4. 落盘代码到版本目录 ----------
if [[ -n "$SRC_DIR" ]]; then
  VER="$(cd "$SRC_DIR" && git describe --tags --always 2>/dev/null || basename "$SRC_DIR")"
  RELEASE_DIR="$APP_DIR/releases/$VER"
  if [[ -e "$RELEASE_DIR" ]]; then
    RELEASE_DIR="$APP_DIR/releases/${VER}-$(date +%Y%m%d%H%M%S)"
  fi
  c "复制代码 → $RELEASE_DIR (来源: $SRC_DIR)"
  mkdir -p "$RELEASE_DIR"
  # 排除 git 来源的开发杂质(.git/node_modules/__pycache__ 之外, 与 upgrade.sh --git / package.sh
  # 同一口径): .trae/.dsh/.gitea/.workbuddy/docs/scripts 等不进 current(2026-08-30 部署审计)。
  # 注意不能用 --exclude='data', 会误伤 web/src/views/data/ 源码目录(曾致前端构建失败)
  tar czf - -C "$SRC_DIR" \
    --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
    --exclude='.trae' --exclude='.dsh' --exclude='.gitea' --exclude='.workbuddy' \
    --exclude='docs' --exclude='scripts' --exclude='backend/scripts' --exclude='backend/tests' \
    . 2>/dev/null | tar xzf - -C "$RELEASE_DIR"
else
  VER="$(tar xzf "$PKG" -O VERSION.tmp 2>/dev/null || basename "$PKG" .tar.gz | sed 's/^csctalon-//')"
  RELEASE_DIR="$APP_DIR/releases/$VER"
  if [[ -e "$RELEASE_DIR" ]]; then
    # 同版本重复发布 → 追加时间戳
    RELEASE_DIR="$APP_DIR/releases/${VER}-$(date +%Y%m%d%H%M%S)"
  fi
  c "解压发布包 → $RELEASE_DIR"
  mkdir -p "$RELEASE_DIR"
  tar xzf "$PKG" -C "$RELEASE_DIR"
fi

# ---------- 5. 准备 .env ----------
ENV_TARGET="$APP_DIR/.env"
if [[ -n "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$ENV_TARGET"
  ok "已使用 --env-file 提供的 .env"
else
  if [[ -f "$ENV_TARGET" ]]; then
    # 沿用已有 .env 时校验生产关键变量(DB_DATA_DIR/ENV_FILE)。
    # 旧模板(根目录 .env.example)无这些变量 → 自动补齐生产目录变量, 保留用户已有配置,
    # 避免旧 .env 导致数据落到代码目录内、升级切版本时数据"丢失"。
    if ! grep -qE "^(DB_DATA_DIR|ENV_FILE)=" "$ENV_TARGET"; then
      warn "$APP_DIR/.env 缺少生产关键变量(DB_DATA_DIR/ENV_FILE), 疑似旧模板 → 自动补齐生产目录变量"
      # 仅追加缺失的变量(逐行检查, 已存在则跳过), 不动用户已配置的密钥/密码等
      for _pv in "${PROD_DIR_VARS[@]}"; do
        _key="${_pv%%=*}"
        if ! grep -qE "^${_key}=" "$ENV_TARGET"; then
          echo "$_pv" >> "$ENV_TARGET"
          ok "  已补充 $_pv"
        fi
      done
      # 补齐后若密钥仍是占位符(旧模板 <请修改...>), 提示用户(否则后端 fail-fast 拒启)
      if grep -qE "POSTGRES_PASSWORD=.*(<|请修改|please|random)" "$ENV_TARGET" \
         || grep -qE "JWT_SECRET_KEY=.*(<|请修改|please|random|CHANGE_ME)" "$ENV_TARGET" \
         || grep -qE "INIT_ADMIN_PASSWORD=.*(<|请修改|admin123)" "$ENV_TARGET" \
         || grep -qE "REDIS_PASSWORD=(<|请修改|$)" "$ENV_TARGET"; then
        warn "检测到 .env 仍含占位符密钥(POSTGRES_PASSWORD/JWT_SECRET_KEY/INIT_ADMIN_PASSWORD/REDIS_PASSWORD)"
        warn "  生产后端启动会 fail-fast 拒绝。请先编辑 $APP_DIR/.env 改为强随机值(参考 deploy/env.production.example)"
      fi
    fi
    # 错误/废弃的上传路径变量守卫(2026-08-31 事故复发防护):
    # 只处理"缺失"(上面补齐), 不处理"错误值已存在"——UPLOAD_DIR=./uploads 这种相对路径
    # 在服务器上会挂到容器内空目录 /app/uploads, 上传/附件静默不可见。复数 UPLOADS_DIR/
    # BROWSER_STATES_DIR 已废弃, 出现即提示。生产脚本此处命中一律 warn(不自动改, 防止覆盖用户配置)。
    if grep -qE "^(UPLOADS_DIR|BROWSER_STATES_DIR)=" "$ENV_TARGET" \
       || grep -qE "^UPLOAD_DIR=(\./|/app)" "$ENV_TARGET" \
       || grep -qE "^BROWSER_STATE_DIR=(\./|/app)" "$ENV_TARGET"; then
      warn "$APP_DIR/.env 存在已废弃/错误的上传路径变量"
      warn "  - 复数 UPLOADS_DIR/BROWSER_STATES_DIR 已废弃(2026-08-30 统一为单数), 删除或用单数"
      warn "  - UPLOAD_DIR/BROWSER_STATE_DIR 不能是相对路径(./)或 /app 开头(容器内空目录)"
      warn "  请改为绝对路径:"
      warn "      UPLOAD_DIR=$APP_DIR/uploads"
      warn "      BROWSER_STATE_DIR=$APP_DIR/browser_states"
      warn "  否则 backend 会把 /app/uploads 挂到空目录, 上传/附件不可见"
    fi
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
    echo "      UPLOAD_DIR=$APP_DIR/uploads"
    echo "      BROWSER_STATE_DIR=$APP_DIR/browser_states"
    echo "      BACKEND_LOGS_DIR=$APP_DIR/logs/backend"
    echo "      NGINX_LOGS_DIR=$APP_DIR/logs/nginx"
    echo "  编辑完成后重新执行本脚本(--src-dir ${SRC_DIR:---pkg $PKG})"
    exit 1
  fi
fi

# ---------- 6. 软链 current + 构建启动 ----------
ln -sfn "$RELEASE_DIR" "$APP_DIR/current"
# 兜底: 让 compose 在代码目录内也能找到生产 .env(env_file: .env 相对 compose 目录解析)
ln -sf "$APP_DIR/.env" "$APP_DIR/current/.env" 2>/dev/null || true
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
