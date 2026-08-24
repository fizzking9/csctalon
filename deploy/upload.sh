#!/usr/bin/env bash
# =============================================================================
# upload.sh — 发布包上传脚本
# 在**本地开发机**执行: 把 package.sh 的产物上传到阿里云服务器。
#
# 用法:
#   ./deploy/upload.sh root@1.2.3.4                 # 上传 dist/latest.tar.gz
#   ./deploy/upload.sh root@1.2.3.4 dist/csctalon-xxx.tar.gz
#
# 前提: 本地已配置到服务器的 SSH 免密登录(scp 可达), 服务器上 /opt/csctalon/packages 已存在
#       (install-server.sh 会自动创建; 若还没跑过安装, 先手动 mkdir -p 或直接用 install-server.sh 的
#       引导流程: 见 deploy/README.md「首次部署」)
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HOST="${1:?用法: ./deploy/upload.sh <user@server-ip> [包文件]}"
PKG="${2:-$ROOT_DIR/dist/latest.tar.gz}"

if [[ ! -f "$PKG" ]]; then
  echo "✘ 发布包不存在: $PKG (先运行 ./deploy/package.sh)" >&2
  exit 1
fi

echo "▶ 上传 ${PKG} → ${HOST}:/opt/csctalon/packages/"
ssh "$HOST" "mkdir -p /opt/csctalon/packages"
scp "$PKG" "${HOST}:/opt/csctalon/packages/"

echo ""
echo "✔ 上传完成。接下来在服务器上执行:"
echo "   首次部署  : sudo /opt/csctalon/packages/csctalon-*/deploy/install-server.sh"
echo "   已有实例  : sudo /opt/csctalon/packages/csctalon-*/deploy/upgrade.sh /opt/csctalon/packages/$(basename "$PKG")"
