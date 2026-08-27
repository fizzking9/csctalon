#!/usr/bin/env bash
# =============================================================================
# install-frpc.sh — 本地服务器 frpc 接入脚本 (数据/计算/存储所在地)
#
# 作用: 在**本地服务器**(csctalon 所在机)上生成 frpc.toml 并启动 frpc,
#       把本地 nginx(web 容器, 127.0.0.1:80)通过 frp 隧道暴露到云端域名。
#       对现有 docker compose 服务零改动(只多一个 frpc 客户端)。
#
# 支持两种运行方式:
#   --mode binary : 下载 frp 二进制 + systemd 服务 (macOS/Linux 通用, 推荐本地)
#   --mode docker : 使用 docker-compose.frpc.yml (Linux, 无需下二进制)
#
# 用法(在本地服务器, root 或 sudo):
#   方式一: git raw 一条命令(gitea 默认, 无需克隆/上传, 配置模板自动从仓库拉取)
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/install-frpc.sh | \
#       sudo bash -s -- --server-host <云端IP> --server-port 7000 --token <与云端一致的token> --domain kf.example.com
#   方式二: 本地脚本(已克隆/上传仓库)
#     bash deploy/frp/local/install-frpc.sh \
#       --server-host <云端公网IP或域名> --server-port 7000 \
#       --token <与云端一致的token> --domain kf.example.com \
#       [--mode binary|docker] [--install-dir /opt/csctalon/frpc] [--frp-version 0.61.1]
#
# 可选:
#   --repo <raw_base>       配置模板来源 git raw base(默认 gitea main 分支)
#
# 生成物(本地):
#   <install-dir>/frpc.toml    frpc 配置(生成后可直接编辑, 重启生效)
#   <install-dir>/frpc         二进制(binary 模式)
#   /etc/systemd/system/frpc.service (binary 模式)
#   docker 容器 csctalon-frpc  (docker 模式)
#
# 验证: 浏览器打开 https://<域名>/ 应出现 csctalon 登录页。
# =============================================================================
set -euo pipefail

SERVER_HOST=""
SERVER_PORT=7000
TOKEN=""
DOMAIN=""
MODE="binary"
INSTALL_DIR="/opt/csctalon/frpc"
FRP_VERSION="0.61.1"
# 配置模板来源 git raw base(默认 gitea main 分支)
REPO_BASE="http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main"

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-host) SERVER_HOST="${2:?--server-host 需要云端地址}"; shift 2 ;;
    --server-port) SERVER_PORT="${2:?}"; shift 2 ;;
    --token)       TOKEN="${2:?--token 需要 frp token}"; shift 2 ;;
    --domain)      DOMAIN="${2:?--domain 需要域名}"; shift 2 ;;
    --mode)        MODE="${2:?--mode 需要 binary 或 docker}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:?}"; shift 2 ;;
    --frp-version) FRP_VERSION="${2:?}"; shift 2 ;;
    --repo)        REPO_BASE="${2:?--repo 需要 raw base url}"; shift 2 ;;
    -h|--help)
      cat >&2 <<'HELP'
用法: install-frpc.sh  --server-host <云端IP或域名> --server-port 7000 --token <frp token> --domain <域名> [--mode binary|docker]

本地 frpc 接入(把本地 nginx:80 通过 frp 隧道暴露到云端域名)。

git raw 一条命令:
  curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/install-frpc.sh | \
    sudo bash -s -- --server-host <云端IP> --server-port 7000 --token <与云端一致的token> --domain kf.example.com

参数:
  --server-host <地址>   必填, 云端 frps 地址(公网IP或域名)
  --server-port <端口>   默认 7000
  --token <token>        必填, frp 认证 token(与云端 frps 完全一致)
  --domain <域名>        必填, 对外访问域名(与云端 nginx server_name 一致)
  --mode <binary|docker> 默认 binary(systemd); docker 需 Linux
  --install-dir <路径>   默认 /opt/csctalon/frpc
  --frp-version <v>      默认 0.61.1
  --repo <raw_base>      配置模板来源 git raw base(默认 gitea main 分支)
HELP
      exit 0 ;;
    *) echo "✘ 未知参数: $1"; exit 1 ;;
  esac
done

[[ -z "$SERVER_HOST" ]] && { echo "✘ 缺少 --server-host"; exit 1; }
[[ -z "$TOKEN" ]]       && { echo "✘ 缺少 --token"; exit 1; }
[[ -z "$DOMAIN" ]]      && { echo "✘ 缺少 --domain"; exit 1; }
[[ "$MODE" != "binary" && "$MODE" != "docker" ]] && { echo "✘ --mode 只能是 binary 或 docker"; exit 1; }

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

