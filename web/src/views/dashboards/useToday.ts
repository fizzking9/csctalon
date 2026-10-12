// 由 Today.vue 的 <script setup> 拆分而来(issue #184)。
// 模板零改动: 全部顶层绑定名经 return 回注 .vue 的 setup 解构。
// 组件与图标 import 留在 .vue 内注册(模板解析需要); 依赖 import 在此独立声明。

// 视图模式: day=日(默认), week=周(完整自然周 周一~周日)
// 数据
// 投诉专项
// F-P1-2: 明细窗口内 登记/处理 计数(标题展示)
// 积压待处理(近30天窗口内登记的未完成, 不随今日/本周切换)
// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
// 实时趋势: 默认只显示接待,可勾选投诉/退款
// 审计弹窗(点击 SN / 人员 唤起)
// 退款挽回 SN 明细(折叠, 默认收起)
// 退款SN筛选
// 投诉筛选: 明细区仅按来源(**三源**)筛选
// ★★ 2026-09-11 (#53): 卡商**移出**筛选器 —— 卡商是独立统计体系(不属投诉总量)。
//    卡商明细请在独立的卡商区块查看。
// 当前政策 (静态配置，后续可从接口获取)
// 格式化
// 防御:误把 {value,yoy_pct} 对象传进来
// 与 OperationDashboard 保持一致,跨日期范围可分清哪天
// #47 (2026-09-10): 卡商单列 —— 原先把卡商归入外部投诉, 现独立配色
// 投诉来源 登记/处理 双柱图 (F-P1-2)
// ★★ 2026-09-11 (#53): 卡商**移出** —— 用户要求"不纳入总量的管理, 整体独立统计分析"。
//    双柱图现为**三源**(内部/商户号/外部); 卡商见独立的卡商区块。
// 投诉分类柱状图选项
// 合并三个来源的by_category
// 明细表列头筛选选项(来源/分类/状态, el-table :filters 需要 {text,value})
// 明细区仅按来源(四类)筛选; 分类/状态筛选与排序在表格列头
// 时间字符串排序(ISO), 空值排后(供表格列 sort-method)
// 补充数据源汇总文本(315/卡商, workbuddy推送)
// #47 (2026-09-10): 卡商单列展示(不再写成'卡商群投诉'并归入外部)
// #47: 卡商登记/处理(来源桶新增的第 4 类)
// ★★ 2026-09-11 (#53): 卡商**独立统计** —— 不属 complaintSummaryData 的总量
//   用户: "(外)卡商 的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
//   后端返回 `card` 对象(与 reg_handled 平行), 此处独立取值。
// 补充明细 tooltip 文案(来源/日期/内容/处理明细/完成时间)
// 影响事件状态映射 (后端 Incident.status: registered/processing/calmed/reviewed)
// 首页突出: 当日活跃影响事件(周期性 / 未闭环 / 高严重度)
// 点击横幅/事件chip滚动到影响事件区
/* ══════════════ #76 统一卡片下钻 ══════════════
   设计说明（Design Rationale）
   目的：**让每个数字都能被追问** —— 使用者看到异常数字时，当场点进去看构成，
         不必截图找人。这是「决策分析看板」而非「展示看板」的分界线。
   逻辑：三类去向（规范 §3.2 优先级）：
         ① 跳明细页 + 带筛选参数（默认，本页全部下钻走这条）
         ② 弹抽屉（已有：SN/人名 → 审计抽屉，沿用 useAudit，不改）
         ③ 滚到同页区块（已有：影响事件横幅，沿用 goToImpactSection）
   口径：★ 下钻必须带**当前时间窗口**（规范 §3.3）—— 本页窗口由 getRange()
         产出（日视图=当日，周视图=本周），逐字透传给目标页，
         否则目标页落到默认近30天，数字与刚才所见对不上 = 下钻失效。
   取舍：❌ 不为每个数字单独开发抽屉 —— 明细页已具备筛选/排序/导出，
         重复造抽屉是「同指标两处各实现一遍」（违反界面设计规范 §四）。
         ❌ 不做「趋势图点击下钻到某小时明细」—— 明细接口按日筛选，
         无小时级筛选能力；强行做会落到「点了一天明细」的误导结果。
         已在图卡 ⓘ 中声明该限制。
   工单：#76 · 规范：docs/standards/看板卡片数据说明与下钻规范.md */
/** 当前窗口（日=当日 / 周=本周），与所有卡片所见窗口一致 */
/**
 * 跳到投诉分析页并带完整上下文。
 * @param {object} opt - { view, source } view=overview|channel|subject, source=internal|merchant|external|card
 */
// 卡商/内部等渠道映射到目标页的 channelTab 取值（见 ComplaintAnalysis.vue 的 channelTab）
/** 跳到执行跟踪（积压明细），带窗口 */
/** 跳到退款挽回分析，带窗口 */
/** 跳到运营看板（人均效率），带窗口 */
/** KPI 卡 → 对应分析页（key 与 kpiCards 的 key 一一对应） */
/** 投诉来源卡 → 渠道明细（source 用目标页 channelTab 的取值） */
// 专项任务状态映射 (后端 SpecialTask.status)
// 选中的看板日期 (默认今天, 支持切换到历史任意一天)
// ── 前端内存缓存: 按 viewMode+start+end 缓存上次加载结果, 切回同窗口秒开 ──
// 背景(2026-08-31): 后端历史窗口缓存 TTL 已拉长到 24h, 但前端切日期仍会
// Promise.all 重拉 8 个接口。历史数据不变化, 切回同一天直接用内存数据即可,
// 不必等后端缓存命中(省一次网络往返)。仅「日视图·今天」手动/自动刷新强制重拉。
// { key, data: {...8个响应} } 单条(最近一次加载的窗口)
// 是否强制刷新: 仅「日视图·今天」的手动刷新/自动刷新; 切换窗口一律走缓存
// 周视图的起止日期(周一00:00 到 周日23:59;如果周日晚于今天则截止到今天结束)
// ⚠️ 已抽到 @/utils/weekRange 作为单一来源（运营看板此前各写一份，写法不一致
//    导致「周日当天本周算成下周」的 bug）。
// 周视图下,判断是否是"当前周"(即selectedDate所在周包含今天)
// 实时数据标签: 日视图且是今天 才显示"实时数据"
// 展示日期文案
// 看板标题
// ★ #80 正名: 「决策分析看板」→「今日值班看板」（用户 2026-09-13 拍板）。
//   理由: 本页内容是**值班台** —— 实时KPI + 值班组长 + 今日影响事件 + 我的待办 +
//   进行中专项 + 政策。它是「平时每天看」的那一个；
//   称为「决策分析」属**过度拔高** —— 本页**没有归因能力、没有目标达成对比、
//   没有异常分解**，三个决策分析的必要件一个都不具备。
//   真正的分析页是 `/dashboards/operation`（四时间粒度 + 全量人效 + 原因Top10），
//   已同步正名为「运营分析看板」。
//   ⚠️ 保留「今日」但主名固定不随视图跳动（原「本周看板」的写法会让使用者
//      以为是两个页面），日期/周次由标题右侧 `displayDate` 承载。
// 选中的日期/周对应的时间范围 (传给后端 start/end)
// 切换日期后重新加载全部数据
// KPI卡片
// 后端 today_kpi 返回嵌套结构 {value, yoy_value, yoy_pct, ...}
// ★ #78: 按 SN(接待数)口径分层 —— 含 AI 独有的 SN
// ★ #78: 按会话口径分层（含 AI 全自动接待）
// 值班组长/主管 (仅显示岗位为主管/组长/副主管的人员; 注意: is_management=true不仅包含管理岗,
// 还包含投诉/退款挽回等享有跨类豁免的专项岗, 不能直接用is_management判断值班组长)
/** ★ 岗位 → 值班徽标（用户 2026-09-14：「组长」/「管理」要一眼可辨）
 *
 *  设计要点:
 *   · 主管级 → 文案「管理」（管理视角的词，而非职级名）
 *   · 组长/副主管 → 文案「组长」（副主管在值班语境下同属组级管理者）
 *   · 客服/投诉专员/退款处理专员 → 不进值班条（非决策岗）
 *   ⚠️ 与 `DUTY_POSITIONS` 必须保持同源 —— 两边不一致会出现
 *      "徽标为空的芯片"或"该显示却查不到标签"。
 */
