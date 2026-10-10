# 退款现金流量视图体系重构实施计划

**状态**：LOCKED（v1.0）
**锁定日期**：20260902
**锁定脚本**：scripts/lock-arch-docs.sh


## Context

用户要对"退款现金流量"视图部分做整体重新梳理,逐个视图处理。需求覆盖四类数据源(线上退款/用户提现/闪猫占位/商户号退款)、五个视图维度(整体趋势/操作人/日记录/池子水位/提现SN时序),以及设备/人员审计时序呈现。

经代码梳理与用户确认,存在以下关键缺口需要补齐才能支撑视图配置:
1. **引擎层**:无季度/年时间粒度、无堆积图通用化(仅 Cash.vue 硬编码)、无累计差额(池子水位)
2. **跨表关联**:引擎禁止 JOIN,用户决策走 matcher 预计算物化到 refund_wide 宽表
3. **退款分类**:用户决策"外部投诉 > 退款原因 > 审核人"互斥优先级,需去重(一个 SN 可能命中多类)
4. **审计页**:无时序流呈现,仅有 devices.py 的卡片式聚合(persons.py 只有计数无明细)

用户明确决策:本期不做次日提现预测(仅池子水位监控);闪猫退款占位暂留;审计走"时序流+明细表双视图,字段精简"。

预期成果:5 个预设视图 + 设备/人员审计页 + 引擎三项能力补齐 + refund_wide 物化宽表 + 3 条分类规则 seed。

## 关键架构决策(用户已拍板)

| 决策点 | 方案 |
|---|---|
| 退款分类 | 3 条 ClassificationRule,互斥优先级(数组顺序=CASE WHEN 顺序),default_label="普通退款" |
| 跨表 JOIN | matcher 预计算物化到 refund_wide 表,引擎查宽表,不动引擎 JOIN 限制 |
| 次日预测 | 本期不做,仅池子水位监控(cumulative 累计差额) |
| 审计呈现 | 时序流(el-timeline)+ 明细表(el-table)双视图,字段精简 |
| 堆积图 | segment 级 stack_group,各 segment 用不同 measure alias 避免被 add-merge 抹平 |
| 累计差额 | Python 后处理(非 SQL 窗口函数),镜像 ratios 模式 |

## 阶段划分(按依赖顺序)

| 阶段 | 主题 | 依赖 | 阻塞 |
|---|---|---|---|
| 1 | 引擎能力补齐(时间粒度+stack_group+cumulative) | 无 | 阻塞阶段 4 全部视图 |
| 2 | refund_wide 物化宽表 + matcher 钩子 | 无(可与阶段1并行) | 阻塞阶段 3、视图 3/5 |
| 3 | 分类规则 seed + 视图引用解析 | 阶段 2 | 阻塞视图 3 |
| 4 | 5 个预设视图(按 1→2→4→3→5 顺序) | 阶段 1,阶段 2(部分) | 业务交付主体 |
| 5 | 审计时序页(DeviceAudit/PersonAudit) | 无(可并行) | 独立交付 |

---

## 阶段 1:引擎能力补齐

三块子任务相互独立,可并行。核心改动集中在 `backend/app/services/analysis_engine.py`。

### 1A. 时间粒度新增 quarter / year

**改动**:
- `backend/app/services/analysis_engine.py:384-398`(`_apply_time_grain`):在 month 分支后插 quarter(`func.to_char(col, 'YYYY-"Q"Q')` → `2026-Q3`)和 year(`func.to_char(col, 'YYYY')`)分支
- `web/src/views/analytics/ViewBuilder.vue:75-81`:时间粒度下拉追加"按季""按年"两个 el-option

**要点**:time_grain 是 segment JSONB 纯字符串,无 Enum 约束,无需改 schema/model/DB。`to_char` 字典序带年份前缀天然有序。季度格式务必带双引号包裹 Q(`'YYYY-"Q"Q'`),否则 PostgreSQL 把 Q 当占位符。

**验证**:ViewBuilder 任意 line 视图切季度/年度预览,x_axis 应为 `2026-Q3`/`2026` 格式且有序。

### 1B. stack_group 堆积图通用化

**决策**:stack_group 放 **segment 级**(非 measure 级,避免被 `_merge_segment_results:287-290` add-merge 抹平)。各 segment 用**不同 measure alias**("直接退款"/"用户提现"而非都叫"金额"),add-merge 不合并,各自独立 series。

