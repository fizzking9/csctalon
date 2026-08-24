#!/usr/bin/env bash
# =============================================================================
# rollback.sh — 服务器回滚脚本
# 回滚 = ① 恢复数据到指定备份点(可选) + ② 代码切回历史版本。
#
# 用法:
#   sudo ./deploy/rollback.sh                       # 交互式: 选择代码版本 + 是否恢复备份
#   sudo ./deploy/rollback.sh --version <版本目录名> # 指定代码版本
#   sudo ./deploy/rollback.sh --restore <备份包>    # 仅恢复数据(不切代码)
#
# 说明:
#   - 升级失败时 upgrade.sh 已自动回滚代码; 若需要把数据也回退到升级前,
#     用 --restore 指定 upgrade.sh 在 /opt/csctalon/backups 生成的备份包。
#   - 数据恢复是"恢复到备份时间点", 备份之后的增量数据会丢失, 执行前请确认。
# =============================================================================
set -euo pipefail

APP_DIR="/opt/csctalon"
CURRENT="$APP_DIR/current"
ENV_FILE="$APP_DIR/.env"

VERSION=""
RESTORE_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:?}"; shift 2 ;;
    --restore) RESTORE_FILE="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "✘ 未知参数: $1" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "✘ 请用 root 或 sudo 执行" >&2; exit 1; fi

c() { echo -e "\033[36m▶ $*\033[0m"; }
ok() { echo -e "\033[32m✔ $*\033[0m"; }
err() { echo -e "\033[31m✘ $*\033[0m" >&2; }

# ---------- 1. 恢复数据(可选) ----------
if [[ -n "$RESTORE_FILE" ]]; then
  if [[ ! -f "$RESTORE_FILE" ]]; then err "备份包不存在: $RESTORE_FILE"; exit 1; fi
  c "恢复数据备份: $RESTORE_FILE"
  bash "$CURRENT/deploy/restore.sh" --env-file "$ENV_FILE" "$RESTORE_FILE"
fi

# ---------- 2. 选择并切换代码版本 ----------
mapfile -t VERSIONS < <(ls -1dt "$APP_DIR"/releases/* 2>/dev/null || true)
if [[ ${#VERSIONS[@]} -eq 0 ]]; then
  err "没有可用的历史版本 (releases 目录为空)"
  exit 1
fi

if [[ -z "$VERSION" ]]; then
  echo "可选历史版本:"
  for i in "${!VERSIONS[@]}"; do
    mark=""
    [[ "${VERSIONS[$i]}" == "$(readlink "$CURRENT")" ]] && mark="  ← 当前"
    echo "  [$((i+1))] ${VERSIONS[$i]}${mark}"
  done
  read -r -p "选择要回滚到的版本序号: " sel
  [[ "$sel" =~ ^[0-9]+$ ]] && [[ $sel -ge 1 && $sel -le ${#VERSIONS[@]} ]] || { err "无效序号"; exit 1; }
  VERSION="${VERSIONS[$((sel-1))]}"
fi

if [[ ! -d "$VERSION" ]]; then
  err "版本目录不存在: $VERSION"
  exit 1
fi

c "切换代码: $(readlink "$CURRENT") → $VERSION"
ln -sfn "$VERSION" "$CURRENT"
cd "$CURRENT"
docker compose --env-file "$ENV_FILE" up -d --build

# 健康检查
c "等待服务健康..."
HEALTHY=0
for _ in $(seq 1 40); do
  if docker compose --env-file "$ENV_FILE" ps --status running 2>/dev/null | grep -q backend \
     && curl -sf -o /dev/null "http://127.0.0.1:8000/health"; then
    HEALTHY=1; break
  fi
  sleep 3
done
[[ $HEALTHY -eq 0 ]] && { err "回滚后服务未健康, 检查日志"; exit 1; }

ok "回滚完成: 当前版本 $(cat "$CURRENT/VERSION.tmp" 2>/dev/null || basename "$VERSION")"