// 四路经营退款路线配色 (A 线上直退 / B 钱包取现 / C 闪猫退款 / D 手动退款)
// ★ #85 投诉两序列配色（登记=橙柱 / 处理=深青线）
//   登记用暖色（“进来了”）、处理用冷青色（“解决了”）—— 直觉上区分"进"与"出"。
//   ⚠️ 处理线**不能用 #10b981** —— 那是「接待数」柱的颜色，
//     同色会导致同屏两根“绿色”无法区分（实测像素采样发现），
//     故选 teal-700 (#0f766e)，与全图现有色均不撞。
// 将四路退款拆分 + 工作量/投诉 组织成 ECharts series
// ★ #85: 投诉改为 **柱(登记) + 线(处理)**（用户 2026-09-14 选定）
//   为什么拆：原单柱把「登记」与「处理」混为一种含义，主管无法判断
//   「今天进的多」还是「今天清的多」—— 两者要采取的动作完全不同。
//   口径（与后端同源，勿在前端另算）:
//     · complaint      = 登记（工单 register_time 落桶，三源分桶，**不含卡商**）
//     · complaint_done = 处理（finish_time 落桶 + 真实完成 ≥3min，**含往期单办结**）
//   ★ 不写「投诉量」—— 避免又产生第三种语义（#85 的核心教训）
// 退款笔数: 四路堆叠柱
// 退款金额: 四路堆叠面积线 (名字后不带 legend, 单独在 tooltip 体现)
// ★ #85: 图例写「登记」「处理」——**不写"投诉量"**，避免第三种语义
// 手机上合并 0~8点 (首桶 0~7 点合成一个), 展示 0~8点 + 08:00~23:00, 更适配窄屏
// ★ #85: 处理序列同样需合并（否则手机端处理线为空）
// 趋势图
// 判定"企微人员":部门为"企微" / 姓名/别名前缀为"企微-" / qw_user_id 已绑定
// (用于人均效率默认过滤, 比严格 qw_user_id 非空更贴近业务语义: 部门=企微就展示)
// 工作效率表: 各专项接待明细列 (按类型分开SN/接待数)
// 从breakdown取某专项的接待数(个/SN去重后)
// 人均效率 (全量排序, 默认仅企微, 勾选后显示所有人)
// 只要有任意业务数据(会话或接待)就保留 (避免空行占位 与后端ops_union语义一致)
// 默认按接待效率(eff_score)降序排名
// ★ #78 AI / 系统账户单列（用户明确：「区分出来，单列也可以」）
//   设计说明: 此前 `is_virtual` 的账户被 `topStaffList` 直接**过滤丢弃**，
//   导致 AI 的工作量在生产上完全不可见（只能从「会话数」虚高中间接感知）。
//   这里改为**单列展示**：AI 仍在参与工作，管理者需要看到它干了多少。
//   取舍: 不并入真人排名（会挤掉真人），也不隐藏（会看不见 AI 规模），
//   而是单独一张小表 —— 两者都能看到，互不干扰。
/** 该账户的来源标签/配色（与 KPI 卡 SOURCE_KINDS 同一套色） */
/** 该 AI/系统账户名下会话数占全站会话数的百分比（可控指标：AI 接管率） */
// 部门列显示策略:移动端且仅企微时隐藏(表头已筛选); 显示所有人时保留以便区分
// 退款挽回SN列表 (按时间倒序)
// 退款SN筛选选项
// 退款原因分布: 堆叠柱状图(成功+失败)
// 从 sn_details 聚合成功/失败数
// 成功率颜色
// 加载数据
// 前端内存缓存: 切回同窗口直接用上次结果(历史数据不变化), 不发请求秒开。
// 仅「日视图·今天」手动/自动刷新走 force=true 强制重拉。
// 命中内存缓存且非强制刷新 → 直接渲染(不发网络请求)
// 积压水位: 固定近30天窗口(登记日期落在窗口内的未完成), 避免历史遗留无限堆叠
// 把一次加载结果应用到各响应式状态
// F-P1-2: 明细窗口 登记/处理 计数(标题展示)
// 仅「日视图·今天」时每60秒自动刷新; 周视图/历史日期数据不需要轮询。
// force=true: 今天是实时窗口, 自动刷新必须真正重拉(不走内存缓存)。

import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { today } from '@/api/analytics'
import { complaintSummary, complaintDetail, complaintBacklog } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'
import { SOURCE_KINDS, buildStack, stackTip, sourceKindMeta } from '@/utils/receptionSource'
import { getWeekRange as _getWeekRange } from '@/utils/weekRange'
import { useIncidentMarkers } from '@/composables/useIncidentMarkers'
import { usePolling, POLL_INTERVALS } from '@/composables/usePolling'
import dayjs from 'dayjs'