**改动**:
- `backend/app/services/analysis_engine.py:302-349`(`_to_chart_data`):series 构建(`:337-340`)时,通过 alias→seg.stack_group 反查,给 series 加 `stack` 字段
- `backend/app/services/analysis_engine.py`(`_extract_measure_keys` 后):新增辅助函数 `_extract_stack_group_map(segments) -> {measure_alias: stack_group}`
- `web/src/components/ViewItemCard.vue:141-146`:`.map` 加 `...(s.stack ? {stack: s.stack} : {})`
- `web/src/views/analytics/ViewRender.vue:148-153`:同样加 stack 透传
- `web/src/views/analytics/ViewBuilder.vue:348-359`(`createEmptySegment`):加 `stack_group: null` 字段
- `web/src/views/analytics/ViewBuilder.vue:68-72`(label 输入框行):segment 配置 UI 加"堆积组"输入框

**要点**:schema/model 无需改(segments 是 `list[dict[str, Any]]`,新键自动透穿)。

**验证**:ViewBuilder 新建 3 segment 视图(refund/wallet/merchant_refund,不同 alias,同 stack_group),bar 图应渲染堆积柱状。stack_group 留空的 segment 作为独立 overlay(保留 Cash.vue"真实现金流"那根的语义)。

**⚠ 风险**:`_merge_segment_results:263` 只从第一个 segment 推断 dim_keys,其他 segment 维度 alias 不一致会**静默丢数据**。需在该处加 WARNING 日志检测不一致;ViewBuilder UI 强制提示"多 segment 维度 alias 必须一致"。

### 1C. 累计差额(池子水位,Python 后处理)

**决策**:镜像 ratios 模式,merged 行已按时间排序,Python running sum。不用 SQL 窗口函数(打破"哑 GROUP BY 执行器"设计,且聚合后小结果集 Python 累加零压力)。

**改动**(镜像 ratios,涉及 7 文件):
- `backend/app/schemas/analysis_view.py:55-66`:新增 `CumulativeItem`(alias/inflow/outflow)
- `backend/app/schemas/analysis_view.py:80,98,110,123`:Out/Create/Update/InlineConfig 各加 `cumulative` 字段
- `backend/app/schemas/analysis_view.py:133-144`(`to_view_config`):塞 `cumulative` 进 config dict
- `backend/app/models/analysis_view.py:55-57`:新增 `cumulative: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)` — **需 Alembic 迁移**
- `backend/app/api/v1/analysis_views.py:83`(`create_view`):传 `cumulative=payload.cumulative`(update_view 走 model_dump 通用赋值,无需改)
- `backend/app/services/analysis_engine.py:60-66`(`execute_view`):读 `view.cumulative` 塞 config
- `backend/app/services/analysis_engine.py:84-95`(ratios 块后):新增 cumulative 块,running sum `inflow - outflow`,保留 2 位小数。**注意**:cumulative 块前需按时间 dim 重排 merged(找 role=time 的 dim alias 排序),不依赖 dim_keys[0]
- `backend/app/services/analysis_engine.py:98` + `:302-349`(`_to_chart_data`):新增 cumulative 参数 + cumulative_keys 处理(镜像 ratio_keys),table columns 和 line/bar series 追加
- `web/src/components/ViewItemCard.vue:107-109,201-211`:透传 cumulative
- 新建 Alembic 迁移 `backend/alembic/versions/x3c4d5e6f7a9_add_view_cumulative.py`:`op.add_column("analysis_views", sa.Column("cumulative", postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False))`,参考 `g9c3d5e7f9a0_add_view_ratios.py`

**⚠ 风险**:累计起算点是 time_range 窗口内第一行,非全期历史。文档/UI 需说明"窗口内累计"。

**验证**:2 segment(op_type='订单退费转入'/'用户提现')+ cumulative `{alias:"净水位", inflow:"转入", outflow:"提现"}`,line 图应显示 3 条 series,净水位=累计(转入-提现)。

---

## 阶段 2:refund_wide 物化宽表 + matcher 钩子

### 2A. 模型 + 迁移 + 数据源注册

