#!/usr/bin/env bash
# ============================================================
# 架构文档锁定脚本(人工审核后执行)
# ============================================================
# 用途: 把 docs/arch/_pending/ 下待审核的设计文档锁定为 LOCKED 架构文档。
# 规则(对应 AI 协作治理规范 4.3 阶段切换 SOP 第 2 步):
#   1. 人工逐份审核,确认文档与实际实现一致
#   2. 重命名为 A-YYYYMMDD-序号-简述.md
#   3. 文件头标注 状态: LOCKED（v1.0）
#   4. 移到 docs/arch/ 根目录
#   5. 打 git tag: arch/A-YYYYMMDD-序号
#
# ⚠️ 本脚本只做机械步骤; "锁哪几份" 必须由人工决定(人机职责边界)。
#    默认锁首批 5 份(见 docs/arch/_pending/README.md 建议), 可用 --docs 覆盖。
#
# 用法:
#   ./scripts/lock-arch-docs.sh                    # 锁定默认首批 5 份
#   ./scripts/lock-arch-docs.sh --docs "A文件 B文件"  # 指定要锁的文档(可含子串)
#   ./scripts/lock-arch-docs.sh --date 20260901     # 覆盖锁定日期
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PENDING_DIR="$ROOT/docs/arch/_pending"
ARCH_DIR="$ROOT/docs/arch"

# 默认首批 5 份(见 _pending/README.md 优先级建议)
DEFAULT_DOCS=(
  "dashboard-system-rebuild.md"
  "refund-cashflow-views-rebuild.md"
  "staff-assignment-history.md"
  "metric-alerting-notification-design.md"
  "时间字段统计规范修订.md"
)

DATE="$(date +%Y%m%d)"
SELECTED=()
ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --docs)
      shift
      if [[ $# -eq 0 ]]; then
        echo "❌ --docs 需要参数(空格分隔的文档名)"
        exit 1
      fi
      SELECTED=($1)
      shift
      ;;
    --date)
      shift
      DATE="$2"
      shift
      ;;
    *)
      echo "❌ 未知参数: $1"
      exit 1
      ;;
  esac
done

if [[ ${#SELECTED[@]} -eq 0 ]]; then
  SELECTED=("${DEFAULT_DOCS[@]}")
fi

echo "=================================================="
echo " 架构文档锁定 (日期: $DATE)"
echo " 锁定数量: ${#SELECTED[@]}"
echo "=================================================="

seq=0
for name in "${SELECTED[@]}"; do
  src="$PENDING_DIR/$name"
  if [[ ! -f "$src" ]]; then
    echo "⚠️  跳过(文件不存在): $name"
    continue
  fi
  seq=$((seq + 1))
  # 简述: 去掉 .md 扩展名, 转小写、下划线(文件名安全)
  brief="$(basename "$name" .md | tr 'A-Z' 'a-z' | tr ' _' '-')"
  new_name="A-${DATE}-$(printf '%03d' "$seq")-${brief}.md"
  dest="$ARCH_DIR/$new_name"

  echo ""
  echo "── 锁定 #${seq}: $name → $new_name"

  # 1. git mv 保留历史
  git mv "$src" "$dest"

  # 2. 文件头插入 LOCKED 状态标记(幂等: 已有则跳过)
  if grep -q '状态.*LOCKED' "$dest"; then
    echo "   状态标记已存在, 跳过"
  else
    # 在第一个标题行(# ...)之后插入状态块
    tmp="$(mktemp)"
    awk -v newname="$new_name" '
      NR==1 && /^#/ { print; print ""; print "**状态**：LOCKED（v1.0）"; print "**锁定日期**：'"$DATE"'"; print "**锁定脚本**：scripts/lock-arch-docs.sh"; print ""; next }
      { print }
    ' "$dest" > "$tmp"
    mv "$tmp" "$dest"
    echo "   已标记 LOCKED"
  fi

  # 3. 打 git tag
  tag="arch/A-${DATE}-$(printf '%03d' "$seq")"
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    echo "   tag 已存在: $tag"
  else
    git tag "$tag"
    echo "   已打 tag: $tag"
  fi
done

echo ""
echo "=================================================="
echo " ✅ 完成。已锁定 ${seq} 份文档。"
echo " 请人工终审后提交: git add -A && git commit"
echo " 推送 tag: git push origin --tags"
echo " 后续: 更新 docs/arch/_pending/README.md 移除已锁定项"
echo "=================================================="
