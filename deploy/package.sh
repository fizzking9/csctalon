#!/usr/bin/env bash
# =============================================================================
# package.sh — 本地发布包打包脚本
# 在**本地开发机**执行: 把当前代码打成可部署的 tar.gz, 供 upload.sh 上传到阿里云。
#
# 用法:
#   ./deploy/package.sh                     # 版本号自动取 git tag/commit
#   ./deploy/package.sh v1.2.0              # 指定版本号
#
# 产物:
#   ./dist/csctalon-<版本>-<时间戳>.tar.gz
#   ./dist/latest.tar.gz                    # 固定名, 上传脚本默认取这个
#
# 排除内容: .git / node_modules / data / logs / backend/uploads / dist /
#           __pycache__ / .venv / .env / docker-compose.override.yml 等
#           (数据、日志、附件、生产 .env 均在服务器侧管理, 不进发布包)
# ⚠️ 发布纯净性(2026-08-30 部署审计): 与 upgrade.sh 的 --git 模式清理清单保持同一口径,
#    只带运行期内容(backend/web/deploy/docker-compose.yml/.env.example/README.md)。
#    - docs/ 纯设计/审查文档, 不进发布包(git 模式同样清理)
#    - .trae/.dsh/.gitea/.workbuddy 等 AI/CI 工具产物不进发布包
#    - scripts/ backend/scripts/ 调试探针脚本不进发布包(backend/.dockerignore 也已排除)
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ---------- 版本号 ----------
if [[ -n "${1:-}" ]]; then
  VERSION="$1"
else
  VERSION="$(git describe --tags --always --dirty 2>/dev/null || git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
fi
STAMP="csctalon-${VERSION}-$(date +%Y%m%d%H%M%S)"
OUT_DIR="$ROOT_DIR/dist"
OUT_FILE="$OUT_DIR/${STAMP}.tar.gz"

echo "▶ 打包版本: ${VERSION}"
echo "▶ 输出: ${OUT_FILE}"

mkdir -p "$OUT_DIR"

# 版本标记文件(服务器侧升级脚本可读)
echo "${VERSION}" > "$ROOT_DIR/VERSION.tmp"

# ---------- 打包 ----------
# 注意: 根 data/logs/dist/backups 不在显式清单里, 无需 exclude;
#       tar --exclude 不能写 data/logs(会匹配任意层级, 误伤 web/src/views/data/
#       源码目录——曾导致前端 Ingest.vue 缺失构建失败)
# backend/scripts: 先整体排除调试脚本, 再用 tar r 追加仅两个登录脚本(#148)
TMP_TAR="${OUT_FILE%.gz}"
tar cf "$TMP_TAR" \
  --exclude='.git' \
  --exclude='.gitignore' \
  --exclude='.gitea' \
  --exclude='.dsh' \
  --exclude='.trae' \
  --exclude='.workbuddy' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='.venv_test' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='.DS_Store' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='.env.test' \
  --exclude='docker-compose.override.yml' \
  --exclude='backend/uploads' \
  --exclude='backend/scripts' \
  --exclude='backend/tests' \
  --exclude='scripts' \
  --exclude='docs' \
  --exclude='*.dump' \
  --exclude='*.sql' \
  docker-compose.yml \
  .env.example \
  README.md \
  backend \
  web \
  deploy \
  VERSION.tmp 2>/dev/null
# 追加仅两个登录脚本(运维工具, 生产需 headless 扫码更新登录态)
mkdir -p backend/scripts
tar rf "$TMP_TAR" \
  backend/scripts/tencent_doc_login.py \
  backend/scripts/kdocs_login.py 2>/dev/null
gzip -f "$TMP_TAR"
rm -f "$ROOT_DIR/VERSION.tmp"

# 固定名(供 upload.sh / 快速引用)
cp "$OUT_FILE" "$OUT_DIR/latest.tar.gz"

echo ""
echo "✔ 打包完成:"
echo "    ${OUT_FILE}  ($(du -h "$OUT_FILE" | cut -f1))"
echo "    ${OUT_DIR}/latest.tar.gz"
echo ""
echo "下一步: ./deploy/upload.sh <user@server-ip>"