**改动**:
- 新建 `backend/app/models/refund_wide.py`:`RefundWide` 模型,PK=`refund_no`(FK→refunds.refund_no)
  - **refund 主表冗余字段**(视图 3/5 筛选/分组必需):refund_method/status/refund_reason/operator/auditor/device_sn/refund_amount/created_at_raw
  - ticket 关联:ticket_no/service_item/service_category/ticket_finish_time/ticket_handle_duration/ticket_handler(优先 order_no 命中,否则最新)
  - merchant_refund 关联:wx_refund_no/mr_trade_time/mr_refund_amount/mr_refund_status/mr_fee/merchant_id
  - wallet 关联:wallet_id/wallet_op_time/wallet_op_amount(取最新一条)
  - external_complaint 关联(供分类规则):has_external_complaint(bool)/external_complaint_count(int)/external_complaint_latest_time(datetime)
  - matched_at(datetime)
- `backend/app/models/__init__.py`:import + `__all__`
- 新建 Alembic 迁移 `backend/alembic/versions/y4d5e6f7a8b0_add_refund_wide.py`:`down_revision = "x3c4d5e6f7a9"`(阶段 1C),建 refund_wide 表(index 在 device_sn/ticket_no/wx_refund_no/has_external_complaint);为 data_match_relations 加 match_type 索引 + 唯一约束 `(src_table,src_id,dst_table,dst_id,match_type)` — **加约束前先去重**
- `backend/app/services/analysis_engine.py:29-39`(`_MODEL_REGISTRY`):加 `"refund_wide": RefundWide`
- `backend/app/services/data_source_registry.py:29-128`(`DATA_SOURCE_SEEDS`):加 refund_wide seed(code/name_cn="退费宽表(物化)"/time_field/pk_field/category="现金流"/internal=True)
- `backend/app/services/data_source_registry.py:132-251`(`FIELD_OVERRIDES`):配置 refund_wide 各字段语义角色(has_external_complaint=category, mr_refund_amount=amount/sum, ticket_handler=person, ticket_finish_time=time, service_item/service_category=category, wallet_op_amount=amount/sum, refund_amount=amount/sum 等)

### 2B. 物化构建服务 + 钩子

**改动**:
- 新建 `backend/app/services/wide_table_builder.py`:`build_refund_wide(db, only_refund_nos=None) -> dict`
  - 流程:拉 DataMatchRelation(src_table='refund', status='matched') 按 refund_no 分组 → 批量拉 Refund/ServiceTicket/MerchantRefund/WalletWithdrawal/ExternalComplaint → 组装 RefundWide 行 → `pg_insert(...).on_conflict_do_update(index_elements=['refund_no'], set_=...)` 批量 upsert → 返回 `{built, updated}`
  - 性能:IN 批量查询,不用逐条 SELECT
- `backend/app/services/matcher.py:28-43`(`match_all` 末尾,`return stats` 前):追加 `build_refund_wide(db)` 调用,stats 加 wide_table 字段
- `backend/app/services/bms_sync.py:357`(`sync_all` return 前):**新增 post-sync 钩子**(目前完全没有!):`match_all(only_unmatched=True) + build_refund_wide`,try/except 兜底不阻塞 sync 主流程,用独立 SessionLocal
- `backend/app/api/v1/uploads.py:232-251`(`_run_post_import_tasks`):在 match_all 后追加 `build_refund_wide(db)`
- (可选)`backend/app/api/v1/wide_tables.py`:新建 `POST /wide-tables/refund-wide/rebuild` 端点,便于手动重建验证
- (可选性能优化)`backend/app/services/matcher.py:63-79`(`_add_relation`):改批量 INSERT ON CONFLICT DO NOTHING,消除 4.2万×6=25万次 SELECT

**验证**:
1. `alembic upgrade head` 成功(若唯一约束失败先去重)
2. `POST /wide-tables/refund-wide/rebuild` 返回 built/updated 数
3. `SELECT count(*) FROM refund_wide` vs `SELECT count(DISTINCT src_id) FROM data_match_relations WHERE src_table='refund' AND status='matched'` 应相等
4. 抽查 10 条 refund_wide 人工对齐 4 表数据
5. 触发 bms_sync,确认 post-sync 钩子日志打印 wide_table stats
6. 前端 ViewBuilder 数据源下拉出现"退费宽表(物化)",字段列表正确

