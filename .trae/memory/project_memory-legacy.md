## Hard Constraints
- Docker Hub images must use domestic mirror: docker.1panel.live
- apt, pip, npm packages must use Tsinghua/npmmirror domestic sources
- Direct refund data must be filtered by refund_method='线上' and exclude sys/system operators
- User withdrawal data must be filtered by op_type='用户提现'
- Business data imported from operations platform is READ-ONLY in this system; never modify business fields
- SN is globally unique; missing SN means incomplete import, not duplicate
- Time stats use original table times; system time only for system-created data
- Amount precision unified to 2 decimals, direct SUM
- Merchant order numbers must NOT be normalized (platform-specific definition)
- Scheduled synchronization must explicitly limit to current day's data (00:00 ~ now) to prevent excessive historical data sync

## Engineering Conventions
- CSV parsing truncates trailing empty columns to align headers with data
- Real cash flow calculation combines direct refunds and user withdrawals
- Business tables keep structure aligned with original export tables; only add index fields when necessary
- New tables join via data_sources + data_source_fields metadata registration (zero-code ingestion)
- Analysis engine supports ratio metrics (alias/numerator/denominator/as_percent) for calculating success rates, recovery rates, etc.
- All _execute_upsert calls must include the db parameter as the first argument to prevent positional parameter errors
- device_combo 初始套餐(免费赠送/体验套餐,BMS导出无充值订单号)入库策略: 生成 fallback 主键 `INIT::{device_sn}::{package_name}::{start_date}`(INIT::前缀与真实订单号BR/WX/SP隔离; device_sn+package_name+start_date 唯一保证幂等on_conflict_do_update; device_sn也空才报错跳过)
- Analysis engine resolves person names by joining with persons and person_aliases tables when dimension role is set to 'person'
- Analysis engine supports mixed chart types: cumulative series marked with series_type='line' will render as line overlay on bar charts

