// 由 ComplaintAnalysis.vue 的 <script setup> 拆分而来(issue #184)。
// 模板零改动: 全部顶层绑定名经 return 回注 .vue 的 setup 解构。
// 组件与图标 import 留在 .vue 内注册(模板解析需要); 依赖 import 在此独立声明。

// #76 下钻落点: 读取来源页传来的窗口/定位参数
// ── 两级导航状态(#67 P1) ──
// analysis(看趋势做判断) | execute(盯人干活)
// overview | channel | subject
// internal | merchant | external | card
/** 由新导航**派生**旧 activeTab —— 保持既有 section 的 v-if 不动，降低回归风险。
 *  ⚠️ 这是过渡期写法：P2 渠道四页同构后，v-if 会由 channelTab 直接驱动。 */
// ── 统一时间条(#67 P1): 全页唯一时间状态 ──
// ── 决策概览数据(#67 P1: 单请求出一屏) ──
// F-P2-4: 被投诉主体下钻抽屉 state
// 移动端响应式: 简化描述文字/控件
// 趋势图粒度(按日/周/月): 周=ISO自然周, 月=自然月; 前端对后端按日数据做汇聚
// F-P1-2: 明细 登记/处理 计数(标题展示)
// ── 积压审核(backlog tab) ──
// ── ★ 2026-09-11 (#67 P3): 数据治理 state 已随页面外迁 ──
//   「人工审核(#55)」与「采集质量(#60)」→ 系统管理 → 投诉采集质量
//   新入口: /system/complaint-quality (见 web/src/views/management/ComplaintDataQuality.vue)
// ── 被投诉主体分析(subject tab) ──
// ★ #67 P2: 主体筛选状态(默认与改造前一致: 全部 / 按总量 / Top20 / 不对比)
//   "Top20" 是后端不传 top_n 时的旧行为 —— 前端显式传 20 保持等价。
// ── 积压快照(决策概览) helpers ──
// 来源×龄期堆叠柱: x=来源, y=数量, 5个龄期桶堆叠
// 投诉原因分布(按内容关键词归类, 替代区分度低的来源子类分类)
// 严重性数量分布(堆叠柱状图, 显示商户号vs外部投诉占比)
// 投诉升级链(按SN, 内部→商户号→外部 传递性)
// 按升级路径排序: 先展示"从内部起步"的链, 再是直发/组合
// 主体投诉趋势(商户号+外部 堆叠面积; 支持按日/周/月汇聚)
// Top5 被投诉主体趋势(堆叠面积, 支持按日/周/月汇聚; Top5按汇聚总量重新排序)
// 各主体 {day: count}
// Top5: 按当前粒度下的汇聚总量降序取前5
// ★★ 2026-09-11 (#53): (外)卡商 **独立统计** ★★
//   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
//   后端 `card` 对象与 `reg_handled` **平行**(非其一部分), 故单独取值。
// ── 投诉水位(进水/出水 并列柱 + 净水位/累计净水位 折线) ──
// 新增投诉 vs 处理投诉 = 此消彼长的比较关系: 并列柱(不同颜色, 不堆叠)
// ── 请求参数(统一由时间条驱动, #67 P1 移除了页面内散落的粒度开关) ──
// ── 决策概览数据加载(#67 P1) ──
// ── 渠道同构面板(#67 P2) ──
//   四个渠道共用一份数据契约：`/summary` 的 internal/merchant/external/card 分源对象。
//   ⚠️ 卡商(card) 是**独立体系**(#53)，不参与投诉总量，但它与其它三源**同结构**返回，
//      因此可以被同一个面板渲染 —— 这正是"同构"的价值。
/** 加载 /summary —— 渠道面板的分类/处理人/趋势数据源 */
/** 该渠道的分源数据对象 */
/** 趋势粒度：与顶部时间条的"自动"保持一致(≤31天=日 / 32~120天=周 / >120天=月) */
/** 渠道 → 明细接口的 source_type 映射
 *  ⚠️ 卡商在明细接口里的 source_type 是 `(外)卡商`(带括号)，与内部 tab key 不同 */
// ── 概览: 结论条(点击下钻到对应页) ──
// ── 概览: KPI 卡(5 张 + 环比) ──
// ── 概览: 总量堆积趋势(内/商/外 三源堆叠; 卡商独立不并入) ──
// ── 概览: (外)卡商独立趋势(不并入上方堆叠) ──
// ── 概览: 登记 vs 处理(对称水位) ──
// ── 概览: 涉诉金额趋势(新增) ──
// ── 概览: 积压龄期分布 ──
// ── A5 · 投诉登记时段热力图（#72） ──
//   x = 小时(0-23)，y = 星期(周一~周日)；色深 = 登记量
//   ★ 用 heatmap 而非折线：时段节律是"二维"的（小时 × 星期），
//     折线只能表一维，会丢掉"周末/工作日差异"这一半信息。
// echarts heatmap: [x(小时), y(星期索引), value]
// weekday 1..7 → 0..6
/** 峰谷提示文案（把"热力图"翻译成"结论" —— 否则使用者要自己看色块推断） */
// ── A2 · 反复投诉（#72） ──
//   ★ 后端已按「事件口径」算好（SN + 北京日期去重），前端只渲染，不做二次计数。
// ── 来源分布饼图(含兜底: source_distribution 为空时用 summary 三源数) ──
// ── 外部投诉平台分布饼图 ──
// ── 商户号被投诉主体趋势(堆叠面积, 商户号内细分; 支持按日/周/月汇聚) ──
// ── 趋势粒度汇聚工具: 把按日序列(day→count)按周/月聚合成带标签的序列 ──
// 多序列汇聚: series 形如 { name: {day: count} }, 返回 { names, buckets, byName(values 对齐 buckets) }
// 统一 key 集合(并集, 升序)
// key → label 映射(取任一存在的 label)
// 每个序列对齐到统一 buckets(缺失补0)
// ── 加载数据 ──
// 时间范围变化 → 积压分页重置
// ★ 概览改用专用接口单请求出一屏(#67 P1):
//   原概览需并发 8 个请求且各接口时间口径不一致 → 同屏数字打架。
// 渠道面板的数据源(分类/处理人/趋势)
// 同时加载明细
// ★ #67 P2: 主体筛选参数(来源/排序/TopN/环比)
//   comparePrev 勾选时才传 compare=prev —— 环比需算对比期，不必每次默认开启。
//   ⚠️ 环比需要「时间窗口」才能算对比期 —— 主体页默认无窗口时
//     自动补一个近 30 天窗口，否则 delta 恒为 None(实测踩到: 列全是 —)。
/** 环比开关变化 —— 立即重拉(排序若选 delta 也依赖它) */
/** 筛选后命中的主体总数(供前端显示 "命中 N 个主体") */
// F-P2-4: 主体下钻 - 打开抽屉并拉该主体明细(detail 支持 subject 归一化过滤)
// 兼容 detail 返回 items; 未命中 source 参数时全三源(内部投诉无主体不返回)
// 时间字符串排序(ISO/MM-DD HH:mm), 空值排后
// 时间窗口: 默认近30天, 7/90/自定义跟随页面筛选(仅统计窗口内登记的未完成)
// 导出积压明细 Excel(与 /backlog 同口径, 含当前筛选, 不受分页限制)
// ★ 初始区间(#67 P1): 默认近 30 天(含今日)。
//   ⚠️ 必须在此初始化 —— 时间条是受控组件，不自己持有区间；
//      缺了它 range 恒为空 → 概览/环比/明细全部拿不到窗口(实测踩到)。
//
// ★★ #76 下钻落点: 先读 query 覆盖默认值 ★★
//   规范依据: docs/standards/看板卡片数据说明与下钻规范.md §3.3
//   「下钻必须带上下文」—— 因此本页必须能**接收**上下文，否则从看板点过来
//   会落到默认近30天，与刚才看到的窗口不一致，数字对不上 = 下钻失效。
//   支持参数: start / end / tab(analysis|execute) / view(overview|channel|subject)
//             / source(internal|merchant|external|card)
// ★ 带窗口下钻: 必须把时间条切到「自定义」并回填区间，
//   否则 range 虽被覆盖、**控件仍显示「近30天」** → 使用者看到"区间显示 8/15~9/13、
//   数据却是 9/13 当天"，比不做下钻更糟（数字与标签对不上 = 误导）。
//   这是"受控组件"的必然后果：状态有第二处（rangePreset/customRange）就必须同步。
// 定位到指定 tab（无参数时保持既有默认：分析 → 决策概览）
// ★ 切换渠道 → 只重新拉该渠道明细(概览/summary 已按区间一次性加载，不必重取)。
//   去掉原来「每切一个 tab 触发全页重载」的行为，减少无效请求。
// 从概览下钻到渠道时，也要拉明细(此时 channelTab 可能未变化)