**⚠ 风险**:matcher 首跑慢(逐条 SELECT);Alembic 唯一约束加前必须去重;同一 SN 多工单选择规则要明确(优先 order_no 命中,否则最新 finish_time)保证批次稳定。

---

## 阶段 3:分类规则 seed + 预设视图引用解析

**改动**:
- 新建 `backend/app/services/preset_classification_rules_seed.py`:定义 `PRESET_RULES`(3 条 ClassificationRule),实现 `ensure_preset_classification_rules_registered(db) -> {name: rule_id}`
  - 规则1(最高优先级):name="客诉-外部投诉",rules=`[{label:"客诉-外部", match:"all", conditions:[{field:"has_external_complaint", op:"eq", value:true}]}]`(走 refund_wide)
  - 规则2:name="客诉-原因",rules=`[{label:"客诉-原因", match:"any", conditions:[{field:"refund_reason", op:"contains", value:"客诉"}]}]`(关键词占位,待用户细化)
  - 规则3:name="客诉-审核人",rules=`[{label:"客诉-审核人", match:"any", conditions:[{field:"auditor", op:"in", value:[客诉组人员名单]}]}]`(名单占位,待用户提供)
  - default_label="普通退款"
  - **互斥优先级**靠 `classification_rule_ids` 数组顺序(引擎 `_build_classification_case:218-242` 转 CASE WHEN,第一个命中生效)
- `backend/app/main.py:73-106`(`_init_data_sources_and_presets`):在 `ensure_preset_views_registered` **之前**插入 `ensure_preset_classification_rules_registered(db)` 调用(顺序很重要:规则先于视图,视图才能引用规则 id)
- `backend/app/services/preset_views_seed.py:518-554`(`ensure_preset_views_registered`):改造支持 `classification_rule_names`(seed 新字段)解析为 id — 函数开头查 `SELECT id,name FROM classification_rules WHERE name IN (...)` 构建 name→id map,按 names 顺序映射为 id 列表填入 classification_rule_ids

**要点**:ClassificationRule 模型无 code 字段(analysis_view.py:66-93),upsert 靠 name 幂等。rules JSONB 结构是 `[{label, match, conditions:[{field,op,value}]}]`(非 op/field/value/target_label)。op 不支持 subquery,所以规则1必须走 refund_wide.has_external_complaint 物化字段。

**⚠ 风险**:规则字段必须与 source_code 对齐 — 规则1仅适用 refund_wide;建议 refund_wide 冗余 refund_reason/auditor(阶段 2A 已含),让 3 条规则都能在 refund_wide 视图上一起用。规则 upsert 失败时跳过引用规则的视图,不要让视图引用空 id。

**验证**:重启看启动日志"预设分类规则注册完成";`GET /api/v1/analysis-views/classification-rules` 应看到 3 条;二次启动 created=0(幂等);在 refund_wide 视图上引用 3 规则,预览结果应有"客诉-外部/客诉-原因/客诉-审核人/普通退款"四类标签,同时命中时取最高优先级。

---

## 阶段 4:5 个预设视图(按 1→2→4→3→5 顺序逐个上)

通用改动:每个视图在 `backend/app/services/preset_views_seed.py` 的 `PRESET_VIEWS` 数组追加 cfg,重启幂等 upsert。`ensure_preset_views_registered` 已在阶段 3 改造支持 classification_rule_names。

**视图处理顺序与理由**:
1. **视图 1(整体趋势)** — 验证 stack_group + quarter/year,高频视图优先;不依赖 refund_wide
2. **视图 2(退费操作人)** — person 归一化已就位 + quarter/year;不依赖 refund_wide
3. **视图 4(池子水位)** — 依赖 cumulative(1C);不依赖 refund_wide,给 cumulative 充分验证窗口
4. **视图 3(日记录按人员/原因)** — 依赖 refund_wide(阶段2)+ 分类规则(阶段3),最重依赖放后
5. **视图 5(提现SN时序)** — 依赖 refund_wide,价值偏审计非日常高频

