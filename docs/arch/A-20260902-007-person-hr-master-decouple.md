# 人力资源主表与别名层解耦

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context（为什么做）

当前 `persons` 表被"污染"：261 条记录里只有 42 条是真正的人力资源人员（有岗位/专项），其余 219 条是 `person_extractor` 扫业务表（退费单/工单/钱包等）时，**每碰到一个新人名就自动建一个 `is_confirmed=False` 的空壳 Person** 产生的。每个空壳只挂着自己那 1 个别名、0 条任职记录。

这违背了用户的模型：**`persons` 应当是人力资源主表（只含真人），`person_aliases` 是跨系统别名关联层**。别名只是把业务表里的名字关联到 HR 人员的手段，不该反过来凭空造 person。当前后果：Persons.vue 默认"待确认"tab 把 42 个 HR 真人和 219 个空壳混在一起，看不出谁是真员工；空壳还把本该进入归并队列的别名（如"企微-洪佳"挂在空壳 person 8 上）藏了起来，反而阻碍归并。

目标：让 `persons` 只由 HR 导入或管理员手工创建（`is_confirmed=True`）；`person_extractor` 只收获别名（`person_id=NULL` 待归并）；清理已有 219 个空壳，把它们的别名归还到待归并队列。

## 影响面核查结论（已确认安全）

- 业务表（refunds/service_tickets/wallet_withdrawals/cs_records/external_complaints/card_complaints）**无 `person_id` FK**，关联纯靠 alias 字符串查表 → 改 person_extractor 不破坏任何关联。
- 分析引擎 `analysis_engine.py` **完全不引用 Person/PersonAlias**（虚拟人员维度是未来项）→ 不受影响。
- 跨表关联服务 `matcher.py` 只做 refund↔ticket↔wallet↔merchant 匹配，**不碰 person** → 不受影响。
- `extract_person_aliases` 仅两处调用：`persons.py:194`（refresh-aliases 端点，正确模块）和 `uploads.py:244`（**已坏**：import 写成 `app.services.person_extraction`/`app.services.matching`，模块不存在，被 try/except 静默吞掉）。
- Person 创建点共 4 处（见下"代码改动"）。
- 空壳特征：`position IS NULL AND is_confirmed=false AND is_active=true`，每壳 1 别名、0 任职（已核实 `stubs_with_assignment=0`），删除零数据损失。

## 代码改动

### 1. backend/app/services/person_extractor.py — 停止造 person（核心）
`extract_person_aliases` 中非系统别名分支（约 L88-L103）：删除"建 Person + 建 alias 挂上去"逻辑，改为**只建 `PersonAlias(person_id=None, ...)`**（待归并）。系统别名分支（L77-L88，is_system=True 不建 person）保持不变。stats 去掉 `new_persons` 计数或保留为 0。

### 2. backend/app/services/staff_import.py — HR 导入标记已确认
- L109 新建 Person 分支：`is_confirmed=False` → `is_confirmed=True`（HR 是权威来源，导入即确认）。
- L114 display_name 单值命中分支（用已有 person）：补 `person.is_confirmed = True`（HR 认领即确认）。
- 这样 HR 导入的 42 人落到"已确认"tab，构成 HR 主表视图。

### 3. backend/app/api/v1/persons.py — split 改为解绑
`split_aliases`（L173-L189）：当前为每个拆出别名建新空壳 person（`is_confirmed=False`）。改为**只把别名 `person_id` 置 NULL**（归还待归并队列），不建 person。语义更对：拆出 = "这个别名不属于此人"，回待归并队列等人工/HR 归并；确需独立成 person 走 merge_new。

### 4. backend/app/api/v1/persons.py — 待归并别名列表只返回未关联
`list_unmerged_aliases`（L53-L62）：当前返回所有非系统别名（357 条，含已关联）。加 `.where(PersonAlias.person_id.is_(None))`，只返回真正待归并的（清理后约 221 条），作为归并工作队列。

### 5. backend/app/api/v1/uploads.py — 修复静默失效的 import（顺带）
`_run_post_import_tasks`（L235-L236）：`from app.services.matching import match_all` → `from app.services.matcher import match_all`；`from app.services.person_extraction import extract_person_aliases` → `from app.services.person_extractor import extract_person_aliases`。当前两行 ImportError 被 L248 try/except 吞掉，导致上传后关联匹配和别名提取都没真正执行。修复后上传即自动归并别名（到 person_id=NULL）。

### 6. web/src/views/system/Persons.vue — 默认 tab 切到"已确认"
L413 `filter.status` 默认值 `'unconfirmed'` → `'confirmed'`。清理后"已确认"= HR 主表（42 真人），作为落地首屏；"待确认"tab 仅留极少数真正未确认的；待归并别名面板（L27 unmergedAliases）显示未关联别名队列。

## 数据清理迁移（Alembic，新建 revision）

文件：`backend/alembic/versions/v1a2b3c4d5e6_demote_stub_persons.py`（down_revision = `u0d1e2f3a4b5`）

SQL（顺序敏感）：
```sql
-- 1. HR 人员(position 非空=有任职=真人)标记已确认,使其进入"已确认"主表视图
UPDATE persons SET is_confirmed = true WHERE position IS NOT NULL AND is_confirmed = false;
-- 2. 空壳 person 的别名解绑到待归并(person_id=NULL)
UPDATE person_aliases SET person_id = NULL
WHERE person_id IN (SELECT id FROM persons WHERE position IS NULL AND is_confirmed = false AND is_active = true);
-- 3. 删除空壳 person(staff_assignments FK 为 CASCADE,但空壳 0 任职,无连锁删除)
DELETE FROM persons WHERE position IS NULL AND is_confirmed = false AND is_active = true;
```
保留：1 个 is_confirmed=true 无岗位的（手工确认真人）、1 个 inactive。
downgrade：不可逆（数据清理），标注 `raise NotImplementedError`。

## 验证

1. `docker compose build backend && up -d backend`，确认迁移 `v1a2b3c4d5e6` 应用成功。
2. SQL 核对：`SELECT count(*) FROM persons` ≈ 44（42 HR + 1 确认 + 1 停用）；`SELECT count(*) FROM persons WHERE position IS NULL AND is_confirmed=false` = 0；`SELECT count(*) FROM person_aliases WHERE person_id IS NULL` ≈ 221。
3. Persons.vue 首屏默认"已确认"tab 显示 42 个 HR 人员（带岗位/专项）；"待归并别名"面板显示约 221 条未关联别名（含"企微-洪佳"等待归并到洪佳）。
4. 触发 `POST /persons/refresh-aliases`：确认 stats 不再增加 new_persons，只增加 new_aliases（person_id=NULL）。
5. 上传一个业务文件，确认 `_run_post_import_tasks` 不再报 ImportError（看 backend 日志无 `后台任务执行失败`），且新名字进待归并队列而非 persons 表。
6. 回归 HR 快照导入：重跑 `POST /persons/import-snapshot`，确认仍幂等（0 insert/update），且匹配到的 person 被设 `is_confirmed=true`。
7. 点查询 `GET /persons/{id}/assignment-at` 仍正常返回历史任职。