import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import { complaintAnalysis, complaintOverview, complaintSummary, complaintDetail, complaintSupplementary, complaintBacklog, complaintBacklogExport, complaintSubjectAnalysis, createAudit } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'

export function useComplaintAnalysis() {
  dayjs.extend(isoWeek)

  const { openDeviceAudit, openPersonAudit } = useAudit()

// #76 下钻落点: 读取来源页传来的窗口/定位参数
  const route = useRoute()

  const loaded = ref(false)

  const loading = ref(false)

// ── 两级导航状态(#67 P1) ──
  const topTab = ref('analysis')

// analysis(看趋势做判断) | execute(盯人干活)
  const analysisTab = ref('overview')

// overview | channel | subject
  const channelTab = ref('external')

// internal | merchant | external | card
/** 由新导航**派生**旧 activeTab —— 保持既有 section 的 v-if 不动，降低回归风险。
 *  ⚠️ 这是过渡期写法：P2 渠道四页同构后，v-if 会由 channelTab 直接驱动。 */
  const activeTab = computed(() => {
    if (topTab.value === 'execute') return 'backlog'
    if (analysisTab.value === 'subject') return 'subject'
    if (analysisTab.value === 'channel') {
      return channelTab.value === 'card' ? 'card' : channelTab.value
    }
    return 'overview'
  })

// ── 统一时间条(#67 P1): 全页唯一时间状态 ──
  const rangePreset = ref('last30')

  const timeGrain = ref('auto')

  const compareMode = ref('prev')

  const customRange = ref([])

  const range = ref({ start: '', end: '' })

  function onRangeChange(r) {
    range.value = { ...r }
    loadData()
  }

// ── 决策概览数据(#67 P1: 单请求出一屏) ──
  const overviewData = ref(null)

  const overviewLoading = ref(false)

  const data = ref(null)

// F-P2-4: 被投诉主体下钻抽屉 state
  const subjectDrawerVisible = ref(false)

  const subjectDetailLoading = ref(false)

  const currentSubject = ref('')

  const subjectDetailList = ref([])

// 移动端响应式: 简化描述文字/控件
  const isMobile = ref(false)

  let _mq = null

  function _onMediaChange(e) { isMobile.value = e.matches }

  onMounted(() => {
    _mq = window.matchMedia('(max-width: 768px)')
    isMobile.value = _mq.matches
    _mq.addEventListener('change', _onMediaChange)
  })

  onBeforeUnmount(() => { _mq?.removeEventListener('change', _onMediaChange) })

// 趋势图粒度(按日/周/月): 周=ISO自然周, 月=自然月; 前端对后端按日数据做汇聚
  const subjectTrendGrain = ref('day')

  const topSubjectTrendGrain = ref('day')

// F-P1-2: 明细 登记/处理 计数(标题展示)
// ── 积压审核(backlog tab) ──
  const backlogData = ref(null)

  const backlogLoading = ref(false)

  const backlogExporting = ref(false)

  const backlogPage = ref(1)

  const backlogPageSize = ref(50)

  const backlogTotal = ref(0)

  const backlogFilters = reactive({ source_type: '', status: '', has_handler: null })

  const auditVisible = ref(false)

  const auditSubmitting = ref(false)

  const auditForm = reactive({ source_type: 'internal', ref_id: '', device_sn: '', attribution: '其他', judgment: '通过', conclusion: '' })

  const attributions = ['网络质量', '套餐争议', '退款协商失败', '降成争议', '其他', '未关联SN']

// ── ★ 2026-09-11 (#67 P3): 数据治理 state 已随页面外迁 ──
//   「人工审核(#55)」与「采集质量(#60)」→ 系统管理 → 投诉采集质量
//   新入口: /system/complaint-quality (见 web/src/views/management/ComplaintDataQuality.vue)
// ── 被投诉主体分析(subject tab) ──
  const subjectData = ref(null)

  const subjectLoading = ref(false)

// ★ #67 P2: 主体筛选状态(默认与改造前一致: 全部 / 按总量 / Top20 / 不对比)
//   "Top20" 是后端不传 top_n 时的旧行为 —— 前端显式传 20 保持等价。
  const subjectFilter = reactive({
    source_type: 'all',
    sort_by: 'total',
    top_n: 20,
    comparePrev: false,
  })

  const b = computed(() => backlogData.value || {})

  const backlogItems = computed(() => b.value.items || [])

  const oldestAging = computed(() => (b.value.max_aging != null ? b.value.max_aging : '—'))

// ── 积压快照(决策概览) helpers ──
  function bySourceCount(src) {
    return (b.value.by_source || []).find(x => x.name === src)?.count ?? 0
  }

  function sourceBarColor(src) {
    if (src === '内部投诉') return '#2563eb'
    if (src === '外部投诉') return '#dc2626'
    return '#7c3aed'
  }

  function sourceRatioPct(src) {
    const t = b.value.total || 0
    if (!t) return '0%'
    return Math.round(bySourceCount(src) / t * 100) + '%'
  }

  const backlogSnapshotItems = computed(() => backlogItems.value.slice(0, 5))

// 来源×龄期堆叠柱: x=来源, y=数量, 5个龄期桶堆叠
  const backlogSourceAgingOption = computed(() => {
    const d = b.value.by_source_aging || []
    if (!d.length || d.every(x => (x.buckets || []).every(bk => !bk.count))) return null
    const buckets = d[0].buckets.map(bk => bk.name)
    const agingColors = ['#2563eb', '#3b82f6', '#f59e0b', '#ea580c', '#dc2626']
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { bottom: 0, left: 'center', itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
      grid: { left: 44, right: 16, bottom: 30, top: 8 },
      xAxis: { type: 'category', data: d.map(x => x.source), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value' },
      series: buckets.map((bk, i) => ({
        name: bk, type: 'bar', stack: 'total',
        data: d.map(x => x.buckets.find(y => y.name === bk)?.count || 0),
        itemStyle: { color: agingColors[i] }, barMaxWidth: 22,
      })),
    }
  })

  const doingPct = computed(() => {
    const t = b.value.total || 0
    if (!t) return 0
    return ((t - (b.value.unassigned || 0)) / t * 100).toFixed(1)
  })

  const backlogAgingOption = computed(() => {
    const d = b.value.by_aging || []
    if (!d.length) return null
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 48, right: 24, bottom: 32, top: 8 },
      xAxis: { type: 'category', data: d.map(x => x.name), axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', name: '工单数' },
      series: [{
        type: 'bar', data: d.map(x => x.count), barMaxWidth: 28,
        itemStyle: { color: '#eab308', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', fontSize: 11 },
      }],
    }
  })

// 投诉原因分布(按内容关键词归类, 替代区分度低的来源子类分类)
  const reasonColors = {
    '退款/售后': '#dc2626',
    '网络/技术': '#2563eb',
    '套餐/计费': '#ea580c',
    '充值/规则': '#ca8a04',
    '其他': '#6b7280',
    '未填写': '#9ca3af',
  }

  function reasonTagType(reason) {
    if (reason === '退款/售后') return 'danger'
    if (reason === '网络/技术') return 'primary'
    if (reason === '套餐/计费') return 'warning'
    if (reason === '充值/规则') return 'warning'
    return 'info'
  }

  const backlogReasonOption = computed(() => {
    const d = b.value.by_reason || []
    if (!d.length || d.every(x => !x.count)) return null
    const items = [...d].reverse()
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 90, right: 44, bottom: 24, top: 8 },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
      series: [{
        type: 'bar', data: items.map(x => ({
          value: x.count,
          itemStyle: { color: reasonColors[x.name] || '#6b7280', borderRadius: [0, 4, 4, 0] },
        })), barMaxWidth: 18,
        label: { show: true, position: 'right', fontSize: 11 },
      }],
    }
  })

  const subj = computed(() => subjectData.value || {
    summary: { subject_total: 0, merchant_total: 0, external_total: 0, external_attributed: 0, external_unattributed: 0, refund_count: 0, refund_amount: 0 },
    subject_ranking: [],
    severity_distribution: [],
    escalation_funnel: [],
    subject_trend: [],
    top_subject_trend: [],
  })

  const subjectRanking = computed(() => subj.value.subject_ranking || [])

  function extRatioColor(ratio) {
    if (ratio == null) return '#909399'
    if (ratio >= 0.5) return '#dc2626'
    if (ratio >= 0.25) return '#ea580c'
    return '#16a34a'
  }

