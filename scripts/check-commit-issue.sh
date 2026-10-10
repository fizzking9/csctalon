#!/usr/bin/env bash
# 提交信息工单号校验（#216 / R-P2-9）
#
# 本地 commit-msg 钩子与 Gitea CI 共用本脚本，保证两处判据完全一致（单一事实来源）：
#   bash scripts/check-commit-issue.sh --message-file <file>   # 校验单条信息（commit-msg hook）
#   bash scripts/check-commit-issue.sh --range <revspec>       # 校验区间内每条提交（CI，如 before..after）
#   bash scripts/check-commit-issue.sh --head-only             # 仅校验 HEAD（CI 无法确定区间时的回退）
#
# 判据（任一满足即放行该条提交）：
#   1) merge 提交：subject 以 "Merge " 开头（git/github/gitea 自动合并产生，天然无工单）；
#   2) 提交信息全文含显式跳过标记 [skip-issue]（大小写不敏感）——仅限纯维护/版本号 bump 等
#      确无工单的场景，留下可审计的显式声明；
#   3) 提交信息含 "#" + 数字（#123）。
# 其余一律判为不合规；存在任一条不合规即退出码 1（空区间视为通过）。
set -euo pipefail

ISSUE_RE='#[0-9]+'
SKIP_RE='\[skip-issue\]'

# check_one <标识>，提交信息从 stdin 读入；合规返回 0，不合规打印一行并返回 1。
check_one() {
  local ident="$1" msg subject
  msg="$(cat)"
  subject="$(printf '%s\n' "$msg" | sed -n '1p')"

  # 1) merge 提交（英文 "Merge ..."；中文合并极少出现在自动提交里，一并兼容大小写）
  case "$subject" in
    "Merge "*|"merge "*) return 0 ;;
  esac
  # 2) 显式跳过标记（全文、大小写不敏感）
  if printf '%s\n' "$msg" | grep -Eiq "$SKIP_RE"; then
    return 0
  fi
  # 3) 携带工单号
  if printf '%s\n' "$msg" | grep -Eq "$ISSUE_RE"; then
    return 0
  fi

  printf '  ✗ %s\n      首行: %s\n' "$ident" "$subject" >&2
  return 1
}

check_sha() {
  local sha="$1" rc=0 ident
  ident="$(git log -1 --format='%h %s' "$sha")"
  if ! git log -1 --format=%B "$sha" | check_one "$ident"; then
    rc=1
  fi
  return "$rc"
}

main() {
  local mode="${1:-}" arg="${2:-}" rc=0 sha
  case "$mode" in
    --message-file)
      [ -n "$arg" ] || { echo "用法: $0 --message-file <file>" >&2; exit 2; }
      if ! check_one "commit-msg" < "$arg"; then rc=1; fi
      ;;
    --range)
      [ -n "$arg" ] || { echo "用法: $0 --range <revspec>" >&2; exit 2; }
      # 逐个提交校验；收集所有违规而非遇第一条即停，便于作者一次改全。
      while IFS= read -r sha; do
        [ -z "$sha" ] && continue
        if ! check_sha "$sha"; then rc=1; fi
      done < <(git rev-list "$arg")
      ;;
    --head-only)
      if ! check_sha "HEAD"; then rc=1; fi
      ;;
    *)
      echo "用法: $0 --message-file <file> | --range <revspec> | --head-only" >&2
      exit 2
      ;;
  esac

  if [ "$rc" -ne 0 ]; then
    {
      echo ""
      echo "❌ 上述提交信息缺少工单号（规范: 信息中写 #工单号，如 'fix(#123): ...'）。"
      echo "   确无工单的纯维护/合并提交，请在信息中显式加 [skip-issue]；merge 提交自动放行。"
    } >&2
  fi
  exit "$rc"
}

main "$@"