export function useToday() {
  const loading = ref(false)

  const lastUpdate = ref('')

  const isMobile = ref(false)

  let _mq = null

  // #233 B1.5-2 看板时间轴标注层: 趋势图事件标记(markPoint/markLine)
  // #234 B1.5-3 事件与数据对照: 详情对话框展示事件前后窗口的接待/投诉/退款量变化
  const {
    markers: incidentMarkers,
    markerDetailMap: incidentMarkerMap,
    loading: incidentMarkersLoading,
    impactComparison: incidentImpactComparison,
    impactComparisonRows: incidentImpactComparisonRows,
    impactLoading: incidentImpactLoading,
    loadMarkers: loadIncidentMarkers,
    loadImpactComparison: loadIncidentImpactComparison,
    buildChartMarks: buildIncidentMarks,
    resolveClick: resolveIncidentClick,
  } = useIncidentMarkers()
  // 当前激活的事件详情(点击 markPoint/markLine 后填充, 用于弹窗展示)
  const activeIncidentDetail = ref(null)
  const incidentDetailVisible = ref(false)
  function onChartClick(params) {
    // 只处理 markPoint/markLine/markArea 点击, 不响应 series 数据点点击(避免误触)
    if (!params || !['markPoint', 'markLine', 'markArea'].includes(params.componentType)) return
    const m = resolveIncidentClick(params)
    if (!m) return
    activeIncidentDetail.value = m
    incidentDetailVisible.value = true
    // #234 B1.5-3 触发事件影响对照加载(失败静默, 不阻塞弹窗)
    loadIncidentImpactComparison(m.id, { window_minutes: 60 }).catch(() => {})
  }

// 视图模式: day=日(默认), week=周(完整自然周 周一~周日)
  const viewMode = ref('day')

// 数据
  const kpiData = ref({})

  const hourlyData = ref({})

  const staffData = ref({})

  const queueData = ref({})

  const recoveryData = ref({})

  const hasRecovery = computed(() => !!recoveryData.value?.kpi)

// 投诉专项
  const complaintSummaryData = ref({})

  const complaintList = ref([])

// F-P1-2: 明细窗口内 登记/处理 计数(标题展示)
  const complaintDetailCount = ref({ registered: 0, handled: 0, total: 0 })

  const complaintCollapseActive = ref(['detail'])

// 积压待处理(近30天窗口内登记的未完成, 不随今日/本周切换)
  const backlogTotal = ref(0)

// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
  const showAllStaff = ref(false)

// 实时趋势: 默认只显示接待,可勾选投诉/退款
  const trendSelectedGroups = ref(['reception'])

// 审计弹窗(点击 SN / 人员 唤起)
  const { openDeviceAudit, openPersonAudit } = useAudit()

// 退款挽回 SN 明细(折叠, 默认收起)
  const recoveryCollapseActive = ref([])

// 退款SN筛选
  const refundFilterResult = ref('')

  const refundFilterHandler = ref('')

  const refundFilterReason = ref('')

// 投诉筛选: 明细区仅按来源(**三源**)筛选
// ★★ 2026-09-11 (#53): 卡商**移出**筛选器 —— 卡商是独立统计体系(不属投诉总量)。
//    卡商明细请在独立的卡商区块查看。
  const COMPLAINT_SOURCE_OPTIONS = ['内部投诉', '商户号投诉', '外部投诉']

  const complaintFilterSource = ref('')

// 当前政策 (静态配置，后续可从接口获取)
  const currentPolicies = ref([
    {
      name: '退款挽回专项行动',
      description: '针对近期退款率上升，启动退款挽回专项行动，目标挽回率提升至40%',
      status: '执行中',
      type: 'warning',
    },
    {
      name: '客户满意度提升计划',
      description: '加强售前咨询质量，确保响应时长<3分钟，解决率>95%',
      status: '进行中',
      type: 'success',
    },
  ])

// 格式化
  function formatNum(v) {
    if (v === null || v === undefined) return '-'
    if (typeof v === 'object') {
      // 防御:误把 {value,yoy_pct} 对象传进来
      if ('value' in v) return formatNum(v.value)
      return '-'
    }
    const n = Number(v)
    if (Number.isNaN(n)) return '-'
    if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
    return Math.round(n).toString()
  }

  function formatShortTime(t) {
    if (t === null || t === undefined || t === '' || t === '-') return '-'
    const d = dayjs(t)
    if (!d.isValid()) return '-'
    // 与 OperationDashboard 保持一致,跨日期范围可分清哪天
    return d.format('MM-DD HH:mm')
  }

  function formatMoney(v) {
    if (v === null || v === undefined) return '-'
    if (typeof v === 'object') {
      if ('value' in v) return formatMoney(v.value)
      return '-'
    }
    const n = Number(v)
    if (Number.isNaN(n)) return '-'
    return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  function scoreTagType(score, d = 500, w = 300, s = 100) {
    if (score >= d) return 'danger'
    if (score >= w) return 'warning'
    if (score >= s) return 'success'
    return 'info'
  }

  function complaintSourceType(source) {
    if (source === '内部投诉') return 'warning'
    if (source === '外部投诉') return 'danger'
    if (source === '商户号投诉') return 'primary'
    // #47 (2026-09-10): 卡商单列 —— 原先把卡商归入外部投诉, 现独立配色
    if (source === '(外)卡商' || source === '卡商群投诉') return 'success'
    return 'info'
  }

  function complaintStatusType(status) {
    if (!status) return 'info'
    if (status.includes('完成') || status.includes('已结') || status.includes('解决')) return 'success'
    if (status.includes('处理中') || status.includes('跟进')) return 'warning'
    if (status.includes('待')) return 'danger'
    return 'info'
  }

// 投诉来源 登记/处理 双柱图 (F-P1-2)
// ★★ 2026-09-11 (#53): 卡商**移出** —— 用户要求"不纳入总量的管理, 整体独立统计分析"。
//    双柱图现为**三源**(内部/商户号/外部); 卡商见独立的卡商区块。
  const complaintRegHandledOption = computed(() => {
    const src = complaintSummaryData.value
    const KEYS = [
      { name: '内部投诉', key: 'internal' },
      { name: '商户号投诉', key: 'merchant' },
      { name: '外部投诉', key: 'external' },
    ]
    const data = KEYS.map(({ name, key }) => {
      const s = src[key] || {}
      return {
        name,
        registered: Number(s.registered ?? s.total) || 0,
        handled: Number(s.handled) || 0,
      }
    })
    if (!data.some(d => d.registered || d.handled)) return null
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
      grid: { left: 8, right: 8, bottom: 32, top: 8, containLabel: true },
      xAxis: { type: 'category', data: data.map(d => d.name), axisLabel: { fontSize: 10, interval: 0 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 10 }, minInterval: 1 },
      series: [
        { name: '登记', type: 'bar', data: data.map(d => d.registered),
          itemStyle: { color: '#2563eb', borderRadius: [3, 3, 0, 0] }, barMaxWidth: 16,
          label: { show: true, position: 'top', fontSize: 10 } },
        { name: '处理', type: 'bar', data: data.map(d => d.handled),
          itemStyle: { color: '#10b981', borderRadius: [3, 3, 0, 0] }, barMaxWidth: 16,
          label: { show: true, position: 'top', fontSize: 10 } },
      ],
    }
  })

// 投诉分类柱状图选项
  const complaintCategoryOption = computed(() => {
    // 合并三个来源的by_category
    const cats = {}
    const add = (arr) => {
      if (!arr) return
      arr.forEach(c => {
        const name = c.name || '未标注'
        cats[name] = (cats[name] || 0) + Number(c.count || 0)
      })
    }
    add(complaintSummaryData.value.internal?.by_category)
    add(complaintSummaryData.value.merchant?.by_category)
    add(complaintSummaryData.value.external?.by_category)
    add(complaintSummaryData.value.card?.by_category)
    const entries = Object.entries(cats).sort((a, b) => b[1] - a[1])
    const x = entries.map(e => e[0])
    const data = entries.map(e => e[1])
    if (data.length === 0 || data.every(v => v === 0)) return null
    const total = data.reduce((s, v) => s + v, 0)
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const p = params[0]
          const v = p?.value || 0
          const pct = total ? ((v / total) * 100).toFixed(1) : '0.0'
          return `${p?.name}<br/>${v} 条 (${pct}%)`
        },
      },
      grid: { left: 8, right: 8, bottom: 28, top: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: x,
        axisLabel: { fontSize: 10, interval: 0, rotate: x.length > 4 ? 20 : 0 },
      },
      yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      series: [{
        type: 'bar',
        data: data.map(v => ({ value: v, itemStyle: { color: '#f59e0b', borderRadius: [3, 3, 0, 0] } })),
        barMaxWidth: 24,
        label: { show: true, position: 'top', fontSize: 10 },
      }],
    }
  })

// 明细表列头筛选选项(来源/分类/状态, el-table :filters 需要 {text,value})
  const complaintSourceColumnFilters = computed(() => {
    const set = new Set(complaintList.value.map(r => r.source).filter(Boolean))
    return [...set].map(v => ({ text: v, value: v }))
  })

  const complaintCategoryColumnFilters = computed(() => {
    const set = new Set(complaintList.value.map(r => r.category).filter(Boolean))
    return [...set].map(v => ({ text: v, value: v }))
  })

  const complaintEntityColumnFilters = computed(() => {
    const set = new Set(complaintList.value.map(r => r.entity || '—').filter(Boolean))
    return [...set].map(v => ({ text: v, value: v }))
  })

  const complaintStatusColumnFilters = computed(() => {
    const set = new Set(complaintList.value.map(r => r.status).filter(Boolean))
    return [...set].map(v => ({ text: v, value: v }))
  })

// 明细区仅按来源(四类)筛选; 分类/状态筛选与排序在表格列头
  const complaintFilteredList = computed(() => {
    if (!complaintFilterSource.value) return complaintList.value
    return complaintList.value.filter(d => d.source === complaintFilterSource.value)
  })

// 时间字符串排序(ISO), 空值排后(供表格列 sort-method)
  function sortTimeStr(a, b) {
    const va = a || ''
    const vb = b || ''
    if (va === vb) return 0
    return va < vb ? -1 : 1
  }

// 补充数据源汇总文本(315/卡商, workbuddy推送)
  const supplExternal = computed(() => complaintSummaryData.value?.supplementary?.external || { total: 0, matched: 0 })

  const supplSummaryText = computed(() => {
    const s = complaintSummaryData.value?.supplementary || {}
    const ext = s.external || { total: 0, matched: 0 }
    const card = s.card || { total: 0, matched: 0 }
    if (!ext.total && !card.total) return ''
    // #47 (2026-09-10): 卡商单列展示(不再写成'卡商群投诉'并归入外部)
    return `补充数据源(按SN关联工单): 315外部投诉 ${formatNum(ext.total)} 条(已关联 ${formatNum(ext.matched)}) · 卡商群投诉 ${formatNum(card.total)} 条(已关联 ${formatNum(card.matched)})`
  })

// #47: 卡商登记/处理(来源桶新增的第 4 类)
  const supplCard = computed(() => complaintSummaryData.value?.supplementary?.card || { total: 0, matched: 0 })

// ★★ 2026-09-11 (#53): 卡商**独立统计** —— 不属 complaintSummaryData 的总量
//   用户: "(外)卡商 的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
//   后端返回 `card` 对象(与 reg_handled 平行), 此处独立取值。
  const cardData = computed(() => complaintSummaryData.value?.card || {})