// 严重性数量分布(堆叠柱状图, 显示商户号vs外部投诉占比)
  const subjectSeverityOption = computed(() => {
    const d = subj.value.severity_distribution || []
    if (!d.length || d.every(x => !x.count)) return null
    const total = d.reduce((sum, x) => sum + x.count, 0)
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const p = params[0]
          const item = d.find(x => x.name === p.seriesName)
          if (!item) return ''
          const pct = total > 0 ? (item.count / total * 100).toFixed(1) : 0
          return `${item.name}<br/>数量: ${item.count}<br/>占比: ${pct}%`
        }
      },
      legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
      grid: { left: 48, right: 24, bottom: 36, top: 8 },
      xAxis: { type: 'value', name: '投诉数' },
      yAxis: { type: 'category', data: [''], axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
      series: [
        { name: '商户号投诉', type: 'bar', stack: 'total',
          data: d.find(x => x.name === '商户号投诉') ? [d.find(x => x.name === '商户号投诉').count] : [0],
          itemStyle: { color: '#7c3aed', borderRadius: [4, 0, 0, 4] },
          barMaxWidth: 28,
          label: { show: true, position: 'inside', fontSize: 12, fontWeight: 600,
            formatter: (p) => p.value > 0 ? `${p.value}` : '' },
        },
        { name: '外部投诉', type: 'bar', stack: 'total',
          data: d.find(x => x.name === '外部投诉') ? [d.find(x => x.name === '外部投诉').count] : [0],
          itemStyle: { color: '#dc2626', borderRadius: [0, 4, 4, 0] },
          barMaxWidth: 28,
          label: { show: true, position: 'inside', fontSize: 12, fontWeight: 600,
            formatter: (p) => p.value > 0 ? `${p.value}` : '' },
        },
      ],
    }
  })

// 投诉升级链(按SN, 内部→商户号→外部 传递性)
  const escalationChainOption = computed(() => {
    const f = subj.value.escalation_funnel || []
    if (!f.length || f.every(x => !x.count)) return null
    // 按升级路径排序: 先展示"从内部起步"的链, 再是直发/组合
    const order = ['internal_only', 'internal_merchant', 'internal_external', 'full_chain', 'merchant_only', 'merchant_external', 'external_only']
    const items = order.map(k => f.find(x => x.key === k)).filter(Boolean)
    const colors = ['#2563eb', '#f59e0b', '#ea580c', '#dc2626', '#7c3aed', '#a855f7', '#dc2626']
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 110, right: 48, bottom: 24, top: 8 },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
      series: [{
        type: 'bar', data: items.map((x, i) => ({
          value: x.count,
          itemStyle: { color: colors[i], borderRadius: [0, 4, 4, 0] },
        })), barMaxWidth: 18,
        label: { show: true, position: 'right', fontSize: 11 },
      }],
    }
  })