# 脚本所在目录: 正常执行用 BASH_SOURCE; curl 管道时 BASH_SOURCE 为空 → 用 $0 兜底
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
fi
TEMPLATE_DIR="$SCRIPT_DIR"
TMP_TEMPLATE=""
trap '[[ -n "$TMP_TEMPLATE" ]] && rm -rf "$TMP_TEMPLATE"' EXIT
# 本地目录没有模板(如 curl 管道执行) → 从 git raw 拉取到临时目录
if [[ ! -f "$TEMPLATE_DIR/frpc.toml" ]]; then
  TMP_TEMPLATE="$(mktemp -d)"
  c "本地未找到模板, 从仓库拉取: $REPO_BASE"
  curl -fsSL "$REPO_BASE/deploy/frp/local/frpc.toml" -o "$TMP_TEMPLATE/frpc.toml" || { err "拉取 frpc.toml 失败(--repo 是否可访问?)"; exit 1; }
  TEMPLATE_DIR="$TMP_TEMPLATE"
fi

mkdir -p "$INSTALL_DIR"

# ---------- 1. 生成 frpc.toml ----------
c "生成 $INSTALL_DIR/frpc.toml ..."
sed -e "s/REPLACE_FRP_SERVER_HOST/${SERVER_HOST}/g" \
    -e "s/^serverPort = .*/serverPort = ${SERVER_PORT}/" \
    -e "s/REPLACE_FRP_TOKEN/${TOKEN}/g" \
    -e "s/REPLACE_FRP_DOMAIN/${DOMAIN}/g" \
    "$TEMPLATE_DIR/frpc.toml" > "$INSTALL_DIR/frpc.toml"
ok "frpc.toml 已生成"

# ---------- 2. 按模式启动 ----------
if [[ "$MODE" == "binary" ]]; then
  c "binary 模式: 下载 frp v${FRP_VERSION} 二进制..."
  if ! command -v unzip >/dev/null 2>&1; then
    c "安装 unzip..."
    (command -v apt-get >/dev/null && apt-get install -y unzip) || true
  fi
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) FRP_ARCH="amd64" ;;
    aarch64|arm64) FRP_ARCH="arm64" ;;
    *) err "不支持的架构: $ARCH (可用 --mode docker 避开二进制下载)"; exit 1 ;;
  esac
  TMPZ="$(mktemp -d)"
  URL="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${FRP_ARCH}.tar.gz"
  c "下载: $URL"
  curl -fSL "$URL" -o "$TMPZ/frp.tar.gz" || { err "下载失败(需能访问 github)。可改用 --mode docker"; exit 1; }
  tar xzf "$TMPZ/frp.tar.gz" -C "$TMPZ"
  cp "$TMPZ"/frp_*/frpc "$INSTALL_DIR/frpc"
  chmod +x "$INSTALL_DIR/frpc"
  rm -rf "$TMPZ"
  "$INSTALL_DIR/frpc" --version

  c "安装 systemd 服务 frpc.service ..."
  cat > /etc/systemd/system/frpc.service <<EOF
[Unit]
Description=frp client (csctalon)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/frpc -c $INSTALL_DIR/frpc.toml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now frpc
  sleep 2
  systemctl --no-pager -l status frpc --no-pager 2>/dev/null | head -8 || true
  ok "frpc systemd 服务已启动"

else
  c "docker 模式: 使用 docker-compose.frpc.yml ..."
  if ! command -v docker >/dev/null 2>&1; then
    err "未检测到 docker。请先安装 docker, 或改用 --mode binary"; exit 1
  fi
  # 把生成的 frpc.toml 与 compose 落到持久目录 $INSTALL_DIR(临时模板目录会被清理)
  # frpc.toml 已生成在 $INSTALL_DIR, 只需落 compose
  if [[ -f "$TEMPLATE_DIR/docker-compose.frpc.yml" ]]; then
    cp "$TEMPLATE_DIR/docker-compose.frpc.yml" "$INSTALL_DIR/docker-compose.frpc.yml"
  else
    curl -fsSL "$REPO_BASE/deploy/frp/local/docker-compose.frpc.yml" -o "$INSTALL_DIR/docker-compose.frpc.yml" || \
      { err "拉取 docker-compose.frpc.yml 失败"; exit 1; }
  fi
  cd "$INSTALL_DIR"
  docker compose -f docker-compose.frpc.yml up -d
  docker ps --filter name=csctalon-frpc --format '{{.Status}}'
  ok "frpc docker 容器已启动"
fi

# ---------- 3. 完成提示 ----------
echo ""
ok "frpc 接入完成!"
echo "  隧道 : ${SERVER_HOST}:${SERVER_PORT} → 本地 127.0.0.1:80"
echo "  域名 : https://${DOMAIN}"
echo "  验证 : 浏览器打开 https://${DOMAIN}/ 应看到 csctalon 登录页"
echo "  日志 : binary=journalctl -u frpc -f | docker=docker logs -f csctalon-frpc"
echo "  注意 : 本地断电/断网时外网访问会中断; 建议配 UPS + 断网自恢复(服务已 Restart=always)"
