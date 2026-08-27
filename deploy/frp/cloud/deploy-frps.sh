#!/usr/bin/env bash
# =============================================================================
# deploy-frps.sh — 云端转发机一键部署脚本 (frp server + nginx TLS)
#
# 作用: 在**云端轻量服务器**(1C2G 起步)上, 一键完成:
#   1. 安装 Docker (frps 以容器方式运行)
#   2. 安装 nginx (宿主 apt 包, 承担 443 TLS 终止, 性能/证书管理最简)
#   3. 生成 frps.toml / nginx 配置(从 deploy/frp/cloud/ 模板替换占位符)
#   4. 启动 frps(容器) + nginx + 校验
#
# 云端服务器职责边界(设计核心):
#   - 只做"端口中转 + TLS 终止", 不跑任何业务容器, 业务数据零落地云端
#   - 对外只开放: 443(HTTPS) / 7000(frps 隧道口); 其余端口仅绑 127.0.0.1
#
# 用法(在云端服务器, root 或 sudo):
#   方式一: git raw 一条命令(gitea 默认, 无需克隆/上传, 模板自动从仓库拉取)
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/cloud/deploy-frps.sh | \
#       sudo bash -s -- --domain kf.example.com --token <openssl rand -hex 32> --dash-pwd <frp面板密码>
#   方式二: 本地脚本(已克隆/上传仓库)
#     bash deploy/frp/cloud/deploy-frps.sh \
#       --domain kf.example.com --token <...> --dash-pwd <...> \
#       --cert /path/to/kf.example.com.crt --key /path/to/kf.example.com.key
#   (证书也可部署后再手动放到 /etc/nginx/certs/ 并 reload nginx)
#
# 可选:
#   --repo <raw_base>       模板来源 git raw base(默认 gitea main 分支)
#   --frp-version 0.61.1    指定 frps 镜像版本
#   --vhost-port 8080       覆盖 frps vhostHTTPPort(需与 nginx 反代目标一致)
#   --skip-install-docker   已装 docker 时跳过
#
# 生成物(云端):
#   /etc/frp/frps.toml                    frps 配置(生成后可直接编辑)
#   /etc/nginx/conf.d/csctalon-frp.conf   nginx 站点配置
#   /etc/nginx/certs/<域名>.crt/.key      证书(若未传 --cert 则提示手动放置)
#   frps 容器(restart=unless-stopped), nginx 已启用
#
# 完成后本地执行 deploy/frp/local/install-frpc.sh 接入隧道。
# =============================================================================
set -euo pipefail

FRP_VERSION="0.61.1"
VHOST_PORT=8080
DOMAIN=""
TOKEN=""
DASH_PWD=""
CERT_FILE=""
KEY_FILE=""
SKIP_DOCKER=0
# 模板来源 git raw base(默认 gitea main 分支)
REPO_BASE="http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main"

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)   DOMAIN="${2:?--domain 需要域名}"; shift 2 ;;
    --token)    TOKEN="${2:?--token 需要 frp token}"; shift 2 ;;
    --dash-pwd) DASH_PWD="${2:?--dash-pwd 需要面板密码}"; shift 2 ;;
    --cert)     CERT_FILE="${2:?--cert 需要证书路径}"; shift 2 ;;
    --key)      KEY_FILE="${2:?--key 需要私钥路径}"; shift 2 ;;
    --repo) REPO_BASE="${2:?--repo 需要 raw base url}"; shift 2 ;;
    --frp-version) FRP_VERSION="${2:?}"; shift 2 ;;
    --vhost-port) VHOST_PORT="${2:?}"; shift 2 ;;
    --skip-install-docker) SKIP_DOCKER=1; shift ;;
    -h|--help)
      cat >&2 <<'HELP'
用法: deploy-frps.sh  --domain <域名> --token <frp token> --dash-pwd <面板密码> [--cert 证书 --key 私钥]

云端 frp 服务端 + nginx TLS 一键部署(装 Docker/nginx → 生成配置 → 启动 frps)。

git raw 一条命令:
  curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/cloud/deploy-frps.sh | \
    sudo bash -s -- --domain kf.example.com --token <openssl rand -hex 32> --dash-pwd <面板密码>

参数:
  --domain <域名>          必填, 对外访问域名(与 DNS、本地 customDomains 一致)
  --token <token>          必填, frp 认证 token(与本地 frpc 完全一致)
  --dash-pwd <密码>        必填, frp 面板密码
  --cert <证书> --key <私钥> 可选, nginx 证书(.crt/.key); 不传则生成占位配置稍后手动放
  --repo <raw_base>        模板来源 git raw base(默认 gitea main 分支)
  --frp-version <v>        默认 0.61.1
  --vhost-port <端口>      默认 8080(frps vhostHTTPPort)
  --skip-install-docker    已装 docker 时跳过安装
HELP
      exit 0 ;;
    *) echo "✘ 未知参数: $1"; exit 1 ;;
  esac
done

[[ -z "$DOMAIN" ]] && { echo "✘ 缺少 --domain"; exit 1; }
[[ -z "$TOKEN" ]]  && { echo "✘ 缺少 --token (建议: openssl rand -hex 32)"; exit 1; }
[[ -z "$DASH_PWD" ]] && { echo "✘ 缺少 --dash-pwd (frp 面板密码)"; exit 1; }
if [[ $EUID -ne 0 ]]; then
  echo "✘ 请用 root 或 sudo 执行"; exit 1