// 主体投诉趋势(商户号+外部 堆叠面积; 支持按日/周/月汇聚)
  const subjectTrendOption = computed(() => {
    const t = subj.value.subject_trend || []
    if (!t.length) return null
    const srcs = ['商户号投诉', '外部投诉']
    const bySource = { '商户号投诉': {}, '外部投诉': {} }
    t.forEach(r => {
      srcs.forEach(s => { if (r[s]) bySource[s][r.day] = (bySource[s][r.day] || 0) + r[s] })
    })
    const agg = aggregateMultiSeries(bySource, subjectTrendGrain.value)
    return {
      tooltip: { trigger: 'axis' },
      legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
      grid: { left: 48, right: 24, bottom: 44, top: 16 },
      xAxis: { type: 'category', data: agg.buckets.map(b => b.label), axisLabel: { fontSize: 10, rotate: agg.buckets.length > 12 ? 30 : 0 } },
      yAxis: { type: 'value', name: '投诉数' },
      series: [
        { name: '商户号投诉', type: 'line', stack: 'total', smooth: true,
          areaStyle: { opacity: 0.6 },
          data: agg.byName['商户号投诉'].values, itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2 }, symbol: 'circle', symbolSize: 4 },
        { name: '外部投诉', type: 'line', stack: 'total', smooth: true,
          areaStyle: { opacity: 0.6 },
          data: agg.byName['外部投诉'].values, itemStyle: { color: '#dc2626' }, lineStyle: { width: 2 }, symbol: 'circle', symbolSize: 4 },
      ],
    }
  })

// Top5 被投诉主体趋势(堆叠面积, 支持按日/周/月汇聚; Top5按汇聚总量重新排序)
  const topSubjectColors = ['#dc2626', '#ea580c', '#ca8a04', '#0891b2', '#7c3aed']

  const topSubjectTrendOption = computed(() => {
    const t = subj.value.top_subject_trend || []
    if (!t.length) return null
    // 各主体 {day: count}
    const bySubject = {}
    t.forEach(s => {
      const m = {}
      ;(s.trend || []).forEach(p => { m[p.day] = (m[p.day] || 0) + p.count })
      bySubject[s.name] = m
    })
    const agg = aggregateMultiSeries(bySubject, topSubjectTrendGrain.value)
    // Top5: 按当前粒度下的汇聚总量降序取前5
    const ranked = agg.names
      .map(n => ({ name: n, total: agg.byName[n].values.reduce((s, v) => s + v, 0) }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 5)
    return {
      tooltip: { trigger: 'axis' },
      legend: { bottom: 0, left: 'center', type: 'scroll', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 10 } },
      grid: { left: 48, right: 24, bottom: 44, top: 16 },
      xAxis: { type: 'category', data: agg.buckets.map(b => b.label), axisLabel: { fontSize: 10, rotate: agg.buckets.length > 12 ? 30 : 0 } },
      yAxis: { type: 'value' },
      series: ranked.map((s, i) => ({
        name: s.name, type: 'line', stack: 'total', smooth: true,
        data: agg.byName[s.name].values,
        itemStyle: { color: topSubjectColors[i % topSubjectColors.length] },
        lineStyle: { color: topSubjectColors[i % topSubjectColors.length], width: 2 },
        areaStyle: { opacity: 0.55 },
        symbolSize: 4,
      })),
    }
  })

  const s = computed(() => data.value || {
    summary: { total: 0, internal_total: 0, merchant_total: 0, external_total: 0, backlog_total: 0, done_total: 0, completion_rate: 0, avg_handle_hours: 0 },
    reg_handled: {
      total: { registered: 0, handled: 0 },
      '内部投诉': { registered: 0, handled: 0 },
      '商户号投诉': { registered: 0, handled: 0 },
      '外部投诉': { registered: 0, handled: 0 },
    },
    source_distribution: [],
    trend: [],
    handler_top: [],
    platform_distribution: [],
    external_category_distribution: [],
    external_trend: [],
    external_total: 0,
    external_matched: 0,
    external_unmatched: 0,
    external_today_registered: 0,
    merchant_category: [],
    merchant_entity_distribution: [],
    merchant_entity_trend: [],
    merchant_handler_distribution: [],
    timeliness: [],
    handler_efficiency: { internal: [], merchant: [], external: [] },
    external_platform_trend: [],
    water: [],
  })

