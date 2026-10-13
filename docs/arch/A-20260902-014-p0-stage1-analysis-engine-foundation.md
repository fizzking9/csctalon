# P0 阶段 1：分析引擎地基 + 人员统一 + 导入增强

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context（为什么做）

v0.2 审计确认系统核心从「4 个固定看板」转向「自助式数据分析引擎」（可视化视图构建器 + 分表聚合后相加 + 条件规则引擎分类）。引擎的前提是**数据源元数据注册**——让新表加入零代码、字段语义可被引擎识别。

同时审计确认了几项关键改进：
- **导入流程**要从"直接 upsert"升级为"预览-复核-确认"工作流（B8/B9/B12），当前 [uploads.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/v1/uploads.py) 只有前 10 行 sample 预览，缺**逐行 diff**（已有折叠/新增高亮/异常红色）
- **人员**要统一（C13）：各表 operator/auditor/handler 当作别名，映射到统一 person；sys/system 标记为系统操作
- **原始文件**要按日期归档（B8），当前 [data_parser.py:save_upload_file](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/data_parser.py#L338) 直接覆盖同名
- **alembic** 从未管理过 schema（[versions/](file:///Users/tanzk/dev-tmp/csctalon/backend/alembic/versions) 空，靠 [main.py:23](file:///Users/tanzk/dev-tmp/csctalon/backend/app/main.py#L23) 的 `create_all`），需切干净

本阶段交付一个**端到端可用切片**：注册数据源 → 导入时看逐行 diff → 文件自动归档 → 人员别名自动提取并归并。引擎执行器本身（analysis_views + engine.py + ViewBuilder）留待阶段 2。

## 已确认决策

| # | 决策 | 选择 |
|---|------|------|
| 1 | 字段元数据来源 | SQLAlchemy 模型反射 + 集中化 Override 混合（字段集永远与模型对齐，语义人工 override） |
| 2 | dev 库切换 alembic | DROP SCHEMA 重建（开发阶段无历史包袱） |
| 3 | wallet diff 策略 | 无主键，全部标 new（避免错误"已存在"判断） |
| 4 | 人员别名归一化 | 兼容「企微-」与「企微·」两种分隔符（样本中两种并存） |
| 5 | sys/system 处理 | 入库 person_aliases 标 is_system=true（审计可追溯，UI 过滤） |
| 6 | 自动建 person | 是，is_confirmed=false（让管理员看见所有候选，不自动归并） |
| 7 | diff 行数上限 | 5w 行硬截断，超出统一标 new |
| 8 | 别名提取触发 | import 成功后自动 + 手动 API（不在启动时跑） |
| 9 | alembic 与 create_all | 去掉 create_all，统一走 alembic upgrade head |

## 数据模型（4 张新表 + 1 字段）

### DataSource（数据源注册）
新建 [backend/app/models/data_source.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/models/data_source.py)
- `code` (unique, 如 refund/wallet/ticket/merchant_refund)
- `table_name` / `name_cn` / `source_type`(business/system)
- `time_field` / `sn_field` / `pk_field` / `pk_strategy`(primary_key/none)
- `status`(active/inactive) / `description`
- 复用 IDMixin + TimestampMixin

### DataSourceField（字段元数据）
同文件
- `source_id` FK→data_sources (CASCADE)
- `field_name` / `field_name_cn` / `data_type`(string/numeric/timestamp/bool/jsonb)
- `semantic_role`(dimension/measure/amount/time/person/sn/category) — String(32) 不用 SA Enum
- `agg_func`(sum/count/avg，仅 measure/amount)
- `is_filterable` / `is_pk` / `enum_values`(JSONB) / `sort_order`
- UniqueConstraint(source_id, field_name)

### Person（统一人员）
新建 [backend/app/models/person.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/models/person.py)
- `display_name` / `qw_user_id` / `qw_name` / `role` / `group_name`
- `is_active` / `is_confirmed`(人工确认归并) / `remark` / `user_id` FK→users(可选)

### PersonAlias（人员别名）
同文件
- `person_id` FK→persons (SET NULL，可空=待归并)
- `alias`(unique) / `alias_normalized`(去企微-前缀+小写，用于归并建议)
- `source_table` / `source_field` / `is_system` / `last_seen_at`

### UploadRecord 加字段
修改 [backend/app/models/upload.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/models/upload.py)
- 新增 `stored_path: str | None`（归档相对路径 yyyy-MM/filename）

## 实现步骤（按依赖排序）

### 后端（步骤 1-19）

1. **新模型**：[data_source.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/models/data_source.py)（DataSource + DataSourceField + SemanticRole 常量）
2. **新模型**：[person.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/models/person.py)（Person + PersonAlias）
3. **改模型**：[upload.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/models/upload.py) 加 stored_path
4. **注册模型**：[models/__init__.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/models/__init__.py) 加 import
5. **alembic init 迁移**：drop 库后 `alembic revision --autogenerate -m "init schema"`，review 产物确认含全部表 + 4 新表 + stored_path 列
6. **改 main.py**：lifespan 去掉 `Base.metadata.create_all`，改为 `_run_migrations_on_startup()` 调 `alembic.command.upgrade(cfg, "head")`
7. **数据源注册服务**：新建 [services/data_source_registry.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services)
   - `DATA_SOURCE_SEEDS`：4 张业务表种子（code/model/name_cn/time_field/sn_field/pk_field/pk_strategy）
   - `FIELD_OVERRIDES`：约 45 条字段语义 override（refund_amount=amount+sum、operator=person、status=category+enum 等）
   - `ensure_data_sources_registered(db)`：反射 `inspect(model).columns` 获取字段集 + data_type，套用 override，幂等 upsert
8. **schemas**：新建 [schemas/data_source.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/schemas) + [schemas/person.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/schemas)
9. **数据源管理 API**：新建 [api/v1/data_sources.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/v1)
   - GET /data-sources、GET /data-sources/{code}、GET /data-sources/{code}/fields
   - PATCH /data-sources/{code}、PATCH /data-sources/fields/{field_id}
   - POST /data-sources/refresh（触发反射重建）
10. **人员别名提取服务**：新建 [services/person_extractor.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services)
    - `PERSON_FIELD_SOURCES`：5 个字段源（refund.operator/auditor、ticket.handler/registrant、wallet.operator）
    - `_normalize_alias`：兼容「企微-」「企微·」前缀剥离 + lower
    - `extract_person_aliases(db)`：GROUP BY 一次拉 distinct 值，sys/system 入库标 is_system，其余自动建待确认 person；幂等（已存在只更新 last_seen_at）
11. **人员归并 API**：新建 [api/v1/persons.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/v1)
    - GET /persons（分页，status 筛选）、GET /persons/{id}、PUT /persons/{id}
    - POST /persons/{id}/merge（body: alias_ids，原 person 软删除）、POST /persons/merge（新建并归并）
    - POST /persons/{id}/split（拆出别名各自建 person）
    - GET /persons/unmerged-aliases、POST /persons/refresh-aliases、DELETE /persons/{id}（停用）
12. **注册路由**：改 [api/v1/router.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/v1/router.py) 挂载 data_sources + persons
13. **upload schemas 加 stored_path**：改 [schemas/upload.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/schemas)（ParsePreview + ImportRequest）
14. **文件归档改造**：改 [services/data_parser.py:save_upload_file](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/data_parser.py#L338)
    - 返回 `(Path, str)` 即（绝对路径, 相对路径 stored_path）
    - 落到 `uploads/yyyy-MM/原文件名`，同名加时间戳后缀
15. **uploads.py 改造**：改 [api/v1/uploads.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/v1/uploads.py)
    - /parse：调 save_upload_file 拿 stored_path，preview 返回里加 stored_path
    - /import：优先用 stored_path 找文件；UploadRecord 存 stored_path；import 成功后调 `extract_person_aliases(db)`
16. **diff 引擎**：新建 [services/diff_preview.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services)
    - `compute_diff(db, df, data_type, mapping, ds)`：按 ds.pk_strategy 分支
    - pk_strategy=none → 全标 new
    - primary_key → 批量 SELECT ... WHERE pk IN (...) 拉现有行，逐行比较（排除 created_at/updated_at/raw_extra/id；None vs "" 视为一致）
    - 返回 {strategy, summary{total/existing/new/abnormal}, truncated, existing_sample, new_rows, abnormal_rows}
    - 5w 行截断；new 明细限 200、abnormal 限 500、existing_sample 限 50
17. **diff 预览端点**：改 [api/v1/uploads.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/v1/uploads.py) 加 `POST /uploads/preview-diff`
18. **种子脚本**（可选）：新建 [scripts/seed_data_sources.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app) 一次性注册
19. **测试**：新建 [tests/test_diff_preview.py](file:///Users/tanzk/dev-tmp/csctalon/backend/tests) + [tests/test_data_source_registry.py](file:///Users/tanzk/dev-tmp/csctalon/backend/tests)

### 前端（步骤 20-26）

20. **api 模块**：新建 [api/data-source.js](file:///Users/tanzk/dev-tmp/csctalon/web/src/api) + [api/person.js](file:///Users/tanzk/dev-tmp/csctalon/web/src/api)；改 [api/upload.js](file:///Users/tanzk/dev-tmp/csctalon/web/src/api/upload.js) 加 `previewDiff(payload)`
21. **Upload.vue 加 diff 步骤**：改 [views/data/Upload.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/views/data/Upload.vue)
    - 4 步流程：选类型文件 → 预览映射 → **diff 复核** → 确认导入
    - diff 步骤：三卡片（existing 灰折叠/new 绿高亮/abnormal 红）+ existing_sample 折叠表 + new_rows 表 + abnormal_rows 表（展开显示字段级 old vs new）
22. **数据源管理页**：新建 [views/system/DataSources.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/views/system)（列表 + 字段查看 + refresh 按钮）
23. **人员管理页**：新建 [views/system/Persons.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/views/system)（待确认/已确认分区 + 归并/拆分 UI + 别名列表）
24. **路由**：改 [router/index.js](file:///Users/tanzk/dev-tmp/csctalon/web/src/router/index.js) 加 /system/data-sources、/system/persons
25. **菜单**：改 [layouts/MainLayout.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/layouts/MainLayout.vue) 系统管理下加子菜单
26. **样式**：diff 表格的红/绿行高亮（用 ElementPlus row-class-name）

## 关键复用点（不要重写）

- [services/upsert.py:apply_mapping](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/upsert.py#L119) + `_to_float/_to_datetime/_to_str/_clean_value` — diff 引擎复用同一套字段转换，保证 diff 与实际 upsert 口径一致
- [services/data_parser.py:read_csv_auto_encoding](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/data_parser.py#L166) — CSV 换行拆行已解决，diff 路径直接用 load_dataframe
- [services/analytics.py:cash_out_trend](file:///Users/tanzk/dev-tmp/csctalon/backend/app/services/analytics.py#L110) — 「分表聚合后相加」的范例，阶段 2 引擎执行器照此泛化
- [schemas/common.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/schemas/common.py) 的 `R` + `PageBase` — 所有新 API 统一响应
- [api/deps.py](file:///Users/tanzk/dev-tmp/csctalon/backend/app/api/deps.py) 的 `CurrentUser`/`DBSession` — 新 API 复用
- [components/BaseChart.vue](file:///Users/tanzk/dev-tmp/csctalon/web/src/components/BaseChart.vue) — 阶段 2 视图渲染器复用
- [stores/auth.js](file:///Users/tanzk/dev-tmp/csctalon/web/src/stores/auth.js) 的 Pinia setup 风格 — 新 store 模板

## 验证方案（端到端）

### 1. 启动与迁移
```bash
# drop 重建
docker compose exec db psql -U csctalon -d csctalon -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
cd backend && alembic upgrade head   # 应建出全部表（含 4 新表 + stored_path 列）
uvicorn app.main:app --reload         # 启动日志无 create_all
# 注册数据源
python -c "from app.core.database import SessionLocal; from app.services.data_source_registry import ensure_data_sources_registered; db=SessionLocal(); print(ensure_data_sources_registered(db)); db.close()"
# 期望: sources=4, fields≈80
```

### 2. 数据源 API
```bash
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/data-sources
# 期望: 4 条数据源
curl localhost:8000/api/v1/data-sources/refund/fields
# 期望: operator=person, refund_amount=amount+sum, status=category+enum[申请中,已退款,无需退款]
```

### 3. 文件归档 + diff 流程（核心）
- 上传 `01样本数据/退费单0601~0721.csv.csv`：步骤3 diff 应全部 new（库空）；导入后 `ls backend/uploads/2026-07/` 见原文件
- **重新上传同一文件**：diff 应 existing=总数, new=0, abnormal=0
- **改某行 refund_amount 后上传**：abnormal=1, diffs 显示 old vs new
- **追加 5 行新 RF 号后上传**：new=5
- 上传 `钱包提现操作0601~0721.csv`：strategy=no_pk, 全标 new, note 提示无主键
- 上传 `服务退款工单0601~0721.csv`：按 ticket_no 分类正确

### 4. 人员别名
- 导入 refund 后 `POST /persons/refresh-aliases`：person_aliases 有所有 operator distinct（除 sys/system），每个 alias 对应 is_confirmed=false 的 person
- `SELECT * FROM person_aliases WHERE is_system=true` 应有 sys/system 2 行
- 归并测试：`POST /persons/{id}/merge` body `{alias_ids:[5]}` → alias.person_id 更新，原 person is_active=false
- 拆分测试：split 后 alias 拆出新建 person

### 5. 异常场景
- CSV 字段内换行（退费单样本已知含）：diff 不报"主键缺失"
- GBK 编码 CSV：detected_encoding=gbk，diff 正常
- 超 5w 行文件：truncated=true
- 未注册数据源（如 cs_record）：diff 返回 400

### 6. 前端
- /system/data-sources 页可见 4 数据源 + 字段表
- /system/persons 页可见待确认人员 + 归并/拆分可用
- /data/upload 4 步流程跑通，diff 卡片显示正确

## 不在本阶段范围

- 分析引擎执行器（analysis_views + classification_rules 模型 + engine.py + ViewBuilder.vue）→ 阶段 2
- 智能助手处理记录数据源（字段已确认 13 列，含"企微·/企微-"两种操作人格式）→ 阶段 2/P2
- 排班报表（复杂多级表头，需特殊解析）→ P3
- 通用事项模块统一、发布页、关联匹配弱化 → 阶段 3
