#!/usr/bin/env bash
# =============================================================================
# cleanup-frpc.sh — 本地 frpc 一键清理脚本
#
# 作用: 清理本地 frp 客户端(frpc)部署, 支持 binary+systemd 与 docker 两种方式。
#   默认只停服务不删数据(安全); 加 --purge 连配置/二进制/镜像一起删。
#
# 用法(本地服务器, root 或 sudo):
#   方式一: git raw 一条命令(gitea 默认, 无需克隆/上传)
#     curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/frp/local/cleanup-frpc.sh | \
#       sudo bash -s -- [--purge] [--install-dir <目录>]
#   方式二: 本地脚本(已克隆/上传仓库)
#     bash deploy/frp/local/cleanup-frpc.sh [--purge] [--install-dir <目录>]
#
# 参数:
#   --purge         连安装目录(/opt/csctalon/frpc)与镜像一并删除(默认只停服务)
#   --install-dir   安装目录(默认 /opt/csctalon/frpc)
#   -h|--help       帮助
#
# 注意:
#   - 不处理 本地 .env 的 CORS_ORIGINS 与备份 cron(sync-backup-to-cloud.sh),
#     如需一并移除请手动处理(见 README §8.5)。
#   - 这是本地脚本; 云端 frps 用 deploy/frp/cloud/cleanup-frps.sh。
# =============================================================================
set -euo pipefail

PURGE=0
INSTALL_DIR="/opt/csctalon/frpc"

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge)        PURGE=1; shift ;;
    --install-dir)  INSTALL_DIR="${2:?--install-dir 需要目录}"; shift 2 ;;
    -h|--help)
      cat >&2 <<'HELP'
用法: cleanup-frpc.sh [--purge] [--install-dir <目录>]

本地 frp 客户端(frpc)一键清理:
  - 停用并删除 systemd frpc 服务(binary 方式) / 停止并删除 csctalon-frpc 容器(docker 方式)
  - --purge 时: 连安装目录($INSTALL_DIR)与 frpc 镜像一并删除
  - 不处理 本地 .env CORS_ORIGINS 与备份 cron, 请手动处理
HELP
      exit 0 ;;
    *) echo "✘ 未知参数: $1"; exit 1 ;;
  esac
done

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
warn() { echo -e "\033[33m⚠ $*\033[0m" >&2; }

# ---------- 1. binary 方式: 停用并删除 systemd 服务 ----------
if systemctl list-unit-files 2>/dev/null | grep -q '^frpc.service'; then
  c "binary 方式: 停用并删除 systemd frpc 服务..."
  systemctl disable --now frpc 2>/dev/null || true
  rm -f /etc/systemd/system/frpc.service
  systemctl daemon-reload
  ok "已停用并删除 frpc.service"
fi

# ---------- 2. docker 方式: 停止并删除容器 ----------
if docker ps -a --filter name=^/csctalon-frpc$ --format '{{.Names}}' | grep -qx csctalon-frpc; then
  c "docker 方式: 停止并删除 csctalon-frpc 容器..."
  docker rm -f csctalon-frpc
  ok "已删除 csctalon-frpc 容器"
fi

# 兼容: 旧 docker-compose.frpc.yml 起的同名容器也一并清理
if docker ps -a --filter name=frpc --format '{{.Names}}' | grep -qx frpc; then
  docker rm -f frpc 2>/dev/null || true
  warn "已删除残留 frpc 容器"
fi

# ---------- 3. --purge: 删除安装目录与镜像 ----------
if [[ $PURGE -eq 1 ]]; then
  c "--purge: 删除安装目录($INSTALL_DIR)与镜像..."
  rm -rf "$INSTALL_DIR"
  ok "已删除 $INSTALL_DIR"
  docker rmi snowdreamtech/frpc:0.61.1 2>/dev/null || warn "frpc 镜像不存在或已被删(可忽略)"
  docker image prune -f >/dev/null 2>&1 || true
else
  warn "未加 --purge: 保留安装目录($INSTALL_DIR), 便于重新部署复用"
fi

echo ""
ok "本地 frpc 清理完成!"
if [[ $PURGE -eq 1 ]]; then
  echo "  已删除: frpc 服务/容器 + $INSTALL_DIR + frpc 镜像"
else
  echo "  已停止/删除 frpc 服务与容器; 安装目录保留(可用 --purge 彻底删除)"
fi
echo "  还需手动: ① 本地 .env CORS_ORIGINS 里的 https://<域名> ② 备份 cron(sync-backup-to-cloud.sh)"
echo "  云端 frps 请用: deploy/frp/cloud/cleanup-frps.sh"