## Lessons Learned
- Using koa-connect wrapper caused ctx leaks; native Koa middleware required instead
- FastAPI 422 validation errors return array format; frontend axios interceptors must convert to string to avoid InvalidCharacterError
- Browser caching old JS files causes post-fix errors; verify with cache clearing (caches.delete + hard refresh)
- Modifying imports based on outdated context snapshots leads to missing dependencies; always grep actual current imports before editing
- Vue Router same-component reuse trap: watch route changes to reload data when using same component for multiple routes
- Changing .env's CONFIG_ENCRYPTION_KEY or JWT_SECRET_KEY invalidates existing encrypted sync_source_configs credentials; all must be re-written via sync settings page
- Adding a Python dependency to pyproject.toml does NOT add it to the Docker image: backend/Dockerfile has a hardcoded `pip install` list that must be updated separately, then rebuilt with `--no-cache` to pick up the new package
- ai-acc platform login response returns {token,userId,username,role,...} with token at the TOP LEVEL of JSON, NOT nested under a data field — different from BMS-style {data:{token}}. When integrating new REST APIs, verify the actual response shape with a curl probe before coding the parser
- User-provided import URLs are often FRONTEND routes (e.g. /admin/assistant/handle-record-export), not API endpoints. The real REST endpoints usually differ (e.g. /ai-acc/cs/v1/assistant/handleRecord/export); probe the platform to discover actual API paths
- fetch_mode field in BMS_DATA_SOURCES seed config routes sync tasks: "http" → ai_acc_fetcher (httpx, no browser), default/"playwright" → BmsFetcher (Playwright). fetch_mode is NOT in the sync_source_configs DB table; it's merged from the seed at config-read time
- ai-acc 导出接口限制单次时间范围不得超过 7 天(正好 7 天允许,超过报 {"code":"500","message":"操作时间范围不能超过7天"});ai_acc_fetcher 对 >7 天区间自动分片:切成 ≤7 天连续子区间(边界相接),逐片 GET 后用 pandas(dtype=str 读 + openpyxl 写)合并为单个 xlsx,drop_duplicates() 去除边界 inclusive 端点重复。合并文件内部 0 重复(已验证)。注意:cs_record pk_strategy="none" 插入不去重,跨同步范围重叠会产生重复行——正常增量同步(用 last_end_time 非重叠)无此问题,但手动重同步重叠范围会有重复
- 业务时间字段时区 bug(已修复):运营平台(BMS CSV / ai-acc xlsx)导出的时间为 naive 北京时间,upsert._to_datetime 用 pd.to_datetime 返回 naive datetime,存入 DateTime(timezone=True) 列时因 DB 时区=Etc/UTC 被当 UTC 解释,导致按北京显示偏后 8 小时。修复:_to_datetime 把 naive 视为北京时间(UTC+8)→ tz_localize(_TZ_CN).tz_convert(utc) 后再存,aware 原样返回;纯日期字段(如 complaint_date)迁移后按北京显示 00:00(日期不变)。存量数据用 SQL `col - INTERVAL '8 hours'` 统一迁移了 7 张业务表(wallet/service_tickets/refunds/cs_records/device_combos/external_complaints/card_complaints,merchant_refunds 为空跳过);系统内部表(incidents/measures/reports 等服务器生成 UTC 时间)未动。修复后 wallet 验证记录按北京显示=CSV 原始值,小时分布回归业务时段
- 预设视图与看板的衔接(易遗漏):新增/重构预设视图(preset_views_seed.py 的 PRESET_VIEWS)后,必须同步更新 preset_dashboards_seed.py 中对应看板(如 preset_dashboard_refund)的 items 引用,否则看板仍展示旧视图——seed 是幂等 upsert,重启会用 seed 覆盖看板 items,新视图不接上看板就不会显示。退款现金流重构(2026-07)新增 5 个 v2 视图(preset_cash_trend_v2/preset_refund_operator_v2/preset_wallet_pool_v2/preset_refund_daily_detail_v2/preset_wallet_refund_timing_v2),首次遗漏了更新看板 seed 导致退款看板内容没换;已修复:退款看板 items 改为 5 个 v2 视图 + 3 个基础分布(状态/原因/方式),移除旧现金流视图及繁杂分类视图
- 服务项/商户/套餐分类视图(preset_category_service_item/merchant/package)数据繁杂未归一化,用户反馈暂从退款看板移除;待后续对 service_item/merchant_name/package_name 做汇聚归一化后再加回看板
- 设备/人员审计时序页(2026-07 新增):backend services/audit_timeline.py 提供 build_device_timeline/build_person_timeline,跨 7 张业务表合并时序;devices.py GET /devices/{sn}/timeline + persons.py GET /persons/{person_id}/timeline;前端 /audit/device + /audit/person 路由(数据中心菜单入口),AuditTimeline.vue 共用组件(时序流 el-timeline + 明细表 el-table 双 tab,字段精简);persons.py list_persons 加了 keyword 模糊搜索
- 分析引擎时间过滤 bug(已修复):_build_time_filter 原本只在 segment 有 role=time 维度时才过滤时间,无 time 维度(如按审核人/状态/原因分组的视图)则返回 None 不过滤——导致看板切换 7天/30天 这些视图数据不变。注释写了"否则用 DataSource.time_field"但代码漏实现。修复:_build_time_filter 加 fallback_time_field 参数,_execute_segment 调用时传 ds.time_field,使所有视图都受看板时间范围过滤。preset_refund_operator_v2 同时改为:operator→auditor(退费审核人)、bar→pie(TOP10+其他归并,占比和=100%)、去掉笔数度量(避免量纲冲突)。引擎 pie 分支 _to_chart_data 加 TOP10+其他归并逻辑(>11项时)。注意:docker compose build 有时会命中 COPY 层缓存导致代码没更新,需用 --no-cache 强制重建
- 退费原因字段来源:refund.refund_reason 99%是"用户退款"无业务意义,真实退款原因在 service_tickets.service_item(需 service_category='退款' 过滤)。preset_refund_reason_top 已改用 ticket 数据源 + service_item 维度,显示"网慢/网卡/无网络/不想用了/设备故障/充值错误"等真实原因。refund_wide 中 service_item 覆盖率仅 16%(大部分退费单未关联工单),但视图直接查 ticket 表不受此限制
- 引擎混合图表支持(柱状+曲线 overlay):_to_chart_data 的 cumulative 系列加 series_type="line" 标记,前端 ViewItemCard series 渲染用 `s.series_type || r.chart_type` 决定 type。preset_wallet_pool_v2 用此能力:chart_type=bar(转入/提现柱状) + 净水位 cumulative 系列自动成 line 曲线 overlay。退费状态分布视图(preset_refund_status)已从退款看板移除(无业务意义),看板现为 7 视图
- sync-all 500 回归 bug(已修复):bms_sync.py sync_all 函数的 post-sync hook 里写了局部 `from app.core.database import SessionLocal`(顶部已 import),Python 将 SessionLocal 视为整个函数的局部变量,导致主流程 `db = SessionLocal()`(line 249)抛 UnboundLocalError,sync-all 和定时同步全部 500。异常被 hook 的 try/except 外层吞掉且无日志,5ms 即返回 500。修复:删掉局部 import,复用顶部 import。教训:函数内局部 import 一个已顶层 import 的名字会触发 UnboundLocalError;诊断此类无日志的 500 应直接 docker exec python 调用函数拿 traceback
- 系统时间标签审核结论(2026-08-01,审核脚本存 data/audit_time_0X_*.sql,报告 docs/数据时间审核报告.md):(1) 时区修复已生效,无残留 8h 偏移,无未来/远古日期,cs_records 小时分布单峰(9 点/21 点峰)符合 8~23:59 客服节奏。(2) service_tickets.finish_time 字段不可靠,89% 非真实完成时间(50% 被 BMS 批量覆盖 finish>register+12h、23% finish==register 同步填充、17% finish==register 同瞬填充,仅 10.7% 是真实完成);统计客服完成时段应改用 register_time+handle_duration,或过滤 `status='已完成' AND handler非空 AND finish BETWEEN register AND register+12h`(仅剩 1282 条)。(3) 上游 BMS 批量时间戳(非我方同步,我方同步在 00:00/08:00):wallet 03:15 的 1631 条全是 op_type='订单退费转入' SYS 自动;device_combos 02:05 的 12260 条全是 creator='Wallet' 自动激活;refunds 03:15 的 841 条主要是 SYS 批量状态更新——这些都是系统操作,凌晨运行合理,但统计客服工作负荷必须 `WHERE operator NOT IN ('SYS','system','Wallet','System')` 排除。(4) 时间字段优先级:退费用 created_at_raw(稳),不用 updated_at_raw(被批量覆盖);工单产生时段用 register_time,不用 finish_time;用户提现用 op_time 但 op_type='用户提现' 排除 '订单退费转入'(SYS 自动)。(5) cs_records 极干净,仅 3 条 00:03 边缘案例(企微-李淑琴 7-26 收尾)占 0.004%