#!/usr/bin/env python3
"""csctalon 工单闭环脚本：建单 / 凭证关单（勾选验收标准 + 凭证评论 + 关闭）。

对应 SKILL.md 第 3 步与第 8 步。Gitea API v1，token 读取顺序：
~/.netrc（按 host 匹配）→ `git credential fill` 兜底。

用法:
  # 建单（正文须含 `- [ ] A: ...` 形式的验收标准，供关单时勾选）
  python3 gitea_flow.py create --title "xxx" --body-file /tmp/issue.md

  # 凭证关单：自动把正文全部 `- [ ]` 勾成 `- [x]`，POST 凭证评论，state=closed
  python3 gitea_flow.py close --issue 296 --evidence-file /tmp/evidence_296.md
  # 降级验收、保留未达成项不勾：
  python3 gitea_flow.py close --issue 296 --evidence-file e.md --no-tick
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_GITEA = "http://10.147.17.41:3000"
DEFAULT_REPO = "tanzk/csctalon"


def read_token(host: str) -> str:
    """token 读取：~/.netrc 优先（按 host 子串匹配），失败回退 git credential fill。"""
    netrc = Path.home() / ".netrc"
    if netrc.exists():
        lines = netrc.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("machine") and host in line:
                for follow in lines[i:i + 3]:
                    m = re.match(r"\s*password\s+(\S+)", follow)
                    if m:
                        return m.group(1)
    out = subprocess.run(
        ["git", "credential", "fill"],
        input=f"protocol=http\nhost={host}\n\n",
        capture_output=True, text=True,
    )
    m = re.search(r"^password=(.+)$", out.stdout, re.M)
    if m:
        return m.group(1).strip()
    sys.exit("拿不到 Gitea token（~/.netrc 与 git credential fill 均失败）")


class Gitea:
    def __init__(self, base: str, repo: str):
        self.base = base.rstrip("/")
        self.repo = repo
        host = re.sub(r"^https?://", "", self.base).split(":")[0]
        self.hdr = {"Authorization": f"token {read_token(host)}",
                    "Content-Type": "application/json"}
        self.api = f"{self.base}/api/v1/repos/{repo}/issues"

    def req(self, method: str, url: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(url, data=data, headers=self.hdr, method=method)
        with urllib.request.urlopen(r) as resp:
            body = resp.read()
            return json.loads(body) if body else {}

    def create(self, title: str, body: str, labels: list[str]) -> dict:
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return self.req("POST", self.api, payload)

    def tick_all(self, num: int) -> int:
        """把正文全部 `- [ ]` 勾成 `- [x]`，PATCH 回去，返回勾选条数。"""
        issue = self.req("GET", f"{self.api}/{num}")
        body, n = issue.get("body") or "", 0
        new = []
        for ln in body.split("\n"):
            if ln.startswith("- [ ] "):
                new.append("- [x] " + ln[6:])
                n += 1
            else:
                new.append(ln)
        if n:
            self.req("PATCH", f"{self.api}/{num}", {"body": "\n".join(new)})
        return n

    def close(self, num: int, evidence: str, tick: bool) -> None:
        if tick:
            n = self.tick_all(num)
            print(f"已勾选验收标准 {n} 条")
        self.req("POST", f"{self.api}/{num}/comments", {"body": evidence})
        self.req("PATCH", f"{self.api}/{num}", {"state": "closed"})
        print(f"凭证评论已发，issue #{num} 已关闭")


def main() -> None:
    ap = argparse.ArgumentParser(description="csctalon 工单闭环（建单/凭证关单）")
    ap.add_argument("action", choices=["create", "close"])
    ap.add_argument("--title")
    ap.add_argument("--body-file")
    ap.add_argument("--issue", type=int)
    ap.add_argument("--evidence-file")
    ap.add_argument("--label", action="append", default=[])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--gitea", default=DEFAULT_GITEA)
    ap.add_argument("--no-tick", action="store_true",
                    help="close 时不自动勾选（降级验收场景）")
    a = ap.parse_args()
    g = Gitea(a.gitea, a.repo)

    if a.action == "create":
        if not (a.title and a.body_file):
            sys.exit("create 需要 --title 与 --body-file")
        body = Path(a.body_file).read_text()
        if not body.strip():
            sys.exit("issue 正文为空")
        if "- [ ] " not in body:
            sys.exit('正文缺 "- [ ]" 验收标准（关单时无处可勾），请补齐后再建')
        issue = g.create(a.title, body, a.label)
        num = issue["number"]
        print(f'#{num} {issue["html_url"]}')
    else:
        if not (a.issue and a.evidence_file):
            sys.exit("close 需要 --issue 与 --evidence-file")
        ev = Path(a.evidence_file).read_text()
        if "关闭凭证核验" not in ev:
            sys.exit('凭证文件缺 "## 关闭凭证核验" 标题，见 SKILL.md 模板')
        g.close(a.issue, ev, tick=not a.no_tick)


if __name__ == "__main__":
    main()
