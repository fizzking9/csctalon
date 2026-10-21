---
name: talon-dashboard-fix-release
description: csctalon 仓库「看板/界面口径小修」标准闭环：先连生产核实数据口径 → 建工单 → 界面三层落地修改 → 标准发布到 evox2 → 生产实测 → 凭证关单。当用户报看板数字/占比/tooltip/口径展示问题，或要求把前端/后端小修改发布上线并关闭对应工单时使用。不用于新功能开发或架构变更。
---

# csctalon 看板小修 → 标准发布 → 凭证关单

适用：talon 仓（`/Users/tanzk/dev-tmp/csctalon`）的展示层/口径类小修（tooltip、占比口径、桶名、文案、ⓘ 说明、小幅后端配合）。核心纪律来自仓库 `AGENTS.md`：**工单先行、提交带 #issue号、关闭必须有可复现凭证**。

## 环境速查

| 项 | 值 |
|---|---|
| 仓库 | `/Users/tanzk/dev-tmp/csctalon`，发布分支 **prod-local**（非 main） |
| Gitea | `http://10.147.17.41:3000`，repo `tanzk/csctalon`，token 在 `~/.netrc`（可 `git credential fill` 兜底） |
| 生产机 | `ssh -o BatchMode=yes node-evox-zt`（免密 sudo），容器 `csctalon-backend` / `csctalon-web` / `csctalon-redis` |
| 升级入口（唯一标准方式） | `sudo nohup bash /opt/csctalon/current/deploy/upgrade.sh --git --branch prod-local > /tmp/csc_upgrade_<issue>.log 2>&1 &`（自动备份+健康检查+失败回滚，**禁止**手拼 docker 命令改部署树） |
| Redis | 有密码，**别直接 redis-cli**，用 backend 容器内 `from app.core.redis_client import cache_invalidate_recent_days` |
| 后端 venv | `backend/.venv/bin/python`、`backend/.venv/bin/ruff`（不是仓库根 .venv）；pytest 在 `backend/` 下跑 |
| web 资产验证 | 容器内 `grep -l '<关键词>' /usr/share/nginx/html/assets/*.js` |
| 已知预存测试失败（与展示小修无关） | `test_analytics_core_smoke.py::TestRrViewsDbSmoke::test_handler_returns_dict_on_empty_db[preset_complaint_subtype_rootcause_heatmap]`（rr_views.py CompoundSelect.c，SQLAlchemy 兼容问题）；凭证中注明即可，不要试图修它 |

## 标准流程（每步不可跳）

### 1. 先核实数据，再动界面
用户报的数字问题，**先连生产容器实跑服务函数核对**（只读，不动数据）：

```bash
ssh -o BatchMode=yes node-evox-zt "docker exec csctalon-backend python -c \"
from app.core.database import SessionLocal
from app.services.analytics import refund_decision_overview   # 按需换函数
db = SessionLocal()
try:
    ov = refund_decision_overview(db, start='2026-06-01', end='2026-09-30', grain='day')
    print(ov['sale_days_split'])   # 或其它字段
finally:
    db.close()
\""
```

判断结论二选一：① 真的算错 → 走 bug 修复（后端测试必须补）；② **数字没错、是口径没标清** → 展示缺陷，按第 2 步与用户确认修法。常见歧义模式：「占比」没标是单数还是金额口径（#296）；tooltip 过于简陋与另一侧不对齐（#297）。

### 2. 修法让用户拍板（AskUserQuestion）
给出 2-3 个选项（如：双口径并列显示 / 仅标注口径 / 改图维度），**推荐项放第一个并标注（推荐）**。不猜实现。

### 3. 建工单（先建再动手）
```bash
python3 ~/.trae-cn/skills/talon-dashboard-fix-release/scripts/gitea_flow.py \
  create --title "xxx" --body-file /tmp/issue.md
```
正文固定结构：`## 问题`（含生产实测数字佐证）→ `## 修复`（用户拍板的方案）→ `## 验收标准`（A/B/C 各一条，格式 `- [ ] A: ...`，后续凭证逐条对应）。

### 4. 界面改动三层落地（AGENTS 界面纪律）
- ① 代码层：区块/函数头部 `设计说明` 注释块（含**取舍**段：不做什么及原因），本次改动点加 `#issue号:` 行注释；
- ② 页面层：ⓘ tooltip / 设计说明抽屉同步口径（写明来源接口、口径判据、时点、「空」的含义、下钻、取舍）；
- ③ 若动用 `docs/standards/review-checklist.md` 相关项，逐项过。