// 补充明细 tooltip 文案(来源/日期/内容/处理明细/完成时间)
  function supplTooltip(row) {
    const list = row.supplementary || []
    if (!list.length) return '无补充明细'
    return list.map(s => {
      const parts = [`[${s.source}]${s.complaint_date || ''}`]
      if (s.problem) parts.push((s.problem || '').slice(0, 60))
      if (s.handle_detail) parts.push(`处理:${(s.handle_detail || '').slice(0, 40)}`)
      if (s.handle_time) parts.push(`完成:${s.handle_time}`)
      return parts.join(' ')
    }).join('\n')
  }

// 影响事件状态映射 (后端 Incident.status: registered/processing/calmed/reviewed)
  const INCIDENT_STATUS = {
    registered: { label: '待确认', type: 'warning' },
    processing: { label: '处理中', type: 'danger' },
    calmed: { label: '已平息', type: 'info' },
    reviewed: { label: '已复盘', type: 'info' },
  }

  function incidentStatusLabel(s) {
    return INCIDENT_STATUS[s]?.label || s || '一般'
  }

  function incidentTagType(s) {
    return INCIDENT_STATUS[s]?.type || 'warning'
  }

// 首页突出: 当日活跃影响事件(周期性 / 未闭环 / 高严重度)
  const activeIncidents = computed(() => {
    const list = queueData.value?.incidents || []
    return list.filter(inc => inc.is_recurring || !inc.is_closed || inc.severity === '高')
  })

  const hasHighSeverityIncident = computed(() => activeIncidents.value.some(inc => inc.severity === '高'))

  const impactBannerTitle = computed(() => {
    const n = activeIncidents.value.length
    const recur = activeIncidents.value.filter(inc => inc.is_recurring).length
    const high = hasHighSeverityIncident.value
    if (high) return `⚠ 当日有 ${n} 项影响事件需关注(含高严重度)`
    if (recur) return `当日有 ${n} 项影响事件生效(含 ${recur} 项周期性)`
    return `当日有 ${n} 项影响事件`
  })

// 点击横幅/事件chip滚动到影响事件区
  function goToImpactSection() {
    const el = document.querySelector('.impact-banner')
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

/* ══════════════ #76 统一卡片下钻 ══════════════
   设计说明（Design Rationale）
   目的：**让每个数字都能被追问** —— 使用者看到异常数字时，当场点进去看构成，
         不必截图找人。这是「决策分析看板」而非「展示看板」的分界线。
   逻辑：三类去向（规范 §3.2 优先级）：
         ① 跳明细页 + 带筛选参数（默认，本页全部下钻走这条）
         ② 弹抽屉（已有：SN/人名 → 审计抽屉，沿用 useAudit，不改）
         ③ 滚到同页区块（已有：影响事件横幅，沿用 goToImpactSection）
   口径：★ 下钻必须带**当前时间窗口**（规范 §3.3）—— 本页窗口由 getRange()
         产出（日视图=当日，周视图=本周），逐字透传给目标页，
         否则目标页落到默认近30天，数字与刚才所见对不上 = 下钻失效。
   取舍：❌ 不为每个数字单独开发抽屉 —— 明细页已具备筛选/排序/导出，
         重复造抽屉是「同指标两处各实现一遍」（违反界面设计规范 §四）。
         ❌ 不做「趋势图点击下钻到某小时明细」—— 明细接口按日筛选，
         无小时级筛选能力；强行做会落到「点了一天明细」的误导结果。
         已在图卡 ⓘ 中声明该限制。
   工单：#76 · 规范：docs/standards/看板卡片数据说明与下钻规范.md */
  const router = useRouter()

/** 当前窗口（日=当日 / 周=本周），与所有卡片所见窗口一致 */
  function drillRange() {
    return getRange()
  }

/**
 * 跳到投诉分析页并带完整上下文。
 * @param {object} opt - { view, source } view=overview|channel|subject, source=internal|merchant|external|card
 */
  function drillToComplaint({ view = 'overview', source = null } = {}) {
    const { start, end } = drillRange()
    const query = { tab: 'analysis', view, start, end }
    // 卡商/内部等渠道映射到目标页的 channelTab 取值（见 ComplaintAnalysis.vue 的 channelTab）
    if (source) query.source = source
    router.push({ path: '/dashboards/complaint-analysis', query })
  }

/** 跳到执行跟踪（积压明细），带窗口 */
  function drillToBacklog() {
    const { start, end } = drillRange()
    router.push({ path: '/dashboards/complaint-analysis', query: { tab: 'execute', start, end } })
  }

/** 跳到退款挽回分析，带窗口 */
  function drillToRefundRecovery() {
    const { start, end } = drillRange()
    router.push({ path: '/dashboards/refund-recovery', query: { start, end } })
  }

/** 跳到运营看板（人均效率），带窗口 */
  function drillToOperation() {
    const { start, end } = drillRange()
    router.push({ path: '/dashboards/operation', query: { start, end } })
  }

/** KPI 卡 → 对应分析页（key 与 kpiCards 的 key 一一对应） */
  function onKpiClick(key) {
    const map = {
      reception: () => router.push({ path: '/dashboards/reception', query: drillRange() }),
      session: () => router.push({ path: '/dashboards/reception', query: drillRange() }),
      refund_count: () => router.push({ path: '/dashboards/refund', query: drillRange() }),
      refund_amount: () => router.push({ path: '/dashboards/refund', query: drillRange() }),
      // #309: 充值卡 → 数据查看·充值流量(承接窗口, 按充值时间 created_at_raw)
      recharge: () => router.push({ path: '/data/recharge-traffic', query: drillRange() }),
      complaint: () => drillToComplaint({ view: 'overview' }),
      staff_on_duty: () => drillToOperation(),
      recovery_rate: () => drillToRefundRecovery(),
    }
    map[key]?.()
  }

/** 投诉来源卡 → 渠道明细（source 用目标页 channelTab 的取值） */
  const COMPLAINT_DRILL_SOURCE = {
    内部投诉: 'internal',
    商户号投诉: 'merchant',
    外部投诉: 'external',
    '(外)卡商': 'card',
  }

  function onComplaintSourceClick(label) {
    const src = COMPLAINT_DRILL_SOURCE[label]
    if (src) drillToComplaint({ view: 'channel', source: src })
  }

// 专项任务状态映射 (后端 SpecialTask.status)
  const TASK_STATUS = {
    pending_start: '待启动',
    executing: '执行中',
    following: '跟进中',
    done: '已完成',
    archived: '已归档',
  }

  function taskStatusLabel(s) {
    return TASK_STATUS[s] || s || '—'
  }

// 选中的看板日期 (默认今天, 支持切换到历史任意一天)
  const selectedDate = ref(dayjs().format('YYYY-MM-DD'))

  const isToday = computed(() => selectedDate.value === dayjs().format('YYYY-MM-DD'))

// ── 前端内存缓存: 按 viewMode+start+end 缓存上次加载结果, 切回同窗口秒开 ──
// 背景(2026-08-31): 后端历史窗口缓存 TTL 已拉长到 24h, 但前端切日期仍会
// Promise.all 重拉 8 个接口。历史数据不变化, 切回同一天直接用内存数据即可,
// 不必等后端缓存命中(省一次网络往返)。仅「日视图·今天」手动/自动刷新强制重拉。
  const _viewCacheKey = () => {
    const { start, end } = getRange()
    return `${viewMode.value}|${start}|${end}`
  }

  let _viewCache = null

// { key, data: {...8个响应} } 单条(最近一次加载的窗口)
// 是否强制刷新: 仅「日视图·今天」的手动刷新/自动刷新; 切换窗口一律走缓存
  const _forceRefresh = () => viewMode.value === 'day' && isToday.value

// 周视图的起止日期(周一00:00 到 周日23:59;如果周日晚于今天则截止到今天结束)
// ⚠️ 已抽到 @/utils/weekRange 作为单一来源（运营看板此前各写一份，写法不一致
//    导致「周日当天本周算成下周」的 bug）。
  function getWeekRange() {
    const { start, end } = _getWeekRange(selectedDate.value)
    let sunday = end
    const nowEnd = dayjs().endOf('day')
    if (sunday.isAfter(nowEnd)) sunday = nowEnd
    return { start, end: sunday }
  }

// 周视图下,判断是否是"当前周"(即selectedDate所在周包含今天)
  const isCurrentWeek = computed(() => {
    if (viewMode.value !== 'week') return false
    const { start, end } = getWeekRange()
    const now = dayjs()
    return (now.isSame(start, 'day') || now.isAfter(start)) && (now.isSame(end, 'day') || now.isBefore(end.add(1, 'second')))
  })

// 实时数据标签: 日视图且是今天 才显示"实时数据"
  const showRealtimeTag = computed(() => viewMode.value === 'day' && isToday.value)

// 展示日期文案
  const displayDate = computed(() => {
    if (viewMode.value === 'week') {
      const { start, end } = getWeekRange()
      const sameMonth = start.month() === end.month()
      const range = sameMonth
        ? `${start.format('YYYY年MM月DD日')} - ${end.format('DD日')}`
        : `${start.format('YYYY年MM月DD日')} - ${end.format('MM月DD日')}`
      const wk = isCurrentWeek.value ? ' 本周' : ''
      return `${range}${wk}`
    }
    const d = dayjs(selectedDate.value)
    const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.day()]
    return `${d.format('YYYY年MM月DD日')} ${wd}`
  })