// ★★ 2026-09-11 (#53): (外)卡商 **独立统计** ★★
//   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
//   后端 `card` 对象与 `reg_handled` **平行**(非其一部分), 故单独取值。
// ── 投诉水位(进水/出水 并列柱 + 净水位/累计净水位 折线) ──
  const complaintWaterOption = computed(() => {
    const w = s.value.water || []
    if (!w.length) return null
    const dates = w.map(x => (x.day || '').slice(5))
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { data: ['新增投诉', '处理投诉', '净水位', '累计净水位'], top: 0, textStyle: { fontSize: 11 } },
      grid: { left: 48, right: 56, bottom: 44, top: 36 },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, rotate: 45 } },
      yAxis: [
        { type: 'value', name: '条数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
        { type: 'value', name: '净水位', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
      ],
      series: [
        // 新增投诉 vs 处理投诉 = 此消彼长的比较关系: 并列柱(不同颜色, 不堆叠)
        { name: '新增投诉', type: 'bar', data: w.map(x => x.in), itemStyle: { color: '#f87171' }, barMaxWidth: 24 },
        { name: '处理投诉', type: 'bar', data: w.map(x => x.out), itemStyle: { color: '#10b981' }, barMaxWidth: 24 },
        { name: '净水位', type: 'line', yAxisIndex: 1, data: w.map(x => x.net), smooth: true, symbol: 'circle', symbolSize: 6,
          itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
          markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
        { name: '累计净水位', type: 'line', yAxisIndex: 1, data: w.map(x => x.cum_net), smooth: true, symbol: 'circle', symbolSize: 5,
          itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' } },
      ],
    }
  })

  const platformSummary = computed(() => {
    const p = s.value.platform_distribution || []
    return p.slice(0, 3).map(x => x.name).join(' / ') || '暂无'
  })

  const merchantEntitySummary = computed(() => {
    const p = s.value.merchant_entity_distribution || []
    return p.slice(0, 2).map(x => x.name).join(' / ') || '暂无'
  })

// ── 请求参数(统一由时间条驱动, #67 P1 移除了页面内散落的粒度开关) ──
  const params = computed(() => {
    const p = {}
    if (range.value.start) p.start = range.value.start
    if (range.value.end) p.end = range.value.end
    if (timeGrain.value && timeGrain.value !== 'auto') p.grain = timeGrain.value
    return p
  })

// ── 决策概览数据加载(#67 P1) ──
  const overviewParams = computed(() => ({
    ...params.value,
    compare: compareMode.value,
  }))

  async function loadOverview() {
    overviewLoading.value = true
    try {
      const res = await complaintOverview(overviewParams.value)
      overviewData.value = res.data || null
    } catch (e) {
      overviewData.value = null
      ElMessage.error('概览加载失败：' + (e?.message || e))
    } finally {
      overviewLoading.value = false
    }
  }

// ── 渠道同构面板(#67 P2) ──
//   四个渠道共用一份数据契约：`/summary` 的 internal/merchant/external/card 分源对象。
//   ⚠️ 卡商(card) 是**独立体系**(#53)，不参与投诉总量，但它与其它三源**同结构**返回，
//      因此可以被同一个面板渲染 —— 这正是"同构"的价值。
  const summaryData = ref(null)

  const summarySrc = computed(() => summaryData.value || {})

  const channelLoading = ref(false)

  const channelRows = ref([])

/** 加载 /summary —— 渠道面板的分类/处理人/趋势数据源 */
  async function loadSummary() {
    try {
      const res = await complaintSummary(params.value)
      summaryData.value = res.data || null
    } catch {
      summaryData.value = null
    }
  }

/** 该渠道的分源数据对象 */
  const channelSrcData = computed(() => {
    const d = summarySrc.value
    return d?.[channelTab.value] || {}
  })

/** 趋势粒度：与顶部时间条的"自动"保持一致(≤31天=日 / 32~120天=周 / >120天=月) */
  const effectiveGrain = computed(() => {
    if (timeGrain.value && timeGrain.value !== 'auto') return timeGrain.value
    const s = range.value.start; const e = range.value.end
    if (!s || !e) return 'day'
    const days = dayjs(e).diff(dayjs(s), 'day') + 1
    if (days <= 31) return 'day'
    if (days <= 120) return 'week'
    return 'month'
  })

/** 渠道 → 明细接口的 source_type 映射
 *  ⚠️ 卡商在明细接口里的 source_type 是 `(外)卡商`(带括号)，与内部 tab key 不同 */
  const CHANNEL_DETAIL_SOURCE = {
    internal: '内部投诉',
    merchant: '商户号投诉',
    external: '外部投诉',
    card: '(外)卡商',
  }

  async function loadChannelDetail() {
    channelLoading.value = true
    try {
      const p = { ...params.value, source_type: CHANNEL_DETAIL_SOURCE[channelTab.value] || '外部投诉', limit: 500 }
      const res = await complaintDetail(p)
      channelRows.value = res.data?.items || res.data?.records || []
    } catch (e) {
      channelRows.value = []
      ElMessage.error('明细加载失败：' + (e?.message || e))
    } finally {
      channelLoading.value = false
    }
  }

// ── 概览: 结论条(点击下钻到对应页) ──
  const conclusions = computed(() => overviewData.value?.conclusions || [])

  function onConclusionDrill(drill) {
    const tab = drill?.tab
    if (!tab || tab === 'overview') return
    if (tab === 'backlog') { topTab.value = 'execute'; return }
    if (tab === 'subject') { topTab.value = 'analysis'; analysisTab.value = 'subject'; loadSubjectAnalysis(); return }
    if (['internal', 'merchant', 'external', 'card'].includes(tab)) {
      topTab.value = 'analysis'; analysisTab.value = 'channel'; channelTab.value = tab
      if (tab === 'external') loadExternalDetail()
      if (tab === 'merchant') loadMerchantDetail()
      return
    }
  }

// ── 概览: KPI 卡(5 张 + 环比) ──
  const ovKpi = computed(() => {
    const m = {}
    for (const k of (overviewData.value?.kpi || [])) m[k.key] = k
    return m
  })

  function kpiDeltaText(k) {
    if (!k) return ''
    if (k.delta_pp != null) {
      return `${k.delta_pp > 0 ? '↑' : '↓'} ${Math.abs(k.delta_pp)}pp`
    }
    if (k.delta_pct != null) {
      return `${k.delta_pct > 0 ? '↑' : '↓'} ${Math.abs(k.delta_pct)}%`
    }
    return ''
  }

  function kpiDeltaType(k) {
    if (!k) return 'info'
    const v = k.delta_pp != null ? k.delta_pp : k.delta_pct
    if (v == null) return 'info'
    return v > 0 ? 'danger' : 'success'
  }

  function fmtKpi(k) {
    if (!k || k.value == null) return '—'
    if (k.unit === '元') return `¥${Number(k.value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    if (k.unit === '%') return `${k.value}`
    return Number(k.value).toLocaleString('zh-CN')
  }

// ── 概览: 总量堆积趋势(内/商/外 三源堆叠; 卡商独立不并入) ──
  const ovStackOption = computed(() => {
    const rows = overviewData.value?.trend_stacked || []
    if (!rows.length) return null
    const srcs = ['内部投诉', '商户号投诉', '外部投诉']
    const colors = { '内部投诉': '#2563eb', '商户号投诉': '#7c3aed', '外部投诉': '#dc2626' }
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
      grid: { left: 48, right: 20, bottom: 40, top: 12 },
      xAxis: {
        type: 'category',
        data: rows.map(r => r.label),
        axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 },
      },
      yAxis: { type: 'value', name: '条' },
      series: srcs.map(s => ({
        name: s, type: 'bar', stack: 'total',
        data: rows.map(r => r[s] || 0),
        itemStyle: { color: colors[s] }, barMaxWidth: 26,
      })),
    }
  })

// ── 概览: (外)卡商独立趋势(不并入上方堆叠) ──
  const ovCardTrendOption = computed(() => {
    const rows = overviewData.value?.card_trend || []
    if (!rows.length) return null
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 16, bottom: 28, top: 12 },
      xAxis: { type: 'category', data: rows.map(r => r.label), axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 } },
      yAxis: { type: 'value', name: '条' },
      series: [{
        type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
        data: rows.map(r => r.registered || 0),
        itemStyle: { color: '#059669' }, areaStyle: { opacity: 0.15 },
      }],
    }
  })

// ── 概览: 登记 vs 处理(对称水位) ──
  const ovWaterOption = computed(() => {
    const rows = overviewData.value?.trend_reg_handled || []
    if (!rows.length) return null
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
      grid: { left: 48, right: 52, bottom: 40, top: 12 },
      xAxis: { type: 'category', data: rows.map(r => r.label), axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 } },
      yAxis: [
        { type: 'value', name: '条' },
        { type: 'value', name: '净水位', splitLine: { show: false } },
      ],
      series: [
        { name: '登记', type: 'bar', data: rows.map(r => r.registered || 0), itemStyle: { color: '#f87171' }, barMaxWidth: 18 },
        { name: '处理', type: 'bar', data: rows.map(r => r.handled || 0), itemStyle: { color: '#10b981' }, barMaxWidth: 18 },
        {
          name: '累计净水位', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 5,
          data: rows.map(r => r.cum_net || 0),
          itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' },
        },
      ],
    }
  })

// ── 概览: 涉诉金额趋势(新增) ──
  const ovRefundOption = computed(() => {
    const rows = overviewData.value?.trend_refund || []
    if (!rows.length) return null
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (ps) => {
          const i = ps[0]?.dataIndex ?? 0
          const r = rows[i] || {}
          return `${r.label}<br/>笔数: ${r.count || 0}<br/>金额: ¥${Number(r.amount || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        },
      },
      legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
      grid: { left: 48, right: 52, bottom: 40, top: 12 },
      xAxis: { type: 'category', data: rows.map(r => r.label), axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 } },
      yAxis: [
        { type: 'value', name: '笔' },
        { type: 'value', name: '金额(元)', splitLine: { show: false } },
      ],
      series: [
        { name: '退款笔数', type: 'bar', data: rows.map(r => r.count || 0), itemStyle: { color: '#fbbf24' }, barMaxWidth: 18 },
        {
          name: '退款金额', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 5,
          data: rows.map(r => Math.round((r.amount || 0) * 100) / 100),
          itemStyle: { color: '#ea580c' }, lineStyle: { width: 2 },
        },
      ],
    }
  })

