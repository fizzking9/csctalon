#!/usr/bin/env bash
# =============================================================================
# cleanup-frps.sh — 云端 frps 一键清理脚本
#
# 作用: 清理云端 frp 服务端(frps)部署, 支持旧 docker run 方式与统一 compose 方式。
#   默认只停服务不删数据(安全); 加 --purge 连配置/日志/镜像一起删。
#
# 用法(云端服务器, root 或 sudo):
#   方式一: git raw 一条命令(gitea 默认, 无需克隆/上传)
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/cloud/cleanup-frps.sh | \
#       sudo bash -s -- [--purge] [--compose-dir <目录>]
#   方式二: 本地脚本(已克隆/上传仓库)
#     bash deploy/frp/cloud/cleanup-frps.sh [--purge] [--compose-dir <目录>]
#
# 参数:
#   --purge         连配置(/etc/frp)、日志(/var/log/frps)、镜像一并删除(默认只停容器)
#   --compose-dir   统一 compose 所在目录(默认自动探测: 当前目录或 /opt/csctalon/frp)
#   --repo <base>   模板来源 git raw base(默认 gitea main 分支)
#   -h|--help       帮助
#
# 注意:
#   - 不处理 1Panel OpenResty 反代/网站/证书: 需到 1Panel 界面手动停用或删除
#     (网站 → 找到绑定域名的反代站点 → 停用/删除; 证书在 网站→SSL 里删)。
#   - 不处理防火墙: 清理后若不再需要 frp, 需自行 ufw deny 7000/8080 或删安全组规则。
#   - 这是云端脚本; 本地 frpc 用 deploy/frp/local/cleanup-frpc.sh。
# =============================================================================
set -euo pipefail

PURGE=0
COMPOSE_DIR=""
REPO_BASE="http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main"

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge)        PURGE=1; shift ;;
    --compose-dir)  COMPOSE_DIR="${2:?--compose-dir 需要目录}"; shift 2 ;;
    --repo)         REPO_BASE="${2:?--repo 需要 raw base url}"; shift 2 ;;
    -h|--help)
      cat >&2 <<'HELP'
用法: cleanup-frps.sh [--purge] [--compose-dir <目录>]

云端 frp 服务端(frps)一键清理:
  - 停止/删除 frps 容器(旧 docker run 与统一 compose 两种方式都处理)
  - --purge 时: 连 /etc/frp(配置)、/var/log/frps(日志)、frps 镜像一并删除
  - 不处理 1Panel OpenResty 反代/证书/防火墙, 请到 1Panel 界面与 ufw 手动处理
HELP
      exit 0 ;;
    *) echo "✘ 未知参数: $1"; exit 1 ;;
  esac
done

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
warn() { echo -e "\033[33m⚠ $*\033[0m" >&2; }

# ---------- 1. 停止/删除 frps 容器 ----------
c "停止/删除 frps 容器..."

# 方式一: 旧 docker run 起的容器
if docker ps -a --filter name=^/frps$ --format '{{.Names}}' | grep -qx frps; then
  docker rm -f frps
  ok "已删除 frps 容器(docker run 方式)"
fi

# 方式二: 统一 compose(自动探测目录)
if [[ -z "$COMPOSE_DIR" ]]; then
  for d in "$PWD" /opt/csctalon/frp; do
    if [[ -f "$d/docker-compose.yml" ]] && grep -q 'name: frp' "$d/docker-compose.yml" 2>/dev/null; then
      COMPOSE_DIR="$d"; break
    fi
  done
fi
if [[ -n "$COMPOSE_DIR" && -f "$COMPOSE_DIR/docker-compose.yml" ]]; then
  c "检测到统一 compose: $COMPOSE_DIR/docker-compose.yml"
  (cd "$COMPOSE_DIR" && docker compose --profile frps down 2>/dev/null || true)
  # 兼容老命名(compose v1)
  (cd "$COMPOSE_DIR" && docker-compose --profile frps down 2>/dev/null || true)
  ok "统一 compose frps 已停止并移除容器"
fi

# 兜底: 无论何种方式, 确保没有 frps 容器残留
if docker ps -a --filter name=^/frps$ --format '{{.Names}}' | grep -qx frps; then
  docker rm -f frps
  warn "已兜底删除残留 frps 容器"
fi

# ---------- 2. --purge: 删配置/日志/镜像 ----------
if [[ $PURGE -eq 1 ]]; then
  c "--purge: 删除配置/日志/镜像..."
  rm -rf /etc/frp /var/log/frps
  ok "已删除 /etc/frp、/var/log/frps"
  docker rmi snowdreamtech/frps:0.61.1 2>/dev/null || warn "frps 镜像不存在或已被删(可忽略)"
  docker image prune -f >/dev/null 2>&1 || true
else
  warn "未加 --purge: 保留配置(/etc/frp)与日志(/var/log/frps), 便于重新部署复用"
fi

echo ""
ok "云端 frps 清理完成!"
if [[ $PURGE -eq 1 ]]; then
  echo "  已删除: frps 容器 + /etc/frp + /var/log/frps + frps 镜像"
else
  echo "  已停止/删除 frps 容器; 配置与日志保留(可用 --purge 彻底删除)"
fi
echo "  还需手动: ① 1Panel 网站→停用/删除反代站点与证书 ② ufw deny 7000/8080(不再需要时)"
echo "  本地 frpc 请用: deploy/frp/local/cleanup-frpc.sh"