### 视图 1:整体趋势(堆积四类金额)
- code=`preset_cash_trend_v2`,chart_type=`bar`(堆积柱状)
- 3 segment(refund 线上退款+排除sys/system / wallet 用户提现 / merchant_refund 商户号退款;闪猫暂占位跳过),各 segment measure alias 不同("直接退款"/"用户提现"/"商户号退款"),stack_group="现金流"
- 共享时间维度:dimensions=[{field:各表时间字段, alias:"日期", role:"time", time_grain:"day"}](alias 必须一致!)
- merge_strategy="add",time_range_default={"days":90}
- 依赖:1A(quarter/year 可选)、1B(stack_group 必须)

### 视图 2:退费操作人统计
- code=`preset_refund_operator`,chart_type=`bar`(可切 pie 看占比)
- 单 segment:refund,filter refund_method='线上' AND status='已退款' AND operator notin ['sys','system']
- dimensions:operator(role=person,自动归一化)
- measures:sum(refund_amount) alias='退款金额' + count(*) alias='退款笔数'
- 依赖:1A,person 归一化已就位

### 视图 4:提现 vs 转入(池子水位)
- code=`preset_wallet_pool`,chart_type=`line`
- 2 segment:wallet op_type='订单退费转入'(alias="转入") + wallet op_type='用户提现'(alias="提现")
- dimensions:op_time(time, day) alias='日期'(两 segment alias 一致)
- cumulative=[{alias:"净水位", inflow:"转入", outflow:"提现"}]
- time_range_default={"days":180}
- 依赖:1C

### 视图 3:日记录按人员/原因(SN 关联 service_tickets)
- code=`preset_refund_daily_detail`,chart_type=`table`
- 单 segment:refund_wide,filter refund_method='线上' AND status='已退款'(字段已冗余到 refund_wide)
- dimensions:refund_no/created_at_raw(day)/operator(person)/refund_reason(category)/service_item(category,来自ticket)/has_external_complaint(category)
- measures:sum(refund_amount) alias='退款金额'
- classification_target='refund_no',classification_rule_names=["客诉-外部投诉","客诉-原因","客诉-审核人"](顺序=优先级)
- 依赖:阶段 2、阶段 3

### 视图 5:提现 SN 关联退费单/工单时间分布
- code=`preset_wallet_refund_timing`,chart_type=`table`
- 单 segment:refund_wide,filter wallet_id is_not_null
- dimensions:wallet_op_time(day)/refund_no/ticket_no/device_sn
- measures:sum(wallet_op_amount) alias='提现金额' + sum(refund_amount) alias='退款金额'
- 依赖:阶段 2

**⚠ 跨视图风险**:多 segment 视图(1/4)维度 alias 不一致会静默丢数据,务必保证各 segment 维度 alias 完全一致。

---

## 阶段 5:审计时序页(独立,可最早开始)

### 5A. 后端时序端点
- `backend/app/api/v1/devices.py`(get_device 之后):新增 `GET /devices/{sn}/timeline?limit=50&days=30`
  - 查 4 表(refund by device_sn / ticket by device_sn / wallet by device_sn / merchant_refund by **device_no**),统一 schema `{type, time, ref_id, summary, amount?}`,合并按 time 倒序
  - 注意:现有 get_device:62-108 查了 4 表但 wallets/merchant_refunds 是死代码(查了未返回);本端点是新增独立端点,不修改 get_device
- `backend/app/api/v1/persons.py`(get_person 之后):新增 `GET /persons/{person_id}/timeline?limit=50&days=30`
  - 查 PersonAlias where person_id → 在 6 表 IN aliases 查记录(refund.operator/auditor, ticket.handler/registrant, wallet.operator, cs_record.operator, external_complaint.follower/reception_cs, card_complaint.handler;merchant_refund 无人员字段不含)
  - 参考 `count_alias_occurrences`(persons.py:263)的查表逻辑,但需返回明细非 count
- (可选)新建 `backend/app/schemas/audit_timeline.py`:TimelineItem(type/time/ref_id/summary/amount?) + TimelineResponse

### 5B. 前端审计页
- 新建 `web/src/views/audit/DeviceAudit.vue` + `PersonAudit.vue`:上部 el-timeline(type 决定标签颜色:refund红/wallet蓝/ticket绿/merchant_refund橙,显示 time+summary+amount),下部 el-table 明细(type tab 切换筛选,分页,字段精简)
- (可选)新建 `web/src/components/AuditTimeline.vue` 纯展示组件(props: items, typeColorMap),两页共用
- `web/src/router/index.js:131-136`(analytics/operator 之后):加 `analytics/device-audit` + `analytics/person-audit` 路由,meta hidden=true(从设备/人员详情跳转进入)
- (可选)设备详情页/人员详情页加"查看时序"按钮跳转