// ── 概览: 积压龄期分布 ──
  const ovAgingOption = computed(() => {
    const rows = overviewData.value?.backlog?.by_aging || []
    if (!rows.length || rows.every(r => !r.count)) return null
    const colors = ['#2563eb', '#3b82f6', '#f59e0b', '#dc2626']
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 44, right: 20, bottom: 28, top: 12 },
      xAxis: { type: 'category', data: rows.map(r => r.name), axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', name: '条' },
      series: [{
        type: 'bar', barMaxWidth: 34,
        data: rows.map((r, i) => ({ value: r.count, itemStyle: { color: colors[i % colors.length] } })),
        label: { show: true, position: 'top', fontSize: 11 },
      }],
    }
  })

// ── A5 · 投诉登记时段热力图（#72） ──
//   x = 小时(0-23)，y = 星期(周一~周日)；色深 = 登记量
//   ★ 用 heatmap 而非折线：时段节律是"二维"的（小时 × 星期），
//     折线只能表一维，会丢掉"周末/工作日差异"这一半信息。
  const ovHourOption = computed(() => {
    const h = overviewData.value?.hour_heatmap
    if (!h || !h.total) return null
    const hmap = {}
    for (const c of h.by_hour || []) hmap[c.hour] = c.count
    const data = []   // echarts heatmap: [x(小时), y(星期索引), value]
    let max = 1
    for (const c of h.cells || []) {
      if (c.count > max) max = c.count
    }
    for (const c of h.cells || []) {
      data.push([c.hour, c.weekday - 1, c.count])  // weekday 1..7 → 0..6
    }
    const WD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    return {
      tooltip: {
        position: 'top',
        formatter: (p) => `${WD[p.value[1]]} ${String(p.value[0]).padStart(2, '0')}时<br/>登记 ${p.value[2]} 条`,
      },
      grid: { left: 44, right: 16, top: 10, bottom: 46 },
      xAxis: {
        type: 'category', data: Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0')),
        splitArea: { show: true }, axisLabel: { fontSize: 9, interval: 1 },
        name: '小时', nameLocation: 'middle', nameGap: 26, nameTextStyle: { fontSize: 10 },
      },
      yAxis: {
        type: 'category', data: WD, splitArea: { show: true }, axisLabel: { fontSize: 10 },
      },
      visualMap: {
        min: 0, max, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
        itemWidth: 10, itemHeight: 60, textStyle: { fontSize: 9 },
        inRange: { color: ['#f1f5f9', '#bfdbfe', '#60a5fa', '#2563eb', '#1e3a8a'] },
      },
      series: [{
        type: 'heatmap', data, label: { show: false },
        emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.3)' } },
      }],
    }
  })

/** 峰谷提示文案（把"热力图"翻译成"结论" —— 否则使用者要自己看色块推断） */
  const peakHint = computed(() => {
    const h = overviewData.value?.hour_heatmap
    if (!h || !h.total) return ''
    const fmt = (xs) => (xs || []).map(x => `${String(x).padStart(2, '0')}时`).join(' ')
    const parts = []
    if (h.peak_hours?.length) parts.push(`高峰（需值守）：${fmt(h.peak_hours)}`)
    if (h.valley_hours?.length) parts.push(`谷底（可减配）：${fmt(h.valley_hours)}`)
    return parts.join(' · ')
  })

// ── A2 · 反复投诉（#72） ──
//   ★ 后端已按「事件口径」算好（SN + 北京日期去重），前端只渲染，不做二次计数。
  const ovRepeat = computed(() => overviewData.value?.repeat || {})

  const ovRepeatRows = computed(() => (ovRepeat.value.items || []).slice(0, 6))

  const repeatThreshold = computed(() => ovRepeat.value.threshold ?? 3)

  const ovEscalatedCount = computed(() => (ovRepeat.value.items || []).filter(i => i.escalated).length)

// ── 来源分布饼图(含兜底: source_distribution 为空时用 summary 三源数) ──
  const sourcePieColors = ['#2563eb', '#7c3aed', '#dc2626']

// ── 外部投诉平台分布饼图 ──
  const platformColors = ['#dc2626', '#ea580c', '#ca8a04', '#65a30d', '#0891b2', '#7c3aed', '#db2777', '#4f46e5']

// ── 商户号被投诉主体趋势(堆叠面积, 商户号内细分; 支持按日/周/月汇聚) ──
  const merchantEntityColors = ['#7c3aed', '#ea580c', '#0891b2', '#ca8a04', '#dc2626', '#2563eb', '#16a34a', '#db2777', '#4f46e5']

  function sourceTagType(source) {
    if (source === '内部投诉') return 'warning'
    if (source === '外部投诉') return 'danger'
    if (source === '商户号投诉') return 'primary'
    return 'info'
  }

  function formatNum(v) {
    if (v == null || v === '') return '0'
    const n = Number(v)
    if (Number.isNaN(n)) return '0'
    return n.toLocaleString('zh-CN')
  }

  function formatTime(t) {
    if (!t) return '—'
    const d = dayjs(t)
    return d.isValid() ? d.format('MM-DD HH:mm') : '—'
  }