### 5. 验证
- 纯前端：`cd web && npm run build`（≈9s，看 ✓ built）。
- 动了后端：`backend/` 下 `../.venv 之外的 .venv` → 用 `backend/.venv/bin/python -m pytest tests/test_<相关>.py -q` + `backend/.venv/bin/ruff check <改动文件>`；金额/桶等口径函数改动必须补/改测试用例（桶边界用**切点内侧安全值**，如 14/17/60/200/400 天，防时区抖动）。
- 有生产数据可对的话，回生产实跑一次核对（如六桶占比合计 ≈100%，四舍五入 99.9/100.0 均正常）。

### 6. 提交与发布
```bash
git add <具体文件> && git commit -m "#<issue> 一句话说明"   # 提交信息必须带 #issue号
git push origin prod-local
ssh -o BatchMode=yes node-evox-zt "sudo nohup bash /opt/csctalon/current/deploy/upgrade.sh --git --branch prod-local > /tmp/csc_upgrade_<issue>.log 2>&1 & echo STARTED"
```
约 2-3 分钟后收尾（sleep 150-160 再查，或轮询一次 `pgrep -f upgrade.sh`）：
```bash
ssh -o BatchMode=yes node-evox-zt "tail -5 /tmp/csc_upgrade_<issue>.log"   # 看「✔ 升级完成: git-旧 → git-新」与备份包名
ssh -o BatchMode=yes node-evox-zt "curl -sf -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:8000/health; \
  docker ps --filter name=csctalon-web --format '{{.Status}}'; \
  docker exec csctalon-web sh -lc \"grep -l '<本次新文案关键词>' /usr/share/nginx/html/assets/*.js\""
```
升级失败的判断：日志出现回滚字样 → 停手排查，禁止重试硬来。

### 7. 缓存（按需）
后端**数据形状变了**（桶结构/字段名/口径）→ 发布后失效相关 pattern，backend 容器内：
```bash
docker exec csctalon-backend python -c "
from app.core.redis_client import cache_invalidate_recent_days
for pat in ('refund:do:*', 'rw:*', 'complaint:backlog:*', 'complaint:overview:*', 'complaint:device-dims:*', 'complaint:extended-dims:*'):
    cache_invalidate_recent_days(pat)"
```
纯文案/tooltip 展示层修改（JSON 契约没变）→ 不需要。

### 8. 凭证关单（缺这步 = 白干）
写 evidence 文件 `/tmp/evidence_<issue>.md`，格式固定：

```markdown
## 关闭凭证核验 (YYYY-MM-DD)

| 验收标准 | 核验方式（可复现） | 结果 |
|---|---|---|
| A: <复述验收标准A> | <测试用例名 / commit 内行号链接 / 实测命令+关键输出> | 通过 |
| B: ... | ... | 通过 |
| C: ... | ... | 通过（1 预存失败另案）← 仅当有预存失败 |

**发布凭证**
- commit `<sha>`（prod-local，已 push）；改动文件数与性质（纯前端/含后端）。
- evox2 标准升级 `upgrade.sh --git --branch prod-local`：备份 `<备份包名.tar.gz>`，git-<旧> → git-<新>，<哪些服务重建>，healthy，health=200，无回滚。
- 缓存：<失效了哪些 pattern / 纯展示层无需失效>。
- <无表结构变更、无数据迁移、无金额逻辑改动，按实际写>。
```

铁律：每条核验方式必须**可复现**（用例名/命令/sha），禁止「已测试通过」这类空话；正文 `- [ ]` 必须勾成 `- [x]`；每张工单各写各的凭证，禁止静默关闭。

```bash
python3 ~/.trae-cn/skills/talon-dashboard-fix-release/scripts/gitea_flow.py \
  close --issue <N> --evidence-file /tmp/evidence_<N>.md
```
（脚本自动：勾选正文全部 `- [ ]` → PATCH body → POST 凭证评论 → PATCH state=closed。若确属降级接受，在 evidence 里写 `结论: 以 <理由> 降级验收`，并加 `--no-tick` 保留未达成项不勾。）

### 9. 收尾汇报给用户
给出：上线版本号、悬停/刷新即可看到的效果描述、关键实测数字表、工单链接与关闭状态。提醒用户强刷页面（新前端包 + 可能的 60s 接口缓存）。

## 反模式（本会话踩过/防过的坑）
- 不核实就答「应该是金额占比」→ 必须先生产实算，30.7% vs 34.9% 那次单数/金额两口径都各自正确，错的只是没标口径。
- 概览页 `ov*Option` 系列是**模板未引用的死代码**，别顺手清理（超工单范围）；新需求别接到它上面。
- 前端桶→参数映射（如 `AGE_PARAM_BY_NAME`/`SALE_AGE_PARAM`）必须与后端 `_core._AGE_PARAM_TO_BUCKET` 1:1，后端为单一来源，改桶时两边同步。
- 投诉侧无金额维度（#252 取舍：工单金额字段稀疏），别擅自给投诉图加金额占比。
- Top8 截断的条形图，占比分母必须是**全量**而不是图上 8 条之和（分母在 slice 前求和）。