// 看板标题
// ★ #80 正名: 「决策分析看板」→「今日值班看板」（用户 2026-09-13 拍板）。
//   理由: 本页内容是**值班台** —— 实时KPI + 值班组长 + 今日影响事件 + 我的待办 +
//   进行中专项 + 政策。它是「平时每天看」的那一个；
//   称为「决策分析」属**过度拔高** —— 本页**没有归因能力、没有目标达成对比、
//   没有异常分解**，三个决策分析的必要件一个都不具备。
//   真正的分析页是 `/dashboards/operation`（四时间粒度 + 全量人效 + 原因Top10），
//   已同步正名为「运营分析看板」。
//   ⚠️ 保留「今日」但主名固定不随视图跳动（原「本周看板」的写法会让使用者
//      以为是两个页面），日期/周次由标题右侧 `displayDate` 承载。
  const pageTitle = '今日值班看板'

// 选中的日期/周对应的时间范围 (传给后端 start/end)
  function getRange() {
    if (viewMode.value === 'week') {
      const { start, end } = getWeekRange()
      return {
        start: start.format('YYYY-MM-DD'),
        end: end.format('YYYY-MM-DD'),
      }
    }
    const d = dayjs(selectedDate.value)
    return {
      start: d.startOf('day').format('YYYY-MM-DD'),
      end: d.endOf('day').format('YYYY-MM-DD'),
    }
  }

  function onDateChange() {
    // 切换日期后重新加载全部数据
    loadAll()
  }

  function onViewModeChange() {
    loadAll()
  }

