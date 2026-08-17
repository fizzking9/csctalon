#!/usr/bin/env bash
# 安装 gitleaks 预提交钩子, 防止凭证再次硬编码入库
# 用法: bash scripts/install-gitleaks-hook.sh
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "❌ 不在 git 仓库中" >&2
  exit 1
}
HOOK_DIR="$REPO_ROOT/.git/hooks"
HOOK_FILE="$HOOK_DIR/pre-commit"

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "❌ 未安装 gitleaks。请先安装:" >&2
  echo "   macOS:  brew install gitleaks" >&2
  echo "   或:     pip install git-filter-repo  # gitleaks 需单独装" >&2
  echo "   或:     参考 https://github.com/gitleaks/gitleaks/releases" >&2
  exit 1
fi

mkdir -p "$HOOK_DIR"

cat > "$HOOK_FILE" <<'EOF'
#!/usr/bin/env bash
# pre-commit: gitleaks 扫描暂存区, 命中凭证即阻断提交
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"

# 仅扫描暂存区变更, 避免全量扫描拖慢提交
if ! gitleaks protect --staged --source "$REPO_ROOT" \
      --config "$REPO_ROOT/.gitleaks.toml" --redact --no-banner; then
  echo ""
  echo "❌ gitleaks 检测到疑似凭证, 已阻止提交。" >&2
  echo "   若为误报, 可用 'git commit --no-verify' 跳过(不推荐)。" >&2
  exit 1
fi

exit 0
EOF

chmod +x "$HOOK_FILE"
echo "✅ gitleaks 预提交钩子已安装: $HOOK_FILE"
echo "   配置: $REPO_ROOT/.gitleaks.toml"
echo "   手动全量扫描: gitleaks detect --source $REPO_ROOT --config $REPO_ROOT/.gitleaks.toml --redact"
