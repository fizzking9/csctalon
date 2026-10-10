#!/usr/bin/env bash
# 安装 commit-msg 钩子：提交信息必须携带 #工单号（#216 / R-P2-9）
# 用法: bash scripts/install-commit-msg-hook.sh
#
# 与 pre-commit（gitleaks，见 scripts/install-gitleaks-hook.sh）是两个独立钩子文件，
# 互不影响、可分别安装；判据统一调用入库脚本 scripts/check-commit-issue.sh，
# 因此判据更新随仓库走，无需重装钩子。
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "❌ 不在 git 仓库中" >&2
  exit 1
}
HOOK_DIR="$REPO_ROOT/.git/hooks"
HOOK_FILE="$HOOK_DIR/commit-msg"
CHECK_SCRIPT="$REPO_ROOT/scripts/check-commit-issue.sh"

if [ ! -f "$CHECK_SCRIPT" ]; then
  echo "❌ 找不到 $CHECK_SCRIPT（请在仓库根目录执行本脚本）" >&2
  exit 1
fi

mkdir -p "$HOOK_DIR"

# 注意用字面量 heredoc（'EOF'）：hook 内的 $1 / $(...) 必须在触发时而非安装时展开。
cat > "$HOOK_FILE" <<'EOF'
#!/usr/bin/env bash
# commit-msg: 校验提交信息携带 #工单号（#216）
# 判据见 scripts/check-commit-issue.sh（本地与 CI 同源）。
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"

bash "$REPO_ROOT/scripts/check-commit-issue.sh" --message-file "$1"
EOF

chmod +x "$HOOK_FILE"
echo "✅ commit-msg 工单号钩子已安装: ${HOOK_FILE}"
echo "   判据脚本: ${CHECK_SCRIPT}（随仓库更新，无需重装）"
echo "   跳过方式: 提交信息加 [skip-issue]（显式声明），或紧急时 git commit --no-verify"
echo "           （--no-verify 会同时绕过 gitleaks pre-commit，慎用）"
