# A-20260903-001-工单自动化门禁与CI联动设计

**状态**：LOCKED（v1.0）
**日期**：2026-09-03
**架构师阶段产出**：AI 助手（工单驱动工作流规范落地补充）
**审查结论**：人工终审通过（2026-09-03）
**来源**：工单驱动工作流规范落地补充（docs/standards/issue-workflow.md §6.4 自动化钩子）
**关联规范**：[issue-workflow.md](../standards/issue-workflow.md)、[review-checklist.md](../standards/review-checklist.md)

> 目的：把「工单 × 四阶段」闭环里目前依赖人工的 3 个焊点，升级为 CI 机器强制，消除「代码已合并、工单还挂 Open」的状态漂移。

---

## 一、背景与问题

当前规范已建立「需求 → 架构 → 编码 → 测试」四阶段状态机，但以下 3 个环节仍靠人肉维持：

| # | 缺口 | 现状 | 后果 |
|---|---|---|---|
| G1 | PR 必须关联工单 | 文字约定，无校验 | 无工单的 PR 照样可合并 |
| G2 | CI 结果回写工单 | 人工贴测试结果 | 工单状态与代码真实质量脱节 |
| G3 | 合并后工单状态流转 | 人工改状态 | 代码已合并、工单仍 Open |

本设计将 3 项升级为 `.gitea/workflows/` 下的机器强制。

---

## 二、设计要点

### 2.1 G1 — PR 工单号门禁（新 workflow：`issue-gate.yml`）

- **触发**：`pull_request` 的 open / synchronize / reopen
- **校验规则**：PR 标题或描述中必须匹配 `#<数字>`（如 `#2`），否则 CI fail
- **豁免**：`docs/`、`*.md` 纯文档提交可豁免（避免文档修订被迫挂工单）
- **失败行为**：CI 红，PR 不可合并

```yaml
# 伪代码（非最终实现，最终以 Gitea Actions 语法为准）
on: pull_request
steps:
  - 读取 PR title + body
  - 若 (title+body) 不含 #\d+ 且 变更路径含非 docs 文件 → exit 1
```

### 2.2 G2 — CI 结果回写工单评论（扩展 `test.yml`）

- **触发**：`pull_request` 上 pytest/ruff 完成后
- **动作**：从 PR 标题/描述解析出 `#issue号`，调用 Gitea API `POST /repos/{owner}/{repo}/issues/{index}/comments`，贴入「CI 结果摘要」（通过/失败 + 失败用例数 + 链接）
- **依赖**：Gitea Secrets 存 `BOT_TOKEN`（具备 issues 写权限的最小权限令牌），**禁止硬编码**
- **幂等**：同一 commit 的同一阶段只贴一次，避免重复刷屏

### 2.3 G3 — 合并后工单状态自动流转（新 workflow：`issue-close-on-merge.yml`）

- **触发**：PR merge（`pull_request` closed 且 merged == true）
- **动作**：解析 `#issue号`，若该工单是「治理/缺陷/功能」单且无剩余未关闭子工单，则将其状态改为 `Closed` 并追加评论「已由 PR #X 合并交付」；若仍有未关闭子工单，仅评论不关闭
- **例外**：`incident`（事故单）**不自动关闭**，必须人工结案（因为事故闭环需业务确认，不能由机器判定）

---

## 三、Secrets 与权限

| 项 | 值 | 说明 |
|---|---|---|
| `GITEA_TOKEN` | 最小权限 token（仅 repo:issue 写 + 读 PR） | 存于仓库 Secrets，代码中零硬编码 |
| 令牌归属 | 建议用专用 bot 账号 | 避免用管理员个人令牌 |

> 遵守 AGENTS.md：令牌一律走 Secrets，禁止写入 workflow 文件。

---

## 四、风险清单

1. **误伤文档 PR**：G1 若不加豁免，文档改动会被迫挂工单 → 已设计 `docs/` 豁免。
2. **令牌泄露面**：workflow 内误打印 token → 规定只在环境变量使用，日志脱敏。
3. **事故单被误关**：G3 若不分类型自动关闭，事故单会跳过业务确认 → 已设计 `incident` 不自动关闭。
4. **重复评论刷屏**：同一 commit 多次重跑 CI → 已设计幂等去重。

---

## 五、实施步骤

1. 人工审核本设计（确认 G1 豁免范围、G3 例外类型）
2. 建 bot 账号 + 最小权限 token，配置仓库 Secrets
3. 新增 `issue-gate.yml`，先灰度（只 warn 不 fail），观察 1 周后收紧为 fail
4. 扩展 `test.yml` 回写工单评论
5. 新增 `issue-close-on-merge.yml`
6. 观察 1 周，确认无状态漂移后归档

---

## 六、验收标准

- [ ] 无工单号的代码 PR 被 CI 拦截（红）
- [ ] 纯文档 PR 不受 G1 影响
- [ ] pytest/ruff 结果自动出现在对应工单评论
- [ ] 治理/缺陷/功能 PR 合并后工单自动 Closed 并附 PR 链接
- [ ] incident 事故单合并后仍保持 Open，不自动关闭
- [ ] 全程无 token 硬编码、无日志泄露
