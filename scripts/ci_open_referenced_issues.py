#!/usr/bin/env python3
"""CI 辅助脚本：从 stdin 读取工单 JSON（Gitea API 响应），解析正文引用的 #N 工单号，
查询各引用工单状态，输出仍为 open 的编号列表（逗号分隔）。

用途：issue-close-on-merge.yml 的子工单判定（A-20260903-001 §2.3 / O-11 / R-P2-15）。
父子工单在本仓库以「父单正文引用 #N」关联；引用不存在/查询失败时保守不视为阻塞。

用法：
    curl -s -H "Authorization: token $TOKEN" "$API/issues/273" \
      | python3 scripts/ci_open_referenced_issues.py --api "$API" --exclude 273

环境变量：GITEA_TOKEN（认证令牌，workflow 注入）。
输出：open 状态的引用工单号，逗号分隔（无则空串）。
"""

import argparse
import json
import os
import re
import sys
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True, help="Gitea repo API base, e.g. http://host/api/v1/repos/o/r")
    ap.add_argument("--exclude", type=int, default=0, help="排除自身的工单号")
    args = ap.parse_args()

    try:
        issue = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # 输入异常 → 保守不阻塞
    body = issue.get("body") or ""

    refs = sorted({int(n) for n in re.findall(r"#(\d+)", body)} - {args.exclude})
    token = os.environ.get("GITEA_TOKEN", "")

    open_refs: list[int] = []
    for n in refs:
        try:
            req = urllib.request.Request(
                f"{args.api}/issues/{n}",
                headers={"Authorization": f"token {token}"},
            )
            state = json.loads(urllib.request.urlopen(req, timeout=10).read()).get("state", "")
            if state == "open":
                open_refs.append(n)
        except Exception:  # noqa: BLE001 引用不存在/网络异常 → 保守不视为子工单阻塞
            pass

    print(",".join(str(n) for n in open_refs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