// KPI卡片
  const kpiCards = computed(() => {
    const d = kpiData.value
    // 后端 today_kpi 返回嵌套结构 {value, yoy_value, yoy_pct, ...}
    const reception = d.reception || {}
    const refundCnt = d.refund_count || {}
    const refundAmt = d.refund_amount || {}
    const complaint = d.complaint || {}
    const staffOnDuty = d.staff_on_duty || {}
    const recovery = recoveryData.value?.kpi || {}
    const recoveryRate = recovery.success_rate != null && !Number.isNaN(Number(recovery.success_rate))
      ? Number(recovery.success_rate).toFixed(1) + '%'
      : '-'
    return [
      {
        key: 'reception',
        label: '接待数',
        displayValue: formatNum(reception.device_count),
        icon: 'Service',
        colorClass: 'kpi-blue',
        // ★ #78: 按 SN(接待数)口径分层 —— 含 AI 独有的 SN
        stack: buildStack(reception.breakdown, 'devices'),
        yoy: (reception.device_count_yoy_pct != null && reception.device_count_yoy_pct !== 0)
          ? `${reception.device_count_yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(reception.device_count_yoy_pct).toFixed(1)}%`
          : '',
        yoyClass: (reception.device_count_yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
      },
      {
        key: 'session',
        label: '会话数',
        displayValue: formatNum(reception.value),
        icon: 'ChatDotRound',
        colorClass: 'kpi-green',
        // ★ #78: 按会话口径分层（含 AI 全自动接待）
        stack: buildStack(reception.breakdown, 'sessions'),
        yoy: (reception.yoy_pct != null && reception.yoy_pct !== 0)
          ? `${reception.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(reception.yoy_pct).toFixed(1)}%`
          : '',
        yoyClass: (reception.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
      },
      {
        key: 'refund_count',
        label: '退款笔数',
        displayValue: formatNum(refundCnt.value),
        icon: 'Warning',
        colorClass: 'kpi-red',
        yoy: (refundCnt.yoy_pct != null && refundCnt.yoy_pct !== 0)
          ? `${refundCnt.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(refundCnt.yoy_pct).toFixed(1)}%`
          : '',
        yoyClass: (refundCnt.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
      },
      {
        key: 'refund_amount',
        label: '退款金额',
        displayValue: '¥' + formatMoney(refundAmt.value),
        icon: 'Money',
        colorClass: 'kpi-orange',
        yoy: (refundAmt.yoy_pct != null && refundAmt.yoy_pct !== 0)
          ? `${refundAmt.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(refundAmt.yoy_pct).toFixed(1)}%`
          : '',
        yoyClass: (refundAmt.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
      },
      // ★ #309 充值卡: 真实现金流充入(剔除工厂质检套餐流水), 与分品类运营看板 #293 同判据
      {
        key: 'recharge',
        label: '充值金额',
        displayValue: '¥' + formatMoney(d.recharge?.value),
        icon: 'Coin',
        colorClass: 'kpi-green',
        sub: `${formatNum(d.recharge?.count)} 笔`,
        yoy: (d.recharge?.yoy_pct != null && d.recharge?.yoy_pct !== 0)
          ? `${d.recharge.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(d.recharge.yoy_pct).toFixed(1)}%`
          : '',
        yoyClass: (d.recharge?.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
      },
      {
        key: 'complaint',
        label: '投诉数',
        displayValue: formatNum(complaint.value),
        icon: 'Bell',
        colorClass: 'kpi-purple',
        yoy: (complaint.yoy_pct != null && complaint.yoy_pct !== 0)
          ? `${complaint.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(complaint.yoy_pct).toFixed(1)}%`
          : '',
        yoyClass: (complaint.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
      },
      {
        key: 'recovery',
        label: '退款挽回',
        displayValue: recoveryRate,
        icon: 'RefreshLeft',
        colorClass: 'kpi-cyan',
        yoy: '',
        yoyClass: '',
      },
      {
        key: 'on_duty',
        label: '值班人员',
        displayValue: `${formatNum(staffOnDuty.value)}人`,
        icon: 'User',
        colorClass: 'kpi-teal',
        yoy: '',
        yoyClass: '',
      },
    ]
  })

// 值班组长/主管 (仅显示岗位为主管/组长/副主管的人员; 注意: is_management=true不仅包含管理岗,
// 还包含投诉/退款挽回等享有跨类豁免的专项岗, 不能直接用is_management判断值班组长)
  const DUTY_POSITIONS = ['主管', '组长', '副主管']

/** ★ 岗位 → 值班徽标（用户 2026-09-14：「组长」/「管理」要一眼可辨）
 *
 *  设计要点:
 *   · 主管级 → 文案「管理」（管理视角的词，而非职级名）
 *   · 组长/副主管 → 文案「组长」（副主管在值班语境下同属组级管理者）
 *   · 客服/投诉专员/退款处理专员 → 不进值班条（非决策岗）
 *   ⚠️ 与 `DUTY_POSITIONS` 必须保持同源 —— 两边不一致会出现
 *      "徽标为空的芯片"或"该显示却查不到标签"。
 */
  const DUTY_ROLE_META = {
    主管: { label: '管理', tagType: 'danger', bg: '#b91c1c' },
    副主管: { label: '组长', tagType: 'warning', bg: '#b45309' },
    组长: { label: '组长', tagType: 'primary', bg: '#1d4ed8' },
  }

  const dutyLeaders = computed(() => {
    const staff = staffData.value?.data || []
    return staff
      .filter(s => DUTY_POSITIONS.includes(s.position) && !s.is_virtual)
      .map(s => {
        const meta = DUTY_ROLE_META[s.position] || {}
        return {
          name: s.name,
          group: s.group,
          department: s.department,
          position: s.position,
          roleLabel: meta.label || '',
          tagType: meta.tagType || 'info',
          roleBg: meta.bg || '#6b7280',
        }
      })
  })

// 四路经营退款路线配色 (A 线上直退 / B 钱包取现 / C 闪猫退款 / D 手动退款)
  const REFUND_ROUTES = [
    { key: 'a', label: 'A线上直退', short: 'A直退', color: '#ef4444' },
    { key: 'b', label: 'B钱包取现', short: 'B取现', color: '#f97316' },
    { key: 'c', label: 'C闪猫退款', short: 'C闪猫', color: '#a855f7' },
    { key: 'd', label: 'D手动退款', short: 'D手动', color: '#94a3b8' },
  ]

  function _rrLabel(r) { return isMobile.value ? (r.short || r.label) : r.label }

// ★ #85 投诉两序列配色（登记=橙柱 / 处理=深青线）
//   登记用暖色（“进来了”）、处理用冷青色（“解决了”）—— 直觉上区分"进"与"出"。
//   ⚠️ 处理线**不能用 #10b981** —— 那是「接待数」柱的颜色，
//     同色会导致同屏两根“绿色”无法区分（实测像素采样发现），
//     故选 teal-700 (#0f766e)，与全图现有色均不撞。
  const COMPLAINT_COLORS = { registered: '#f59e0b', handled: '#0f766e' }

// 将四路退款拆分 + 工作量/投诉 组织成 ECharts series
  function buildTrendSeries(data) {
    const groups = trendSelectedGroups.value
    const series = []
    if (groups.includes('reception')) {
      series.push({ name: isMobile.value ? '会话' : '会话数', type: 'bar', data: data.reception || [], itemStyle: { color: '#3b82f6' }, barMaxWidth: isMobile.value ? 14 : 20 })
      series.push({ name: isMobile.value ? '接待' : '接待数', type: 'bar', data: data.reception_devices || [], itemStyle: { color: '#10b981' }, barMaxWidth: isMobile.value ? 14 : 20 })
    }
    if (groups.includes('complaint')) {
      // ★ #85: 投诉改为 **柱(登记) + 线(处理)**（用户 2026-09-14 选定）
      //   为什么拆：原单柱把「登记」与「处理」混为一种含义，主管无法判断
      //   「今天进的多」还是「今天清的多」—— 两者要采取的动作完全不同。
      //   口径（与后端同源，勿在前端另算）:
      //     · complaint      = 登记（工单 register_time 落桶，三源分桶，**不含卡商**）
      //     · complaint_done = 处理（finish_time 落桶 + 真实完成 ≥3min，**含往期单办结**）
      //   ★ 不写「投诉量」—— 避免又产生第三种语义（#85 的核心教训）
      series.push({
        name: isMobile.value ? '登记' : '投诉登记',
        type: 'bar', data: data.complaint || [],
        itemStyle: { color: COMPLAINT_COLORS.registered },
        barMaxWidth: isMobile.value ? 14 : 20,
      })
      series.push({
        name: isMobile.value ? '处理' : '投诉处理',
        type: 'line', data: data.complaint_done || [],
        smooth: true, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: COMPLAINT_COLORS.handled },
        lineStyle: { width: 2, color: COMPLAINT_COLORS.handled },
      })
    }
    if (groups.includes('refund')) {
      REFUND_ROUTES.forEach(r => {
        // 退款笔数: 四路堆叠柱
        series.push({ name: _rrLabel(r), type: 'bar', stack: 'refund_cnt', data: data[`refund_count_${r.key}`] || [], itemStyle: { color: r.color }, barMaxWidth: isMobile.value ? 14 : 20 })
      })
      REFUND_ROUTES.forEach(r => {
        // 退款金额: 四路堆叠面积线 (名字后不带 legend, 单独在 tooltip 体现)
        series.push({ name: `${_rrLabel(r)}·金额`, type: 'line', stack: 'refund_amt', yAxisIndex: 1, data: data[`refund_amount_${r.key}`] || [], smooth: true, itemStyle: { color: r.color }, lineStyle: { width: 1 }, areaStyle: { opacity: 0.12 } })
      })
    }
    return series
  }

  function buildTrendLegend() {
    const groups = trendSelectedGroups.value
    const items = []
    if (groups.includes('reception')) {
      items.push(isMobile.value ? '会话' : '会话数')
      items.push(isMobile.value ? '接待' : '接待数')
    }
    if (groups.includes('complaint')) {
      // ★ #85: 图例写「登记」「处理」——**不写"投诉量"**，避免第三种语义
      items.push(isMobile.value ? '登记' : '投诉登记')
      items.push(isMobile.value ? '处理' : '投诉处理')
    }
    if (groups.includes('refund')) {
      REFUND_ROUTES.forEach(r => items.push(_rrLabel(r)))
    }
    return items
  }

// 手机上合并 0~8点 (首桶 0~7 点合成一个), 展示 0~8点 + 08:00~23:00, 更适配窄屏
  function mergeMobileTrend(data) {
    const labels = data?.hour_labels || data?.slot_labels || []
    const MERGE_N = 8
    const sumHead = (arr) => {
      if (!Array.isArray(arr)) return 0
      return arr.slice(0, MERGE_N).reduce((s, v) => s + (typeof v === 'number' ? v : 0), 0)
    }
    const tail = (arr) => (Array.isArray(arr) ? arr.slice(MERGE_N) : [])
    return {
      labels: ['0~8点', ...(Array.isArray(labels) ? labels.slice(MERGE_N) : [])],
      reception: [sumHead(data.reception), ...tail(data.reception)],
      reception_devices: [sumHead(data.reception_devices), ...tail(data.reception_devices)],
      complaint: [sumHead(data.complaint), ...tail(data.complaint)],
      // ★ #85: 处理序列同样需合并（否则手机端处理线为空）
      complaint_done: [sumHead(data.complaint_done), ...tail(data.complaint_done)],
      refund_count_a: [sumHead(data.refund_count_a), ...tail(data.refund_count_a)],
      refund_count_b: [sumHead(data.refund_count_b), ...tail(data.refund_count_b)],
      refund_count_c: [sumHead(data.refund_count_c), ...tail(data.refund_count_c)],
      refund_count_d: [sumHead(data.refund_count_d), ...tail(data.refund_count_d)],
      refund_amount_a: [sumHead(data.refund_amount_a), ...tail(data.refund_amount_a)],
      refund_amount_b: [sumHead(data.refund_amount_b), ...tail(data.refund_amount_b)],
      refund_amount_c: [sumHead(data.refund_amount_c), ...tail(data.refund_amount_c)],
      refund_amount_d: [sumHead(data.refund_amount_d), ...tail(data.refund_amount_d)],
    }
  }

// 趋势图
  const trendOption = computed(() => {
    const raw = hourlyData.value || {}
    const data = isMobile.value ? mergeMobileTrend(raw) : raw
    const labels = data?.hour_labels || data?.labels || data?.slot_labels || data?.hours || []
    const mob = isMobile.value
    const showRefund = trendSelectedGroups.value.includes('refund')
    // #233 B1.5-2 + #234 B1.5-3 注入事件标记:
    //   点事件 → markPoint; 区间事件 → markLine + markArea(背景高亮, 视觉对照)
    // kind: 日视图=hourly (labels 形如 'HH:mm'); 周视图=daily (labels 形如 'MM-DD')
    const seriesList = buildTrendSeries(data)
    if (seriesList.length) {
      const marks = buildIncidentMarks(labels, { kind: viewMode.value === 'week' ? 'daily' : 'hourly' })
      // 注入到第一个 series(它是主接待量, 与事件影响最直接相关)
      seriesList[0] = {
        ...seriesList[0],
        markPoint: marks.markPoint,
        markLine: marks.markLine,
        markArea: marks.markArea,
      }
    }
    return {
      tooltip: { trigger: 'axis' },
      legend: {
        data: buildTrendLegend(),
        top: 0,
        type: mob ? 'scroll' : 'plain',
        itemWidth: 10,
        itemHeight: 8,
        itemGap: mob ? 6 : 12,
        textStyle: { fontSize: mob ? 10 : 12 },
        pageIconColor: '#94a3b8',
        pageTextStyle: { color: '#6b7280', fontSize: 10 },
      },
      grid: mob
        ? { left: 28, right: showRefund ? 40 : 16, bottom: 16, top: 24, containLabel: true }
        : { left: 40, right: showRefund ? 60 : 20, bottom: 30, top: 40 },
      xAxis: {
        type: 'category',
        data: labels,
        axisLabel: { fontSize: mob ? 9 : 11, interval: 0, rotate: mob ? 30 : 0 },
      },
      yAxis: [
        {
          type: 'value',
          name: mob ? '' : '数量',
          position: 'left',
          axisLabel: { fontSize: mob ? 9 : 11 },
          splitLine: { lineStyle: { type: 'dashed', opacity: 0.4 } },
        },
        {
          type: 'value',
          name: mob ? '' : '金额(元)',
          position: 'right',
          show: showRefund,
          axisLabel: { fontSize: mob ? 9 : 11 },
          splitLine: { show: false },
        },
      ],
      series: seriesList,
    }
  })

// 判定"企微人员":部门为"企微" / 姓名/别名前缀为"企微-" / qw_user_id 已绑定
// (用于人均效率默认过滤, 比严格 qw_user_id 非空更贴近业务语义: 部门=企微就展示)
  function isQwStaff(s) {
    if (!s) return false
    if ((s.qw_user_id || '').trim()) return true
    if ((s.department || s.dept || '') === '企微') return true
    const nm = (s.name || s.op || s.display_name || '').trim()
    return /^企微[-_·\s]?/.test(nm)
  }

// 工作效率表: 各专项接待明细列 (按类型分开SN/接待数)
  const SPECIALTY_COLS = [
    { key: '咨询接待', short: '咨询', color: '#3b82f6' },
    { key: '套餐专项', short: '套餐', color: '#f97316' },
    { key: '退款处理', short: '处理', color: '#ef4444' },
    { key: '退款挽回', short: '挽回', color: '#06b6d4' },
    { key: '内部投诉', short: '内诉', color: '#7c3aed' },
    { key: '外部投诉', short: '外诉', color: '#dc2626' },
  ]

// 从breakdown取某专项的接待数(个/SN去重后)
  function getSpecialtyPeople(row, catKey) {
    const bd = row.breakdown || {}
    const info = bd[catKey]
    return info ? Number(info.people || 0) : 0
  }

// 人均效率 (全量排序, 默认仅企微, 勾选后显示所有人)
  const topStaffList = computed(() => {
    let items = staffData.value?.data || []
    items = items.filter(s => !s.is_virtual)
    // 只要有任意业务数据(会话或接待)就保留 (避免空行占位 与后端ops_union语义一致)
    items = items.filter(s => (Number(s.reception_count) || 0) + (Number(s.reception_device_count) || 0) + (Number(s.rc) || 0) + (Number(s.rdc) || 0) > 0)
    if (!showAllStaff.value) {
      items = items.filter(isQwStaff)
    }
    // 默认按接待效率(eff_score)降序排名
    items = [...items].sort((a, b) => (Number(b.eff_score) || 0) - (Number(a.eff_score) || 0))
    return items
  })

// ★ #78 AI / 系统账户单列（用户明确：「区分出来，单列也可以」）
//   设计说明: 此前 `is_virtual` 的账户被 `topStaffList` 直接**过滤丢弃**，
//   导致 AI 的工作量在生产上完全不可见（只能从「会话数」虚高中间接感知）。
//   这里改为**单列展示**：AI 仍在参与工作，管理者需要看到它干了多少。
//   取舍: 不并入真人排名（会挤掉真人），也不隐藏（会看不见 AI 规模），
//   而是单独一张小表 —— 两者都能看到，互不干扰。
  const virtualStaffList = computed(() => {
    const items = staffData.value?.data || []
    return items
      .filter(s => s.is_virtual)
      .filter(s => (Number(s.reception_count) || 0) + (Number(s.reception_device_count) || 0) > 0)
      .sort((a, b) => (Number(b.reception_count) || 0) - (Number(a.reception_count) || 0))
  })

/** 该账户的来源标签/配色（与 KPI 卡 SOURCE_KINDS 同一套色） */
/** 该 AI/系统账户名下会话数占全站会话数的百分比（可控指标：AI 接管率） */
  function aiSharePct(row) {
    const total = Number(kpiData.value?.reception?.value || 0)
    if (!total) return '0.0'
    return (Number(row.reception_count || 0) * 100 / total).toFixed(1)
  }

// 部门列显示策略:移动端且仅企微时隐藏(表头已筛选); 显示所有人时保留以便区分
  const showDeptColumn = computed(() => !isMobile.value || showAllStaff.value)

// 退款挽回SN列表 (按时间倒序)
  const recoverySnList = computed(() => {
    const details = recoveryData.value?.sn_details || []
    return details.map(d => ({
      sn: d.sn,
      result: d.result,
      staff: d.staff,
      group: d.staff_group,
      reason: d.reason,
      time: d.time,
      amount: d.amount,
      status: d.status,
      src: d.src,
    })).sort((a, b) => {
      const ta = dayjs(a.time).valueOf()
      const tb = dayjs(b.time).valueOf()
      return tb - ta
    })
  })

// 退款SN筛选选项
  const refundHandlerOptions = computed(() => {
    const set = new Set()
    recoverySnList.value.forEach(d => { if (d.staff) set.add(d.staff) })
    return Array.from(set).sort()
  })

  const refundReasonOptions = computed(() => {
    const set = new Set()
    recoverySnList.value.forEach(d => { if (d.reason) set.add(d.reason) })
    return Array.from(set).sort()
  })

  const refundFilteredList = computed(() => {
    return recoverySnList.value.filter(d => {
      if (refundFilterResult.value && d.result !== refundFilterResult.value) return false
      if (refundFilterHandler.value && d.staff !== refundFilterHandler.value) return false
      if (refundFilterReason.value && d.reason !== refundFilterReason.value) return false
      return true
    })
  })

// 退款原因分布: 堆叠柱状图(成功+失败)
  const recoveryReasonStackOption = computed(() => {
    // 从 sn_details 聚合成功/失败数
    const map = {}
    recoverySnList.value.forEach(d => {
      const r = d.reason || '未知'
      if (!map[r]) map[r] = { success: 0, failed: 0 }
      if (d.result === 'success') map[r].success++
      else map[r].failed++
    })
    const entries = Object.entries(map).sort((a, b) => (b[1].success + b[1].failed) - (a[1].success + a[1].failed))
    const x = entries.map(e => e[0])
    const successData = entries.map(e => e[1].success)
    const failedData = entries.map(e => e[1].failed)
    const total = successData.reduce((s, v) => s + v, 0) + failedData.reduce((s, v) => s + v, 0)
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const reason = params[0]?.axisValue || ''
          let html = `<b>${reason}</b><br/>`
          let tot = 0
          params.forEach(p => {
            const v = p.value || 0
            tot += v
            const marker = `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${p.color};margin-right:4px"></span>`
            html += `${marker}${p.seriesName}: ${v} 单<br/>`
          })
          const pct = total ? ((tot / total) * 100).toFixed(1) : '0.0'
          html += `<span style="color:#909399">合计: ${tot} 单 (${pct}%)</span>`
          return html
        },
      },
      legend: { data: ['成功挽回', '挽回失败'], bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
      grid: { left: 8, right: 8, bottom: 28, top: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: x,
        axisLabel: { fontSize: 10, interval: 0, rotate: x.length > 4 ? 20 : 0 },
      },
      yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      series: [
        {
          name: '成功挽回', type: 'bar', stack: 'total', data: successData,
          itemStyle: { color: '#22c55e', borderRadius: [0, 0, 0, 0] },
          barMaxWidth: 24,
          label: { show: true, position: 'inside', fontSize: 9, color: '#fff', formatter: (p) => p.value > 0 ? p.value : '' },
        },
        {
          name: '挽回失败', type: 'bar', stack: 'total', data: failedData,
          itemStyle: { color: '#ef4444', borderRadius: [3, 3, 0, 0] },
          barMaxWidth: 24,
          label: { show: true, position: 'inside', fontSize: 9, color: '#fff', formatter: (p) => p.value > 0 ? p.value : '' },
        },
      ],
    }
  })

// 成功率颜色
  const successRateColor = computed(() => {
    const rate = Number(recoveryData.value?.kpi?.success_rate || 0)
    if (rate >= 38) return { background: '#f0fdf4' }
    if (rate >= 30) return { background: '#fefce8' }
    return { background: '#fef2f2' }
  })

// 加载数据
// 前端内存缓存: 切回同窗口直接用上次结果(历史数据不变化), 不发请求秒开。
// 仅「日视图·今天」手动/自动刷新走 force=true 强制重拉。
  async function loadAll({ force = false } = {}) {
    // 命中内存缓存且非强制刷新 → 直接渲染(不发网络请求)
    if (!force && !_forceRefresh() && _viewCache && _viewCache.key === _viewCacheKey()) {
      applyViewData(_viewCache.data)
      return
    }
    loading.value = true
    try {
      const { start, end } = getRange()
      const params = { start, end }
      const grain = viewMode.value === 'week' ? 'day' : 'hour'

      const [kpiRes, hourlyRes, staffRes, queueRes, recoveryRes, complaintSummaryRes, complaintDetailRes, backlogRes] = await Promise.all([
        today.kpi(params),
        today.hourlyTrend({ ...params, trend_grain: grain }),
        today.staff({ ...params, aggregate_by: 'person', topn: 200 }),
        today.queue(params),
        today.refundRecovery(params),
        complaintSummary(params),
        complaintDetail({ ...params, limit: viewMode.value === 'week' ? 1000 : 500, include_supplementary: true }),
        // 积压水位: 固定近30天窗口(登记日期落在窗口内的未完成), 避免历史遗留无限堆叠
        complaintBacklog({ start: dayjs().subtract(30, 'day').format('YYYY-MM-DD'), end: dayjs().format('YYYY-MM-DD') }),
      ])

      // #233 B1.5-2 时间轴事件标记: 与数据并行加载(不阻塞主数据, 失败也不影响主流程)
      loadIncidentMarkers({ start, end }).catch(() => {})

      const data = { kpiRes, hourlyRes, staffRes, queueRes, recoveryRes, complaintSummaryRes, complaintDetailRes, backlogRes }
      _viewCache = { key: _viewCacheKey(), data }
      applyViewData(data)
      
      lastUpdate.value = dayjs().format('HH:mm:ss')
    } catch (e) {
      console.error('加载今日数据失败', e)
    } finally {
      loading.value = false
    }
  }

// 把一次加载结果应用到各响应式状态
  function applyViewData({ kpiRes, hourlyRes, staffRes, queueRes, recoveryRes, complaintSummaryRes, complaintDetailRes, backlogRes }) {
    if (kpiRes.data) kpiData.value = kpiRes.data
    if (hourlyRes.data) hourlyData.value = hourlyRes.data
    if (staffRes.data) staffData.value = staffRes.data
    if (queueRes.data) queueData.value = queueRes.data
    if (recoveryRes.data) recoveryData.value = recoveryRes.data
    if (complaintSummaryRes.data) complaintSummaryData.value = complaintSummaryRes.data
    if (complaintDetailRes.data) {
      complaintList.value = complaintDetailRes.data?.items || []
      // F-P1-2: 明细窗口 登记/处理 计数(标题展示)
      const d = complaintDetailRes.data
      complaintDetailCount.value = {
        registered: Number(d.registered) || 0,
        handled: Number(d.handled) || 0,
        total: Number(d.total) || (d.items?.length || 0),
      }
    }
    if (backlogRes.data) backlogTotal.value = backlogRes.data?.total || 0
  }

  // 统一轮询层(S-P1-2): 今日自动刷新, 页面隐藏暂停 + 卸载自动停止
  const { startPolling } = usePolling()

  function _onMediaChange(e) {
    isMobile.value = e.matches
  }

  onMounted(() => {
    _mq = window.matchMedia('(max-width: 768px)')
    isMobile.value = _mq.matches
    _mq.addEventListener('change', _onMediaChange)
    loadAll()
    // 仅「日视图·今天」时每60秒自动刷新; 周视图/历史日期数据不需要轮询。
    // force=true: 今天是实时窗口, 自动刷新必须真正重拉(不走内存缓存)。
    startPolling('today:auto-refresh', () => {
      if (viewMode.value === 'day' && isToday.value) loadAll({ force: true })
    }, POLL_INTERVALS.TODAY_AUTO)
  })

  onBeforeUnmount(() => {
    // 轮询任务由 usePolling 同生命周期钩子停止
    if (_mq) {
      _mq.removeEventListener('change', _onMediaChange)
      _mq = null
    }
  })

  return {
    loading,
    lastUpdate,
    isMobile,
    _mq,
    viewMode,
    // #233 B1.5-2 + #234 B1.5-3 时间轴标注 + 事件影响对照
    incidentMarkers,
    incidentMarkerMap,
    activeIncidentDetail,
    incidentDetailVisible,
    incidentImpactComparison,
    incidentImpactComparisonRows,
    incidentImpactLoading,
    onChartClick,
    kpiData,
    hourlyData,
    staffData,
    queueData,
    recoveryData,
    hasRecovery,
    complaintSummaryData,
    complaintList,
    complaintDetailCount,
    complaintCollapseActive,
    backlogTotal,
    showAllStaff,
    trendSelectedGroups,
    openDeviceAudit,
    openPersonAudit,
    recoveryCollapseActive,
    refundFilterResult,
    refundFilterHandler,
    refundFilterReason,
    COMPLAINT_SOURCE_OPTIONS,
    complaintFilterSource,
    currentPolicies,
    formatNum,
    formatShortTime,
    formatMoney,
    scoreTagType,
    complaintSourceType,
    complaintStatusType,
    complaintRegHandledOption,
    complaintCategoryOption,
    complaintSourceColumnFilters,
    complaintCategoryColumnFilters,
    complaintEntityColumnFilters,
    complaintStatusColumnFilters,
    complaintFilteredList,
    sortTimeStr,
    supplExternal,
    supplSummaryText,
    supplCard,
    cardData,
    supplTooltip,
    INCIDENT_STATUS,
    incidentStatusLabel,
    incidentTagType,
    activeIncidents,
    hasHighSeverityIncident,
    impactBannerTitle,
    goToImpactSection,
    router,
    drillRange,
    drillToComplaint,
    drillToBacklog,
    drillToRefundRecovery,
    drillToOperation,
    onKpiClick,
    COMPLAINT_DRILL_SOURCE,
    onComplaintSourceClick,
    TASK_STATUS,
    taskStatusLabel,
    selectedDate,
    isToday,
    _viewCacheKey,
    _viewCache,
    _forceRefresh,
    getWeekRange,
    isCurrentWeek,
    showRealtimeTag,
    displayDate,
    pageTitle,
    getRange,
    onDateChange,
    onViewModeChange,
    kpiCards,
    DUTY_POSITIONS,
    DUTY_ROLE_META,
    dutyLeaders,
    REFUND_ROUTES,
    _rrLabel,
    COMPLAINT_COLORS,
    buildTrendSeries,
    buildTrendLegend,
    mergeMobileTrend,
    trendOption,
    isQwStaff,
    SPECIALTY_COLS,
    getSpecialtyPeople,
    topStaffList,
    virtualStaffList,
    aiSharePct,
    showDeptColumn,
    recoverySnList,
    refundHandlerOptions,
    refundReasonOptions,
    refundFilteredList,
    recoveryReasonStackOption,
    successRateColor,
    loadAll,
    applyViewData,
    _onMediaChange,
  }
}