fi

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
if [[ ! -f "$TEMPLATE_DIR/frps.toml" || ! -f "$TEMPLATE_DIR/nginx.conf" ]]; then
  TMP_TEMPLATE="$(mktemp -d)"
  c "本地未找到模板, 从仓库拉取: $REPO_BASE"
  curl -fsSL "$REPO_BASE/deploy/frp/cloud/frps.toml"  -o "$TMP_TEMPLATE/frps.toml"  || { err "拉取 frps.toml 失败(--repo 是否可访问?)"; exit 1; }
  curl -fsSL "$REPO_BASE/deploy/frp/cloud/nginx.conf" -o "$TMP_TEMPLATE/nginx.conf" || { err "拉取 nginx.conf 失败(--repo 是否可访问?)"; exit 1; }
  TEMPLATE_DIR="$TMP_TEMPLATE"
fi

# ---------- 1. 安装 Docker ----------
if [[ $SKIP_DOCKER -eq 0 ]] && ! command -v docker >/dev/null 2>&1; then
  c "未检测到 Docker, 安装 (Ubuntu/Debian)..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
  ok "Docker 已安装: $(docker --version)"
fi
if [[ $SKIP_DOCKER -eq 0 ]] && ! docker compose version >/dev/null 2>&1; then
  c "安装 Docker Compose 插件..."
  apt-get update -y && apt-get install -y docker-compose-plugin
fi
docker compose version >/dev/null 2>&1 || { err "Docker Compose 不可用"; exit 1; }

# ---------- 2. 安装 nginx(宿主) ----------
if ! command -v nginx >/dev/null 2>&1; then
  c "安装 nginx..."
  apt-get update -y && apt-get install -y nginx
fi

# ---------- 3. 生成 frps.toml ----------
mkdir -p /etc/frp /var/log/frps /etc/nginx/conf.d /etc/nginx/certs
c "生成 /etc/frp/frps.toml ..."
sed -e "s/REPLACE_FRP_TOKEN/${TOKEN}/g" \
    -e "s/REPLACE_FRP_DASH_PWD/${DASH_PWD}/g" \
    -e "s/^vhostHTTPPort = .*/vhostHTTPPort = ${VHOST_PORT}/" \
    "$TEMPLATE_DIR/frps.toml" > /etc/frp/frps.toml
ok "frps.toml 已生成"

# ---------- 4. 处理证书 ----------
CERT_BASENAME=""
if [[ -n "$CERT_FILE" && -n "$KEY_FILE" ]]; then
  CERT_BASENAME="$(basename "$CERT_FILE" .crt)"
  cp "$CERT_FILE" "/etc/nginx/certs/${CERT_BASENAME}.crt"
  cp "$KEY_FILE"  "/etc/nginx/certs/${CERT_BASENAME}.key"
  chmod 600 "/etc/nginx/certs/${CERT_BASENAME}.key"
  ok "证书已安装: ${CERT_BASENAME}.crt/.key"
else
  CERT_BASENAME="__PLACEHOLDER__"
  c "未提供 --cert/--key, 生成占位配置。请稍后手动放置证书到 /etc/nginx/certs/ 并修改 nginx 配置中的证书名后 reload。"
fi

# ---------- 5. 生成 nginx 配置 ----------
c "生成 /etc/nginx/conf.d/csctalon-frp.conf ..."
# 移除默认站点, 避免 default_server 抢占
rm -f /etc/nginx/sites-enabled/default
sed -e "s/REPLACE_FRP_DOMAIN/${DOMAIN}/g" \
    -e "s/REPLACE_FRP_CERT_NAME/${CERT_BASENAME}/g" \
    -e "s|127.0.0.1:8080|127.0.0.1:${VHOST_PORT}|g" \
    "$TEMPLATE_DIR/nginx.conf" > /etc/nginx/conf.d/csctalon-frp.conf
nginx -t || { err "nginx 配置校验失败, 请检查"; exit 1; }
ok "nginx 配置已生成并通过校验"

# ---------- 6. 启动 frps 容器 ----------
c "启动 frps 容器 (v${FRP_VERSION})..."
docker rm -f frps 2>/dev/null || true
docker run -d --name frps --restart unless-stopped \
  --network host \
  -v /etc/frp/frps.toml:/etc/frp/frps.toml:ro \
  -v /var/log/frps:/var/log/frps \
  "snowdreamtech/frps:${FRP_VERSION}"
sleep 2
docker ps --filter name=frps --format '{{.Status}}'
ok "frps 已启动"

# ---------- 7. 启用并校验 nginx ----------
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx
systemctl --no-pager -l status nginx --no-pager 2>/dev/null | head -3 || true
ok "nginx 已重启"

# ---------- 8. 防火墙提示 ----------
PUBLIC_IP="$(curl -fsSL --max-time 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
echo ""
ok "云端转发机部署完成!"
echo "  域名       : https://${DOMAIN}"
echo "  云端公网IP : ${PUBLIC_IP}"
echo "  隧道端口   : 7000 (安全组需放行, 建议仅对办公/内网出口IP开放)"
echo "  业务端口   : 443 (HTTPS, 需放行); 其余 8080/7500 均绑 127.0.0.1 无需开放"
echo ""
echo "  下一步(本地服务器)执行:"
echo "    bash deploy/frp/local/install-frpc.sh \\"
echo "      --server-host ${PUBLIC_IP} --server-port 7000 \\"
echo "      --token <同一token> --domain ${DOMAIN}"
echo ""
echo "  安全组建议:"
echo "    443  → 0.0.0.0/0 (HTTPS)"
echo "    7000 → 仅办公/内网出口IP (frp 隧道)"
echo "    22   → 仅办公IP"
echo "  备注: 若 --cert 未传, 请把证书放 /etc/nginx/certs/<域名>.crt/.key 后 systemctl reload nginx"