// ── 趋势粒度汇聚工具: 把按日序列(day→count)按周/月聚合成带标签的序列 ──
  function aggregateSeries(dayMap, grain) {
    if (grain === 'day') {
      const sorted = Object.keys(dayMap).sort()
      return {
        labels: sorted.map(d => d.slice(5)),
        keys: sorted,
        values: sorted.map(d => dayMap[d]),
      }
    }
    const groups = new Map()
    Object.entries(dayMap).forEach(([d, v]) => {
      const dt = dayjs(d)
      if (!dt.isValid()) return
      let key, label
      if (grain === 'week') {
        const ws = dt.startOf('isoWeek')
        const we = ws.add(6, 'day')
        key = ws.format('YYYY-MM-DD')
        label = `${ws.format('MM-DD')}~${we.format('MM-DD')}`
      } else {
        key = dt.format('YYYY-MM')
        label = dt.format('YYYY-MM')
      }
      const g = groups.get(key) || { label, value: 0 }
      g.value += v
      groups.set(key, g)
    })
    const sorted = [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
    return {
      labels: sorted.map(([, g]) => g.label),
      keys: sorted.map(([k]) => k),
      values: sorted.map(([, g]) => g.value),
    }
  }

// 多序列汇聚: series 形如 { name: {day: count} }, 返回 { names, buckets, byName(values 对齐 buckets) }
  function aggregateMultiSeries(series, grain) {
    const names = Object.keys(series)
    if (!names.length) return { names: [], buckets: [], byName: {} }
    const byName = {}
    names.forEach(n => { byName[n] = aggregateSeries(series[n], grain) })
    // 统一 key 集合(并集, 升序)
    const keys = new Set()
    names.forEach(n => byName[n].keys.forEach(k => keys.add(k)))
    const sortedKeys = [...keys].sort()
    // key → label 映射(取任一存在的 label)
    const labelOf = {}
    names.forEach(n => {
      byName[n].keys.forEach((k, i) => { if (!labelOf[k]) labelOf[k] = byName[n].labels[i] })
    })
    const buckets = sortedKeys.map(k => ({ key: k, label: labelOf[k] || k }))
    // 每个序列对齐到统一 buckets(缺失补0)
    names.forEach(n => {
      const m = {}
      byName[n].keys.forEach((k, i) => { m[k] = byName[n].values[i] })
      byName[n].values = buckets.map(b => m[b.key] || 0)
    })
    return { names, buckets, byName }
  }

// ── 加载数据 ──
  async function loadData() {
    loading.value = true
    try {
      backlogPage.value = 1 // 时间范围变化 → 积压分页重置
      const res = await complaintAnalysis(params.value)
      data.value = res.data || {}
      loaded.value = true
      // ★ 概览改用专用接口单请求出一屏(#67 P1):
      //   原概览需并发 8 个请求且各接口时间口径不一致 → 同屏数字打架。
      loadOverview()
      // 渠道面板的数据源(分类/处理人/趋势)
      loadSummary()
      loadChannelDetail()
      // 同时加载明细
      loadBacklog()
      loadSubjectAnalysis()
    } finally {
      loading.value = false
    }
  }

  async function loadSubjectAnalysis() {
    subjectLoading.value = true
    try {
      // ★ #67 P2: 主体筛选参数(来源/排序/TopN/环比)
      //   comparePrev 勾选时才传 compare=prev —— 环比需算对比期，不必每次默认开启。
      //   ⚠️ 环比需要「时间窗口」才能算对比期 —— 主体页默认无窗口时
      //     自动补一个近 30 天窗口，否则 delta 恒为 None(实测踩到: 列全是 —)。
      const base = { ...params.value }
      if (subjectFilter.comparePrev && !base.start && !base.end) {
        const today = dayjs()
        base.start = today.subtract(29, 'day').format('YYYY-MM-DD')
        base.end = today.format('YYYY-MM-DD')
      }
      const p = {
        ...base,
        source_type: subjectFilter.source_type,
        sort_by: subjectFilter.sort_by,
        top_n: subjectFilter.top_n,
        compare: subjectFilter.comparePrev ? 'prev' : 'none',
      }
      const res = await complaintSubjectAnalysis(p)
      subjectData.value = res.data || {}
    } finally {
      subjectLoading.value = false
    }
  }

/** 环比开关变化 —— 立即重拉(排序若选 delta 也依赖它) */
  function onSubjectCompareChange() {
    loadSubjectAnalysis()
  }

/** 筛选后命中的主体总数(供前端显示 "命中 N 个主体") */
  const subjRankCount = computed(() => subjectData.value?.filters?.ranked_total ?? 0)

// F-P2-4: 主体下钻 - 打开抽屉并拉该主体明细(detail 支持 subject 归一化过滤)
  async function openSubjectDetail(name) {
    if (!name) return
    currentSubject.value = name
    subjectDrawerVisible.value = true
    subjectDetailLoading.value = true
    subjectDetailList.value = []
    try {
      const p = { ...params.value, limit: 500, include_supplementary: true, subject: name }
      const res = await complaintDetail(p)
      // 兼容 detail 返回 items; 未命中 source 参数时全三源(内部投诉无主体不返回)
      subjectDetailList.value = res.data?.items || []
    } finally {
      subjectDetailLoading.value = false
    }
  }

// 时间字符串排序(ISO/MM-DD HH:mm), 空值排后
  function sortTimeStr(a, b) {
    const va = a || ''
    const vb = b || ''
    if (va === vb) return 0
    if (!va) return 1
    if (!vb) return -1
    return va < vb ? -1 : 1
  }

  async function loadBacklog() {
    backlogLoading.value = true
    try {
      const p = {
        page: backlogPage.value,
        page_size: backlogPageSize.value,
        sort: 'aging_asc',
        ...params.value, // 时间窗口: 默认近30天, 7/90/自定义跟随页面筛选(仅统计窗口内登记的未完成)
      }
      if (backlogFilters.source_type) p.source_type = backlogFilters.source_type
      if (backlogFilters.status) p.status = backlogFilters.status
      if (backlogFilters.has_handler != null) p.has_handler = backlogFilters.has_handler
      const res = await complaintBacklog(p)
      backlogData.value = res.data || {}
      backlogTotal.value = res.data?.total || 0
    } finally {
      backlogLoading.value = false
    }
  }

// 导出积压明细 Excel(与 /backlog 同口径, 含当前筛选, 不受分页限制)
  async function handleBacklogExport() {
    backlogExporting.value = true
    try {
      const p = { sort: 'aging_asc', ...params.value }
      if (backlogFilters.source_type) p.source_type = backlogFilters.source_type
      if (backlogFilters.status) p.status = backlogFilters.status
      if (backlogFilters.has_handler != null) p.has_handler = backlogFilters.has_handler
      const res = await complaintBacklogExport(p)
      const blobData = res instanceof Blob ? res : res?.data
      const blob = new Blob([blobData], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
      let fname = `积压投诉明细_${effectiveStart.value || '全量'}_${effectiveEnd.value || '至今'}.xlsx`
      const cd = res.headers?.['content-disposition'] || ''
      const m = /filename\*=UTF-8''([^;]+)/i.exec(cd)
      if (m) fname = decodeURIComponent(m[1])
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = fname
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      ElMessage.success('已导出 ' + (backlogTotal.value || 0) + ' 条')
    } catch (e) {
      ElMessage.error('导出失败: ' + (e?.message || e))
    } finally {
      backlogExporting.value = false
    }
  }

  const SOURCE_TO_AUDIT = { '内部投诉': 'internal', '商户号投诉': 'merchant', '外部投诉': 'external' }

  function openAudit(row) {
    auditForm.source_type = SOURCE_TO_AUDIT[row.source] || 'internal'
    auditForm.ref_id = row.ticket_no || ''
    auditForm.device_sn = row.device_sn || ''
    auditForm.attribution = '其他'
    auditForm.judgment = '通过'
    auditForm.conclusion = ''
    auditVisible.value = true
  }

  async function submitAudit() {
    if (!auditForm.device_sn.trim() && !auditForm.ref_id.trim()) {
      ElMessage.warning('该工单无SN且无工单号，无法关联核查')
      return
    }
    auditSubmitting.value = true
    try {
      await createAudit({ ...auditForm })
      ElMessage.success('核查已保存')
      auditVisible.value = false
      loadBacklog()
    } finally {
      auditSubmitting.value = false
    }
  }

  onMounted(() => {
    // ★ 初始区间(#67 P1): 默认近 30 天(含今日)。
    //   ⚠️ 必须在此初始化 —— 时间条是受控组件，不自己持有区间；
    //      缺了它 range 恒为空 → 概览/环比/明细全部拿不到窗口(实测踩到)。
    //
    // ★★ #76 下钻落点: 先读 query 覆盖默认值 ★★
    //   规范依据: docs/standards/看板卡片数据说明与下钻规范.md §3.3
    //   「下钻必须带上下文」—— 因此本页必须能**接收**上下文，否则从看板点过来
    //   会落到默认近30天，与刚才看到的窗口不一致，数字对不上 = 下钻失效。
    //   支持参数: start / end / tab(analysis|execute) / view(overview|channel|subject)
    //             / source(internal|merchant|external|card)
    const q = route.query || {}
    const today = dayjs()
    const isDate = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)
    if (isDate(q.start) && isDate(q.end)) {
      // ★ 带窗口下钻: 必须把时间条切到「自定义」并回填区间，
      //   否则 range 虽被覆盖、**控件仍显示「近30天」** → 使用者看到"区间显示 8/15~9/13、
      //   数据却是 9/13 当天"，比不做下钻更糟（数字与标签对不上 = 误导）。
      //   这是"受控组件"的必然后果：状态有第二处（rangePreset/customRange）就必须同步。
      rangePreset.value = 'custom'
      customRange.value = [q.start, q.end]
      range.value = { start: q.start, end: q.end }
    } else {
      range.value = {
        start: today.subtract(29, 'day').format('YYYY-MM-DD'),
        end: today.format('YYYY-MM-DD'),
      }
    }
    // 定位到指定 tab（无参数时保持既有默认：分析 → 决策概览）
    if (q.tab === 'execute') {
      topTab.value = 'execute'
    } else if (['overview', 'channel', 'subject'].includes(q.view)) {
      topTab.value = 'analysis'
      analysisTab.value = q.view
      if (q.view === 'channel' && ['internal', 'merchant', 'external', 'card'].includes(q.source)) {
        channelTab.value = q.source
      }
    }
    loadData()
  })

// ★ 切换渠道 → 只重新拉该渠道明细(概览/summary 已按区间一次性加载，不必重取)。
//   去掉原来「每切一个 tab 触发全页重载」的行为，减少无效请求。
  watch(channelTab, () => {
    if (analysisTab.value === 'channel') loadChannelDetail()
  })

// 从概览下钻到渠道时，也要拉明细(此时 channelTab 可能未变化)
  watch(analysisTab, () => {
    if (analysisTab.value === 'channel') loadChannelDetail()
  })

  return {
    openDeviceAudit,
    openPersonAudit,
    route,
    loaded,
    loading,
    topTab,
    analysisTab,
    channelTab,
    activeTab,
    rangePreset,
    timeGrain,
    compareMode,
    customRange,
    range,
    onRangeChange,
    overviewData,
    overviewLoading,
    data,
    subjectDrawerVisible,
    subjectDetailLoading,
    currentSubject,
    subjectDetailList,
    isMobile,
    _mq,
    _onMediaChange,
    subjectTrendGrain,
    topSubjectTrendGrain,
    backlogData,
    backlogLoading,
    backlogExporting,
    backlogPage,
    backlogPageSize,
    backlogTotal,
    backlogFilters,
    auditVisible,
    auditSubmitting,
    auditForm,
    attributions,
    subjectData,
    subjectLoading,
    subjectFilter,
    b,
    backlogItems,
    oldestAging,
    bySourceCount,
    sourceBarColor,
    sourceRatioPct,
    backlogSnapshotItems,
    backlogSourceAgingOption,
    doingPct,
    backlogAgingOption,
    reasonColors,
    reasonTagType,
    backlogReasonOption,
    subj,
    subjectRanking,
    extRatioColor,
    subjectSeverityOption,
    escalationChainOption,
    subjectTrendOption,
    topSubjectColors,
    topSubjectTrendOption,
    s,
    complaintWaterOption,
    platformSummary,
    merchantEntitySummary,
    params,
    overviewParams,
    loadOverview,
    summaryData,
    summarySrc,
    channelLoading,
    channelRows,
    loadSummary,
    channelSrcData,
    effectiveGrain,
    CHANNEL_DETAIL_SOURCE,
    loadChannelDetail,
    conclusions,
    onConclusionDrill,
    ovKpi,
    kpiDeltaText,
    kpiDeltaType,
    fmtKpi,
    ovStackOption,
    ovCardTrendOption,
    ovWaterOption,
    ovRefundOption,
    ovAgingOption,
    ovHourOption,
    peakHint,
    ovRepeat,
    ovRepeatRows,
    repeatThreshold,
    ovEscalatedCount,
    sourcePieColors,
    platformColors,
    merchantEntityColors,
    sourceTagType,
    formatNum,
    formatTime,
    aggregateSeries,
    aggregateMultiSeries,
    loadData,
    loadSubjectAnalysis,
    onSubjectCompareChange,
    subjRankCount,
    openSubjectDetail,
    sortTimeStr,
    loadBacklog,
    handleBacklogExport,
    SOURCE_TO_AUDIT,
    openAudit,
    submitAudit,
  }
}