**验证**:`GET /devices/{sn}/timeline` 返回 4 表合并倒序;`GET /persons/{person_id}/timeline` 返回 6 表合并;前端 `/analytics/device-audit?sn=XXX` 显示时序流+明细表;type tab 筛选生效;空数据显示 el-empty。

**⚠ 风险**:6 表 IN aliases 查询性能(别名多时慢),建议每表限 200 条再合并;summary 文案需与用户确认;时区已修复前端 dayjs 自动转北京。

---

## 跨阶段风险汇总

1. **_merge_segment_results 多 segment 维度 alias 不一致静默丢数据**(1B/视图1/4)— 在 `_merge_segment_results:263` 加 WARNING 日志;ViewBuilder UI 强制提示
2. **matcher 性能**(阶段2)— `_add_relation:63-79` 逐条 SELECT,首跑可能数十分钟;批量 INSERT ON CONFLICT 优化作为独立子任务
3. **Alembic 迁移链**(1C→2)— 1C 加 cumulative 列(server_default='[]'::jsonb 安全);2 建 refund_wide 表 + data_match_relations 唯一约束(加前必须去重);down_revision 链顺序不能错
4. **Dockerfile 依赖** — 本方案无新增 Python 依赖(仅用 SQLAlchemy/pg_insert);若 wide_table_builder 引入新依赖必须同步改 `backend/Dockerfile` 硬编码 pip list
5. **refund_wide 字段冗余** — 视图 3/5 需要 refund_wide 冗余 refund 表的 refund_method/status/refund_reason/operator/auditor/refund_amount/created_at_raw;阶段 2A 建模必须包含,wide_table_builder 必须 join refund 表填这些字段
6. **分类规则字段与 source_code 对齐** — 规则1 仅适用 refund_wide;refund_wide 冗余 refund_reason/auditor 后 3 规则都能在 refund_wide 视图用
7. **cumulative 累计起算点** — time_range 窗口内累计非全期,文档/UI 需说明

## 部署节奏建议

- **Sprint 1**:阶段 1A+1B+1C(引擎三能力)+ 阶段 5(审计页,完全独立)
- **Sprint 2**:阶段 2(refund_wide+matcher钩子+迁移)+ 视图 1/2(不依赖 refund_wide)
- **Sprint 3**:阶段 3(分类规则)+ 视图 3/4/5
- **Sprint 4**:回归 + matcher 性能调优 + 文档完善

## 验证方式(端到端)

每个阶段完成后:
1. 后端:`alembic upgrade head` → 重启后端,看启动日志 seed 注册成功
2. 前端:`pnpm dev` 重启,清浏览器缓存
3. ViewBuilder 新建/编辑视图预览,确认引擎能力生效
4. 预设视图列表应出现新视图,执行结果数据正确
5. 抽样数据人工对齐源表,确保数值/分类/时序正确
6. 审计页跳转 + 时序流 + 明细表渲染正常

## Critical Files

- `backend/app/services/analysis_engine.py`(三块引擎改造核心:384-398 时间粒度、302-349 stack_group+cumulative、84-95 cumulative 计算)
- `backend/app/services/preset_views_seed.py`(5 视图追加 + classification_rule_names 解析 518-554)
- `backend/app/services/data_source_registry.py`(refund_wide 注册 29-128 + FIELD_OVERRIDES 132-251)
- `backend/app/models/refund_wide.py`(新建,物化宽表模型)
- `backend/app/models/analysis_view.py`(cumulative JSONB 列 55-57)
- `backend/app/services/wide_table_builder.py`(新建,物化构建服务)
- `backend/app/services/matcher.py`(match_all 钩子 28-43 + _add_relation 性能 63-79)
- `backend/app/services/preset_classification_rules_seed.py`(新建,3 条分类规则)
- `backend/app/api/v1/devices.py` + `persons.py`(timeline 端点)
- `web/src/views/audit/DeviceAudit.vue` + `PersonAudit.vue`(新建)
- `web/src/views/analytics/ViewBuilder.vue` + `ViewRender.vue` + `components/ViewItemCard.vue`(stack 透传 + quarter/year 选项)
