// 由 OperationDashboard.vue 的 <script setup> 拆分而来(issue #184)。
// 模板零改动: 全部顶层绑定名经 return 回注 .vue 的 setup 解构。
// 组件与图标 import 留在 .vue 内注册(模板解析需要); 依赖 import 在此独立声明。

// ========== 时间选择 ==========
// ========== 人员筛选 ==========
// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
// 审计弹窗(点击人员/设备唤起)
// ========== 图表配置 ==========
// 趋势图: 默认只显示接待,可勾选投诉/退款
// 工作效率表: 各专项接待明细列 (按类型分开SN/接待数)
// ★ 2026-09-14 投诉专项合并：新增「投诉专项」列。
//   ⚠️ 旧「内部投诉/外部投诉」列**保留** —— 历史区间的工单仍按旧桶归集，
//      删掉会让历史数据无处显示（且非法重算历史）。
//   ⚠️ 列 key 必须与后端 `analytics.CAT_BUCKETS` 的 `cat_name` 逐字一致，
//      否则 `getSpecialtyPeople(row, col.key)` 取不到值 → 恒显示「—」。
/** ★ 岗位 → 徽标（用户 2026-09-14：「组长」「管理」要一眼可辨）
 *
 *  设计要点:
 *   · **管理岗**（主管）→ 文案「管理」：使用者关心的是"他能拍板什么"，
 *     而非"他叫什么级别"。
 *   · **组长/副主管** → 文案「组长」：值班/带组语境下同属组级管理者。
 *   · 专员类（投诉专员/退款处理专员）→ 显示**原文**（不缩写）：
 *     "投诉专员"本身信息量足够，缩写反而模糊。
 *   · 客服 → 浅色文字（数量最多，高亮会淹没管理岗）。
 *   ⚠️ 不得用 `is_management` 判断 —— 它含专员类跨类豁免岗（投诉专员等），
 *     那些**不是**管理岗。
 */
// ========== 数据存储 ==========
// 投诉专项数据
// ★ #87: 取组件实例代理 —— 用于在 setup 函数内做路由跳转
//   （本文件未引入 useRouter，模板里用的是 `$router`；此处保持一致写法）
// 被投诉主体维度数据
// ★ #87 投诉健康度指标（活跃SN / 投诉率三维 / 岗位负荷 / 重复投诉率）
// workbuddy 推送补充数据源(315/卡商, 按SN关联工单)
// #47 (2026-09-10): 卡商补充数据源(单列, 原先并入外部)
// ★ #80 三源合计（当前窗口内，来自 reg_handled —— 与四卡同源，可安全相加）
/** 只有拿到三源数据才显示合计提示（避免显示 0 误导） */
/** ★ #81 口径一致性说明（页面 ⓘ）—— 使用者最容易困惑的地方，必须给解释 */
/** ★ #80: 趋势图数据说明（页面层 ⓘ，规范 §二 双强制之①） */
// ========== 投诉三源（#81 口径统一后的唯一出口）==========
// ★ #81 (2026-09-13): 核心指标「投诉数」的口径已与「投诉专项四卡」**统一同源**
//   —— 后端 `today_kpi` 现直接调用 `complaints._sources_reg_handled`。
//   故此处可以安全地做「括弧三源」（#80 诉求 2）：
//   三源之和 == 主值，不会出现括弧与总数对不上的误导。
//   ⚠️ 三源**不含 (外)卡商**（#53: 卡商独立统计, 全部单算）。
//   数据源: complaints/summary 的 reg_handled（与四卡同源，保证逐位一致）。
/** 投诉数卡的「括弧三源」chips（每源取其 registered，与主值同窗口同判据） */
// 无数据时不渲染 chips（避免显示三个 0 误导）
/** 投诉数卡的数据说明（来源/口径/时点/取舍 —— 看板卡片规范 §二 双强制） */
// ========== ★ #82 积压账龄分档 ==========
/** 后端 `backlog.aging`（全量未完成投诉工单按账龄分档）。空则不渲染。 */
/** >30 天部分（需决策清理的部分 —— 这才是"该不该管"的答案） */
/** 四档配色：越老越红（绿→黄→橙→红），让"陈旧"在视觉上自然浮出 */
/** ★ #87 投诉健康度数据说明（页面层 ⓘ，规范 §二 双强制之①）
 *
 *  本卡组与上方「投诉专项四卡」的关系：四卡看**绝对量**，本组看**相对水平**。
 *  两者不可互相替代 —— 绝对量无法回答"好不好"，相对水平无法回答"有多少要处理"。
 */
/** ★ #87 岗位负荷数据说明 */
/** 比率格式化：null/undefined → 「—」（分母为0，不是 0%） */
/** ★ #87 下钻：投诉率 → 投诉分析页（带窗口 + 来源）
 *  ⚠️ 使用组件实例的 `proxy.$router`（本文件未引入 useRouter，
 *     与模板中既有的 `$router.push` 写法保持一致）。
 */
/** ★ #87 下钻：重复投诉 → 明细页（同SN重复无法在明细接口透传，落到窗口内投诉列表） */
/** ★ #87: 兼办投诉量（非组长/专员完成的投诉结案数）—— 暴露岗位临时调配 */
/** 兼办占比（相对全站投诉结案数） */
/** 积压账龄数据说明（页面层 ⓘ，看板卡片规范 §二 双强制） */
// 投诉明细表 筛选/排序状态 (KPI 卡片点击 = 来源筛选)
/** ★ #80: 投诉明细接口固定取前 500 条（见 loadAll 的 complaintDetail 调用）。
 *  必须与调用处保持一致，否则提示文案会与实际截断点不符。 */
/** ★ #80 硬伤 B: 是否触达截断上限 → 用于显示"明细可能不全"警告。
 *  判据: 返回条数 >= 上限（后端是 LIMIT，达到上限即可能还有更多）。 */
// 时间字符串排序(ISO), 支持空值排在后
// 来源/分类/状态 可选项(el-table :filters 需要 {text,value} 形式)
// 明细表最终数据 = 来源(KPI点击/列内筛选二者合并) + 分类 + 状态 三级筛选
// ========== 选项 ==========
// ⚠️ 2026-09-13 修复存量 bug: 原用 `startOf('week').add(1,'day')`,
//    而 dayjs 的周起始是**周日** → 周日当天「本周」会被算成下周（09-14~09-20，未来）
//    → 指标全为 0。改用共享 getWeekRange（周一起始，单一来源）。
// ========== 标签 ==========
// ========== 日期范围计算 ==========
// ⚠️ 2026-09-13 修复: 原 `now.subtract(offset,'week').startOf('week').add(1,'day')`
//   在周日当天会把「本周」算成下一周（未来）→ 全部指标为 0。
//   改用共享 getWeekRange（周一起始）+ clamp 到今天，避免拉取未来日期。
// ========== KPI卡 ==========
// ★ #80: 四路经营退款拆分（后端 `cash_routes_today` 已返回 route_a~d 的 count/amount）。
//   用途: 「退款笔数」卡下方括弧显示 A/B/C/D 各自笔数，
//        「退款金额」卡下方显示四路金额 —— 让「四路合计」不再是黑箱。
//   ⚠️ 金额是元（后端已 rounded 2 位），前端统一折算为万元显示，避免长数字撑破卡片。
// 实际现金流 E —— 与 ABCD 经营估算口径不同（E 才是真实现金退出）
// ★ #78: stack 由后端 reception.breakdown 构造（null 时不渲染）
// ★ #80: 退款笔数 —— 括弧展开四路笔数（A线上直退/B钱包取现/C闪猫/D手动）
//   四路之和 == 主值（后端 operational.count 就是 A+B+C+D）。
// ★ #83: 退款笔数也接目标水位 —— #83 已修正其口径为四路合计（与看板同源）
//   且阈值经分布验证（400/460）。修法: 与挽回卡同一个 `metricLevelByCode`。
// ★ #80: 退款金额 —— 副行给四路金额；并补一条「实际现金流E」对照
//   ⚠️ 铁律: ABCD 是**经营记账**（含退入钱包这类非现金退出），E 才是**真实现金流出**。
//     两者不相等是正常的，不是 bug（见 docs/standards 与 #20）。
// ★ #81: 括弧三源 —— ⚠️ 必须写 `.value`！
//   `complaintChips` / `complaintTip` 是 computed ref，在**另一个 computed 内部**
//   访问时不会被 Vue 自动解包（自动解包只发生在模板里）→ 直接写 `chips: complaintChips`
//   会把 ref 对象塞进 props，`v-if="chips && chips.length"` 恒为 undefined → 静默不渲染。
//   ★ 教训: props 传 computed 结果时必须显式 `.value`（实测踩过一次）。
// ★ #82: 挽回率卡接「目标达成水位」—— 阈值来自后端 metric_thresholds，
//   前端只负责显示。这是本页**唯一**接入水位的指标，原因（重要）：
//   ⚠️ 2026-09-13 生产实测发现其余指标的阈值**严重错配** + 口径与看板卡不同源：
//        refund_count 实际 314 / 阈值 30-50（差10倍，且只算A路单表）
//        ticket_backlog 实际 599 / 阈值 20-50（差30倍）
//        internal_complaint_daily 实际 123 / 阈值 8-15（差15倍）
//     若强行接上会显示「退款笔数严重告警」（其实很正常）→ **比不显示更糟**，
//     会训练使用者忽略告警。已开 #83 治理，治理完成后再扩展接入。
//   `recovery_rate` 通过双重验证：① 口径同源（底层 refund_recovery_analysis
//     与看板挽回卡一致）② 阈值 40/30/20 与 Today.vue 既有业务规则吻合。
/**
 * ★ #82/#83 通用水位取值：从后端 `metric_levels` 按 code 取。
 *
 * ⚠️ 为什么抽成函数而非直接写 `kpiData.value?.metric_levels?.xxx`：
 *   ① 避免每个卡各写一遍可选链（写错即静默不渲染，实测踩过）；
 *   ② #83 起接入多个指标，统一入口便于后续扩展。
 * @param {string} code 指标 code（须与后端 METRIC_DEFS 一致）
 * @returns {object|null} null 时 KpiCard 不渲染水位（不编造目标线）
 */
// ========== ★ #82 目标达成水位 ==========
/** 后端 `metric_levels.recovery_rate`（含阈值与级别）；未配阈值时为 null */
/** 挽回率卡数据说明（含目标线来源 —— 让使用者知道"阈值可调"） */
// ========== 图表配置 ==========
// 四路经营退款路线配色 (A 线上直退 / B 钱包取现 / C 闪猫退款 / D 手动退款)
// ★ #80 投诉三源趋势配色（与投诉专项四卡、饼图共用同一套色，规范 §五 禁止漂移）
/**
 * ★ #80 把「按日」的三源投诉趋势对齐到趋势图槽位（用户诉求 3）。
 *
 * 为什么需要对齐（而不是直接用接口顺序）：
 *   退款走 `today/hourly-trend`（槽位可能是小时/日/周/月）；
 *   而投诉三源只有**按日**趋势（`complaints/summary.*.trend`，源于补充表逐日聚合）。
 *   → 只有槽位标签本身就是「MM-DD」时才能一一对齐；否则返回 null，
 *     由调用方退化为单一「投诉量」柱。
 *   ★ 宁可少拆，不可拆错 —— 错配会把 A 天的投诉画到 B 天，比不拆更糟。
 *
 * @returns {null | Object} null=不可拆（非按日粒度）; 否则 {internal:[],merchant:[],external:[]}
 */
/**
 * 组织趋势图 series。
 * ★ #80 改造（用户诉求 3）：
 *   ① 退款 —— A/B/C/D **四路堆叠柱**（配色与「退款笔数」卡 chips 一致）
 *   ② 投诉 —— **内部/商户号/外部 三源堆叠柱**（按日粒度时）
 * 取舍：
 *   ① **只堆叠笔数，金额改为半透明堆叠柱** —— 原来金额是四路面积线，
 *      与笔数柱叠在同一张图上互相遮挡；且 ABCD 金额混入非现金退出，
 *      相加无对账意义（真正的对账基准是 E，已在「退款金额」卡给出）。
 *   ② 三源堆叠仅在按日粒度可用，周/月聚合自动退化为单一「投诉量」柱，不强行拆。
 *   ③ 不在图例写「合计」字样 —— 投诉三源与「核心指标·投诉数」不同源（见 #81），
 *      写合计会让人误以为两处应相等。
 */
// ★ #85: **处理**单列一条线（柱=登记三源堆叠，线=处理总量）
//   口径: 后端 `complaint_done`（finish 落桶 + 真实完成，含往期单办结，不含卡商）
//   与四卡副行「处理」**同源**。
//   取舍: 处理**不按三源拆**（拆了线太多、且处理人/渠道归属另有专项页承接），
//         只给"该时段清掉多少"这一个总量信号 —— 与主管的动作直接对应。
//   ⚠️ 处理线**不能用 #10b981** —— 那是「接待数」柱的颜色，
//     同色会让同屏两根“绿色”无法区分（实测像素采样发现）→ 用 teal-700。
// ★ #85: 处理线图例（与柱分开，避免误读为"第四种来源"）
// ★ #80: 投诉三源堆叠（仅按日粒度可用，否则为 null → 退化单一柱）
// ========== 人均效率表 ==========
// 判定"企微人员":部门=企微 / 姓名前缀"企微-" / qw_user_id 已绑定
// ★ #78: AI/系统账户不进真人表（它们在下方单独一张小表展示）
// 按人视图下默认仅显示企微人员(宽松判定), 勾选后显示所有人
// ★ #78 AI / 系统账户单列（用户要求：「区分出来，单列也可以」）
//   设计说明: 此前 `is_virtual` 账户被各页直接过滤丢弃，AI 工作量完全不可见。
//   这里单列展示 —— 既能看到 AI 规模，也不挤占真人排名。
//   取舍: 不给名次（AI 不是人，排名无意义）；空则不渲染整块。
/** 该 AI/系统账户名下会话数占全站会话数的百分比（可控指标：AI 接管率） */
// 部门列显示策略:移动端 + 按人视图 + 仅企微时隐藏(表头已筛选); 其余保留
// ========== 退款挽回 ==========
// ★ #80（用户诉求 4）：原样返回 staff_details 所有人 → 「为 0 的不显示」。
//   ⚠️ 关键区分（不能一刀切"为0就隐藏"）：
//     · total_count == 0        → 本期**确实没接到挽回任务**，属噪声 → 隐藏
//     · total_count > 0 但成功 = 0 → **接了活但全失败** → 必须保留！
//       这正是主管最该看见的一类；滤掉就是掩盖问题。
//   排序：全失败者前置（用 allFail 作为一级排序键），让问题浮到顶上。
// 隐藏「本期无挽回任务」（total=0）—— 这些是噪声，不是问题
// 全失败（有任务、成功=0）置顶：主管一进来就看到谁需要帮扶
/** ★ #80: 本期无挽回任务（total=0）而被隐藏的人数 —— 必须告知，否则使用者
 *  会以为"人少了"是系统漏数据。规范 §二「空必须可解释」。 */
/** ★ #80: 全失败人数（有任务但成功 0）—— 标红提示 */
/** ★ #80: 成功占比（堆叠条用）。total=0 时给 0，避免除零。 */
/** ★ #80: 堆叠条悬浮说明（各段量级，规范 §二：视觉元素须说清含义） */
/** ★ #80: 成功率阈值配色（达标≥38% / 预警30-38% / 告警<30%）—— 与今日看板同阈值 */
// ========== 投诉专项 ==========
// ★★ 2026-09-11 (#53): (外)卡商 **移出饼图** ★★
//   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
//   卡商改为独立区块展示(见模板 card 区块)。
// 被投诉主体 Top5 (商户号+外部 堆叠, 仅统计有主体的来源)
// ========== 数据加载 ==========
// ★ #87: 失败不阻断整页（新指标，后端老版本/异常时降级为不渲染）
// ★ #87: 仅在拿到有效数据时赋值（避免接口异常时渲染空壳）

import { ref, computed, reactive, onMounted, onBeforeUnmount, watch, getCurrentInstance } from 'vue'
import { today } from '@/api/analytics'
import { listGroups } from '@/api/group'
import { complaintSummary, complaintDetail, complaintSubjectAnalysis, complaintHealthMetrics } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import weekOfYear from 'dayjs/plugin/weekOfYear'
import { buildStack, sourceKindMeta, RECEPTION_BREAKDOWN_TIP } from '@/utils/receptionSource'
import { getWeekRange, getWeekRangeStr, weekLabel } from '@/utils/weekRange'

export function useOperationDashboard() {
  dayjs.extend(isoWeek)

  dayjs.extend(quarterOfYear)

  dayjs.extend(weekOfYear)

  const loading = ref(false)

  const isMobile = ref(false)

  let _mq = null

// ========== 时间选择 ==========
  const grain = ref('week')

  const weekOffset = ref(0)

  const monthOffset = ref(0)

  const quarterOffset = ref(0)

  const yearOffset = ref(0)

  const customDateRange = ref(null)

  const trendGrain = ref('day')

  const quickGrainOpt = ref('all')

  const grainQuickOptions = [{ label: '全部', value: 'all' }]

// ========== 人员筛选 ==========
  const filterDepartment = ref('')

  const filterGroupId = ref(null)

  const groupsList = ref([])

  const staffFilter = reactive({
    aggregateBy: 'person',
  })

// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
  const showAllStaff = ref(false)

// 审计弹窗(点击人员/设备唤起)
  const { openPersonAudit, openDeviceAudit } = useAudit()

// ========== 图表配置 ==========
  const receptionPerspective = ref('count')

// 趋势图: 默认只显示接待,可勾选投诉/退款
  const trendSelectedGroups = ref(['reception'])

// 工作效率表: 各专项接待明细列 (按类型分开SN/接待数)
// ★ 2026-09-14 投诉专项合并：新增「投诉专项」列。
//   ⚠️ 旧「内部投诉/外部投诉」列**保留** —— 历史区间的工单仍按旧桶归集，
//      删掉会让历史数据无处显示（且非法重算历史）。
//   ⚠️ 列 key 必须与后端 `analytics.CAT_BUCKETS` 的 `cat_name` 逐字一致，
//      否则 `getSpecialtyPeople(row, col.key)` 取不到值 → 恒显示「—」。
  const SPECIALTY_COLS = [
    { key: '咨询接待', short: '咨询', color: '#3b82f6' },
    { key: '套餐专项', short: '套餐', color: '#f97316' },
    { key: '退款处理', short: '处理', color: '#ef4444' },
    { key: '退款挽回', short: '挽回', color: '#06b6d4' },
    { key: '内部投诉', short: '内诉', color: '#7c3aed' },
    { key: '外部投诉', short: '外诉', color: '#dc2626' },
    { key: '投诉专项', short: '投诉', color: '#be123c' },
  ]

/** ★ 岗位 → 徽标（用户 2026-09-14：「组长」「管理」要一眼可辨）
 *
 *  设计要点:
 *   · **管理岗**（主管）→ 文案「管理」：使用者关心的是"他能拍板什么"，
 *     而非"他叫什么级别"。
 *   · **组长/副主管** → 文案「组长」：值班/带组语境下同属组级管理者。
 *   · 专员类（投诉专员/退款处理专员）→ 显示**原文**（不缩写）：
 *     "投诉专员"本身信息量足够，缩写反而模糊。
 *   · 客服 → 浅色文字（数量最多，高亮会淹没管理岗）。
 *   ⚠️ 不得用 `is_management` 判断 —— 它含专员类跨类豁免岗（投诉专员等），
 *     那些**不是**管理岗。
 */
  const POSITION_META = {
    主管: { label: '管理', bg: '#b91c1c' },
    副主管: { label: '组长', bg: '#b45309' },
    组长: { label: '组长', bg: '#1d4ed8' },
  }

  function positionMeta(pos) {
    if (!pos) return null
    return POSITION_META[pos] || null
  }

  function getSpecialtyPeople(row, catKey) {
    const bd = row.breakdown || {}
    const info = bd[catKey]
    return info ? Number(info.people || 0) : 0
  }

// ========== 数据存储 ==========
  const kpiData = ref({})

  const hourlyData = ref({})

  const receptionData = ref({})

  const refundData = ref({})

  const staffData = ref({})

  const recoveryData = ref({})

  const hasRecovery = computed(() => recoveryData.value && recoveryData.value.kpi)

// 投诉专项数据
  const complaintSummaryData = ref({})

  const complaintList = ref([])

// ★ #87: 取组件实例代理 —— 用于在 setup 函数内做路由跳转
//   （本文件未引入 useRouter，模板里用的是 `$router`；此处保持一致写法）
  const proxy = getCurrentInstance()?.proxy

// 被投诉主体维度数据
  const complaintSubjectData = ref({})

// ★ #87 投诉健康度指标（活跃SN / 投诉率三维 / 岗位负荷 / 重复投诉率）
  const healthMetrics = ref(null)

// workbuddy 推送补充数据源(315/卡商, 按SN关联工单)
  const supplExternal = computed(() => complaintSummaryData.value?.supplementary?.external || { total: 0, matched: 0 })

// #47 (2026-09-10): 卡商补充数据源(单列, 原先并入外部)
  const supplCard = computed(() => complaintSummaryData.value?.supplementary?.card || { total: 0, matched: 0 })

  const supplSummaryText = computed(() => {
    const s = complaintSummaryData.value?.supplementary || {}
    const ext = s.external || { total: 0, matched: 0 }
    const card = s.card || { total: 0, matched: 0 }
    if (!ext.total && !card.total) return ''
    return `补充数据源(按SN关联工单): 315外部投诉 ${formatNum(ext.total)} 条(已关联 ${formatNum(ext.matched)}) · 卡商群投诉 ${formatNum(card.total)} 条(已关联 ${formatNum(card.matched)})`
  })

// ★ #80 三源合计（当前窗口内，来自 reg_handled —— 与四卡同源，可安全相加）
  const complaintThreeTotal = computed(() => {
    const r = complaintSummaryData.value?.reg_handled || {}
    const t = r.total
    if (t && typeof t === 'object') return Number(t.registered ?? t.total ?? 0)
    return ['内部投诉', '商户号投诉', '外部投诉']
      .reduce((s, k) => s + Number(r[k]?.registered ?? r[k]?.total ?? 0), 0)
  })

/** 只有拿到三源数据才显示合计提示（避免显示 0 误导） */
  const complaintTotalHint = computed(() => complaintThreeTotal.value > 0)

/** ★ #81 口径一致性说明（页面 ⓘ）—— 使用者最容易困惑的地方，必须给解释 */
  const complaintScopeTip = [
    '<b>本区四卡与上方「核心指标·投诉数」什么关系？</b>',
    '<b>#81 (2026-09-13) 已统一为同源同口径</b>，两处数字<b>逐位一致</b>：',
    '· 判据 = 工单按 <b>complaint_scope 分桶</b>（内部/商户号/外部，含渠道回退 + #58 软纠正）',
    '　　+ 315 补充表<b>净新增</b>（已扣已关联工单的重叠，不重复计数）',
    '· 后端实现 = `today_kpi` 直接调用 `complaints._sources_reg_handled`（单一出口）',
    '<b>卡商不计入本合计</b> —— 按 #53「(外)卡商 不纳入总量的管理，全部单算」，',
    '　卡商在下方**独立区块**呈现（工单侧真卡商 + card_complaints − 重叠）。',
    '<br/><b>修正前的历史问题（已解决，勿再参照旧说法）</b>：',
    '· 核心指标曾为『工单 + 315表全量 + 卡商表全量』→ ① 含卡商(违反 #53)',
    '　② 315 表已关联工单的记录被重复计入 → 与四卡差 37 条(本月实测)',
    '· 现统一后：<b>括弧三源之和 == 主值</b>，可放心相加核对。',
  ].join('<br/>')

/** ★ #80: 趋势图数据说明（页面层 ⓘ，规范 §二 双强制之①） */
  const TREND_TIP = [
    '<b>来源</b>：节拍 = /analytics/today/hourly-trend；投诉三源 = /complaints/summary 的 trend',
    '<b>口径</b>：',
    '· 接待 = 会话数(cs_records 计数) 与 接待数(去重设备SN)，两柱并列',
    '· 退款 = 四路经营笔数 A线上直退 + B钱包取现 + C闪猫退款 + D手动退款，**堆叠柱**',
    '　　（按创建时间；四路之和 = 上方「退款笔数」卡）',
    '· 退款金额 = 同样四路，**半透明堆叠柱**（右轴），仅作量级参照',
    '· 投诉 = **登记(柱) + 处理(线)** —— 柱按内部/商户号/外部三源堆叠；',
    '　　线 = 该时段**处理完成量**（完成时间落桶 + 真实完成≥3min，**含往期单办结**）',
    '　　口径与上方核心指标及四卡**同源**（不含卡商，已扣 315 补充表重叠）',
    '　　→ 柱 > 线 = 在累积；柱 < 线 = 在消化',
    '<b>时点</b>：跟随顶部时间条；本页缓存 24h（复盘场景，非实时）',
    '<b>「空」的含义</b>：某段不显示 = 该时段该来源无记录（**不是**数据未加载）；',
    '　　整图全空 = 本窗口无数据，或所选粒度超出数据覆盖范围',
    '<b>下钻</b>：⚠️ <b>本图不做点击下钻</b> —— 明细接口按【日】筛选，无小时级能力，',
    '　　强行下钻会落到"那一天的全部明细"，与所点时段不符（规范 §3.4 声明）。',
    '　　请用下方各专项区块下钻（那里已带窗口）。',
    '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
    '① <b>不堆叠金额</b> —— ABCD 金额混入"退入钱包"等<b>非现金退出</b>，相加无业务意义；',
    '　　（真正的对账基准是 E＝实际现金流，已在「退款金额」卡单列）',
    '② <b>投诉三源只在按日粒度拆</b> —— 补充表 trend 仅到日；周/月聚合会拆错，',
    '　　故自动退化为单一「投诉量」柱（宁可少拆，不可拆错）',
    '③ <b>图例不写"合计"</b> —— 投诉三源与「核心指标·投诉数」不同源（见 #81），',
    '　　写合计会让人误以为两处应相等',
  ].join('<br/>')

// ========== 投诉三源（#81 口径统一后的唯一出口）==========
// ★ #81 (2026-09-13): 核心指标「投诉数」的口径已与「投诉专项四卡」**统一同源**
//   —— 后端 `today_kpi` 现直接调用 `complaints._sources_reg_handled`。
//   故此处可以安全地做「括弧三源」（#80 诉求 2）：
//   三源之和 == 主值，不会出现括弧与总数对不上的误导。
//   ⚠️ 三源**不含 (外)卡商**（#53: 卡商独立统计, 全部单算）。
//   数据源: complaints/summary 的 reg_handled（与四卡同源，保证逐位一致）。
  const COMPLAINT_SOURCE_CHIPS = [
    { key: '内部投诉', label: '内部', color: '#2563eb' },
    { key: '商户号投诉', label: '商户', color: '#7c3aed' },
    { key: '外部投诉', label: '外部', color: '#dc2626' },
  ]

/** 投诉数卡的「括弧三源」chips（每源取其 registered，与主值同窗口同判据） */
  const complaintChips = computed(() => {
    const rh = complaintSummaryData.value?.reg_handled || {}
    // 无数据时不渲染 chips（避免显示三个 0 误导）
    if (!COMPLAINT_SOURCE_CHIPS.some((c) => Number(rh[c.key]?.registered) > 0)) return null
    return COMPLAINT_SOURCE_CHIPS.map((c) => ({
      label: c.label,
      value: Number(rh[c.key]?.registered || 0),
      color: c.color,
    }))
  })

/** 投诉数卡的数据说明（来源/口径/时点/取舍 —— 看板卡片规范 §二 双强制） */
  const complaintTip = computed(() => {
    const rh = complaintSummaryData.value?.reg_handled || {}
    const parts = COMPLAINT_SOURCE_CHIPS
      .map((c) => `${c.label}投诉 ${formatNum(rh[c.key]?.registered)} 条`)
      .join(' · ')
    return [
      '<b>来源</b>：/analytics/today/kpi 的 complaint（后端直接调用 complaints 的三源出口）',
      `<b>构成</b>：${parts || '（本窗口暂无数据）'}`,
      '　→ 三源之和 <b>等于</b>本卡数值（#81 口径统一保证）',
      '<b>口径</b>：工单按 <b>complaint_scope 分桶</b>（内部/商户号/外部，含渠道回退 + #58 软纠正）',
      '　+ 315 补充表<b>净新增</b>（已扣除已关联工单的重叠，不重复计数）',
      '　<b>不含 (外)卡商</b> —— 卡商按 #53 独立统计、全部单算，见下方独立区块',
      '<b>时点</b>：跟随顶部时间条；本页缓存 24h（复盘场景，非实时）',
      '<b>「空」的含义</b>：显示 0 = 本窗口确无该来源投诉；下方三个括弧不显示 = 三源全为 0',
      '<b>下钻</b>：点本卡 → 投诉分析页（带当前时间窗口与三源维度）',
      '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
      '① <b>不含卡商</b> —— 卡商是独立体系（#53 用户决策），并入会使总量口径混乱',
      '② <b>不按来源再拆"登记/处理"</b> —— 本卡是「发生量」，处理情况在下方专项区块承接，',
      '　　避免一张卡塞两种语义（登记 vs 处理）导致误读',
      '③ 修正前本卡为『工单 + 315表全量 + 卡商表全量』（不扣重且含卡商），与四卡对不上（差 37 条）；',
      '　　#81 已统一为同源，此卡与四卡<b>逐位一致</b>。',
    ].join('<br/>')
  })

// ========== ★ #82 积压账龄分档 ==========
/** 后端 `backlog.aging`（全量未完成投诉工单按账龄分档）。空则不渲染。 */
  const backlogAging = computed(() => {
    const a = complaintSummaryData.value?.backlog?.aging
    if (!a || !a.total) return null
    return a
  })

  const backlogAgingTotal = computed(() => Number(backlogAging.value?.total || 0))

/** >30 天部分（需决策清理的部分 —— 这才是"该不该管"的答案） */
  const overdueCount = computed(() => {
    const a = backlogAging.value
    if (!a) return 0
    return Number(a.d31_90 || 0) + Number(a.d90_plus || 0)
  })

/** 四档配色：越老越红（绿→黄→橙→红），让"陈旧"在视觉上自然浮出 */
  const BACKLOG_AGING_SEGMENTS = [
    { key: 'd0_7', short: '0-7天', label: '0-7 天（正常压力）', color: '#16a34a' },
    { key: 'd8_30', short: '8-30天', label: '8-30 天（需关注）', color: '#ca8a04' },
    { key: 'd31_90', short: '31-90天', label: '31-90 天（已积压，需介入）', color: '#ea580c' },
    { key: 'd90_plus', short: '>90天', label: '>90 天（陈年烂账）', color: '#dc2626' },
  ]

/** ★ #87 投诉健康度数据说明（页面层 ⓘ，规范 §二 双强制之①）
 *
 *  本卡组与上方「投诉专项四卡」的关系：四卡看**绝对量**，本组看**相对水平**。
 *  两者不可互相替代 —— 绝对量无法回答"好不好"，相对水平无法回答"有多少要处理"。
 */
  const healthTip = [
    '<b>来源</b>：/complaints/health-metrics（#87 第一批指标）',
    '<b>口径</b>：',
    '· <b>活跃SN基数</b> = 窗口内 <code>cs_records</code> 有接待记录的去重设备SN',
    '　　→ 它是<b>所有投诉率的分母基底</b>（为何需要它见下方"取舍"）',
    '· <b>投诉率</b> = 投诉SN ÷ 活跃SN。投诉判据 = 工单服务类别<b>空白或「投诉」</b>',
    '　　（客服登记投诉时不选类别 → 导出为空白，故空白即投诉；⚠️ 不可用 LIKE \'%投诉%\'）',
    '　<b>不含 (外)卡商</b>（#53 卡商独立统计、全部单算）',
    '· <b>重复投诉率</b> = 30 天内<b>同一设备(SN)</b>被投诉 ≥2 次的条数 ÷ 投诉工单数',
    '　⚠️ 这是<b>升级的代理信号</b>，<b>不是真实升级率</b> —— 真实升级率需升级投诉表',
    '　　（客服申报 / 组长化解字段）采集到位后才能严格计算',
    '<b>时点</b>：跟随顶部时间条；本页缓存 24h（复盘场景，非实时）',
    '<b>「空」的含义</b>：',
    '· 比率显示 <b>「—」</b> = 该窗口<b>活跃SN为0</b>（无接待记录）→ 分母不存在，',
    '　　<b>不是 0%</b>。切勿读成"零投诉"。（规范：空必须可解释）',
    '· 某维度显示 0.00% = 该窗口确无该来源投诉（分母有效）',
    '· 整块不渲染 = 接口未返回数据',
    '<b>下钻</b>：点任一比率卡 → 投诉分析页（带当前时间窗口与来源筛选）',
    '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
    '① <b>不叫「升级率」</b> —— 现有数据只能算"同SN重复投诉"，把它叫升级率就是',
    '　　<b>口径漂移</b>（同一个概念两套实现，正是 #81/#83/#85 的病根）。宁可名字保守。',
    '② <b>必须给出活跃SN</b>，而不是只给投诉数 —— 否则无法区分这两种截然不同的局面：',
    '　　投诉↓ + 活跃↓ = 可能是<b>客户流失</b>（危险信号）；',
    '　　投诉↓ + 活跃→ = <b>真实改善</b>（值得肯定）。只看绝对数会把后者当成绩、把前者当喜报。',
    '③ <b>不加趋势图</b> —— 第一批先把"当期水位"看清；趋势图属第二批（避免一次铺太大）。',
    '④ <b>不并入上方四卡</b> —— 四卡是"发生量/处理量"台账，本组是"相对水平"评价，',
    '　　混在一区会让使用者分不清哪个是"要处理的数"。',
  ].join('<br/>')

/** ★ #87 岗位负荷数据说明 */
  const healthPositionTip = [
    '<b>口径</b>：岗位负荷 = <b>岗位归因分子</b> ÷ 在岗人数',
    '· <b>组长 / 投诉专员</b> 分子 = 该岗位人员在窗口内<b>完成的投诉结案数</b>',
    '　　（真实完成 = 完成时间落窗 且 完成−登记 ≥3 分钟；与投诉四卡"处理"同口径）',
    '· <b>客服</b> 分子 = <b>接待数</b>（接待会话数）',
    '　　→ 分子口径<b>故意不同</b>：客服的职责是接待，不是结案（用户 2026-09-14 确认）',
    '<b>岗位归属</b>：取自 <code>person.position</code> / <code>specialty</code>',
    '　（已与 SCD 任职历史源同步；处理人经别名表匹配，实测覆盖 100%）',
    '· 投诉专员 = 专项 ∈ (投诉专项 / 内部投诉 / 外部投诉)　· 组长含副主管',
    '　（2026-09-14 起内/外部投诉已合并为「投诉专项」；旧两项保留供历史归因）',
    '<b>「空」的含义</b>：显示 <b>「—」</b> = 该岗位<b>当前无在岗人员</b>（人数为0），',
    '　分母不存在 → 无法计算人均；<b>不是 0 工作量</b>。',
    '· 显示 0 = 该岗位有在岗人员但窗口内无归因工作量（如请假/转岗）',
    '<b>下钻</b>：⚠️ 本表<b>不做点击下钻</b> —— 明细接口按【日】筛选，',
    '　而"岗位归因"是处理人维度的计算列，无法透传。',
    '　需要看某人明细请用下方「处理人 Top10」或「工作效率值排名」。',
    '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
    '① <b>分子按岗位归因，而非用"全站结案总数"</b> —— 曾误用总量导致组长与专员人均',
    '　　完全相同（同一分子÷同一分母），该数字无任何管理意义。',
    '② <b>保留「归因合计」行</b> —— 让"未登记岗位/主管"的工作量可见，',
    '　　避免读者以为三类之和应等于全站结案数（差额就是"其他"）。',
    '③ <b>不与「工作效率值排名」合并</b> —— 那张表已含复杂权重系数（跨类豁免等），',
    '　　本表用<b>朴素口径</b>（条数÷人数），便于非专业读者核对。（详见工单 #68）',
  ].join('<br/>')

/** 比率格式化：null/undefined → 「—」（分母为0，不是 0%） */
  function fmtRate(v) {
    if (v === null || v === undefined || v === '') return '—'
    return `${Number(v).toFixed(2)}%`
  }

/** ★ #87 下钻：投诉率 → 投诉分析页（带窗口 + 来源）
 *  ⚠️ 使用组件实例的 `proxy.$router`（本文件未引入 useRouter，
 *     与模板中既有的 `$router.push` 写法保持一致）。
 */
  function drillToComplaintAnalysis(source) {
    const { start, end } = getDateRange()
    const q = { tab: 'analysis', view: source ? 'channel' : 'analysis', start, end }
    if (source) q.source = source === '外部投诉' ? 'external' : 'internal'
    proxy.$router.push({ path: '/dashboards/complaint-analysis', query: q })
  }

/** ★ #87 下钻：重复投诉 → 明细页（同SN重复无法在明细接口透传，落到窗口内投诉列表） */
  function drillToRepeatComplaint() {
    const { start, end } = getDateRange()
    proxy.$router.push({
      path: '/dashboards/complaint-analysis',
      query: { tab: 'detail', start, end },
    })
  }

/** ★ #87: 兼办投诉量（非组长/专员完成的投诉结案数）—— 暴露岗位临时调配 */
  const healthOtherDuty = computed(() => {
    const n = healthMetrics.value?.by_position?.find((p) => p.bucket === '兼办投诉')?.numerator
    return Number(n || 0)
  })

/** 兼办占比（相对全站投诉结案数） */
  const healthOtherDutyPct = computed(() => {
    const tot = Number(healthMetrics.value?.attribution?.total_handled || 0)
    if (!tot) return '0'
    return ((healthOtherDuty.value * 100) / tot).toFixed(1)
  })

/** 积压账龄数据说明（页面层 ⓘ，看板卡片规范 §二 双强制） */
  const backlogAgingTip = [
    '<b>来源</b>：/complaints/summary 的 backlog.aging',
    '<b>口径</b>：**全量**未完成投诉工单，按 register_time 至今的账龄分档',
    '　· 未完成 = status ∉（已结案/已完成/完成/已解决/已回复/已反馈/已关闭/终止/已终止）',
    '　· 315 补充表已办结的关联单视为已完成（不进积压，与上方同规则）',
    '　· <b>不含 (外)卡商</b> —— 卡商按 #53 独立统计、不纳入投诉总量',
    '<b>时点</b>：跟随数据最新状态（非窗口筛选）；本页缓存 24h',
    '<b>「空」的含义</b>：某档显示 0 = 该账龄区间确无未结单（不是数据缺失）；整卡不显示 = 全量积压为 0',
    '<b>下钻</b>：⚠️ 本卡**不做逐档下钻** —— 账龄是计算列，明细接口按【日】筛选，',
    '　无法透传账龄条件。请点下方「投诉明细」或跳投诉分析页（那里可按状态筛未完成）。',
    '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
    '① <b>不改上方「积压待处理」卡</b> —— 两者含义确实不同：',
    '　上方=**近30天窗口内**登记的未完成 → 近期消化压力；',
    '　本卡=**全量**未完成按账龄分档 → 历史包袱。',
    '　实测差异极大（近30天 46 条 vs 全量 436 条）→ 改口径会造成数字变动争议，故并列保留。',
    '② <b>不做饼图</b> —— 四档是递进关系（越老越糟），横向条更易比较多少与占比。',
    '③ <b>不做逐档下钻</b> —— 见上方「下钻」说明（技术能力边界，非偷懒）。',
  ].join('<br/>')

// 投诉明细表 筛选/排序状态 (KPI 卡片点击 = 来源筛选)
  const complaintSourceFilter = ref('')

  const complaintCategoryFilter = ref('')

  const complaintStatusFilter = ref('')

/** ★ #80: 投诉明细接口固定取前 500 条（见 loadAll 的 complaintDetail 调用）。
 *  必须与调用处保持一致，否则提示文案会与实际截断点不符。 */
  const COMPLAINT_DETAIL_LIMIT = 500

/** ★ #80 硬伤 B: 是否触达截断上限 → 用于显示"明细可能不全"警告。
 *  判据: 返回条数 >= 上限（后端是 LIMIT，达到上限即可能还有更多）。 */
  const complaintTruncated = computed(() => complaintList.value.length >= COMPLAINT_DETAIL_LIMIT)

  function toggleComplaintSourceFilter(src) {
    complaintSourceFilter.value = complaintSourceFilter.value === src ? '' : src
  }

  function clearComplaintFilters() {
    complaintSourceFilter.value = ''
    complaintCategoryFilter.value = ''
    complaintStatusFilter.value = ''
  }

// 时间字符串排序(ISO), 支持空值排在后
  function sortTimeStr(a, b) {
    const va = a || ''
    const vb = b || ''
    if (va === vb) return 0
    return va < vb ? -1 : 1
  }

// 来源/分类/状态 可选项(el-table :filters 需要 {text,value} 形式)
  const complaintSourceFilters = computed(() => {
    const set = new Set(complaintList.value.map(r => r.source).filter(Boolean))
    return [...set].map(v => ({ text: v, value: v }))
  })

  const complaintCategoryFilters = computed(() => {
    const set = new Set(complaintList.value.map(r => r.category).filter(Boolean))
    return [...set].map(v => ({ text: v, value: v }))
  })

  const complaintStatusFilters = computed(() => {
    const set = new Set(complaintList.value.map(r => r.status).filter(Boolean))
    return [...set].map(v => ({ text: v, value: v }))
  })

// 明细表最终数据 = 来源(KPI点击/列内筛选二者合并) + 分类 + 状态 三级筛选
  const filteredComplaintList = computed(() => {
    let list = complaintList.value || []
    if (complaintSourceFilter.value) {
      list = list.filter(r => r.source === complaintSourceFilter.value)
    }
    if (complaintCategoryFilter.value) {
      list = list.filter(r => r.category === complaintCategoryFilter.value)
    }
    if (complaintStatusFilter.value) {
      list = list.filter(r => r.status === complaintStatusFilter.value)
    }
    return list
  })

// ========== 选项 ==========
// ⚠️ 2026-09-13 修复存量 bug: 原用 `startOf('week').add(1,'day')`,
//    而 dayjs 的周起始是**周日** → 周日当天「本周」会被算成下周（09-14~09-20，未来）
//    → 指标全为 0。改用共享 getWeekRange（周一起始，单一来源）。
  const weekOptions = computed(() => {
    const opts = []
    for (let i = 0; i < 12; i++) {
      const { start, end } = getWeekRange(undefined, i)
      opts.push({ label: weekLabel(i), value: i, range: `${start.format('MM-DD')}~${end.format('MM-DD')}` })
    }
    return opts
  })

  const monthOptions = computed(() => {
    const opts = []
    for (let i = 0; i < 12; i++) {
      const d = dayjs().subtract(i, 'month')
      const label = i === 0 ? '本月' : i === 1 ? '上月' : d.format('YYYY年MM月')
      opts.push({ label, value: i })
    }
    return opts
  })

  const quarterOptions = computed(() => {
    const opts = []
    for (let i = 0; i < 8; i++) {
      const d = dayjs().subtract(i, 'quarter')
      const label = i === 0 ? '本季度' : i === 1 ? '上季度' : `${d.year()}年Q${d.quarter()}`
      opts.push({ label, value: i })
    }
    return opts
  })

  const yearOptions = computed(() => {
    const opts = []
    for (let i = 0; i < 3; i++) {
      const d = dayjs().subtract(i, 'year')
      const label = i === 0 ? '今年' : i === 1 ? '去年' : `${d.year()}年`
      opts.push({ label, value: i })
    }
    return opts
  })

// ========== 标签 ==========
  const grainLabel = computed(() => {
    const gMap = { week: '周视图', month: '月视图', quarter: '季度视图', year: '年视图' }
    const tMap = { day: '·按日', week: '·按周', month: '·按月' }
    return (gMap[grain.value] || '周视图') + (tMap[trendGrain.value] || '')
  })

  const grainTagType = computed(() => ({ week: 'primary', month: 'warning', quarter: 'danger', year: 'info' }[grain.value] || 'info'))

  const periodName = computed(() => {
    if (customDateRange.value) return '自定义范围'
    switch (grain.value) {
      case 'week': {
        const i = weekOffset.value
        return i === 0 ? '本周' : i === 1 ? '上周' : `第${dayjs().subtract(i, 'week').isoWeek()}周`
      }
      case 'month': {
        const i = monthOffset.value
        return i === 0 ? '本月' : i === 1 ? '上月' : dayjs().subtract(i, 'month').format('YYYY年MM月')
      }
      case 'quarter': {
        const i = quarterOffset.value
        return i === 0 ? '本季度' : i === 1 ? '上季度' : `${dayjs().subtract(i, 'quarter').year()}年Q${dayjs().subtract(i, 'quarter').quarter()}`
      }
      case 'year': {
        const i = yearOffset.value
        return i === 0 ? '今年' : i === 1 ? '去年' : `${dayjs().subtract(i, 'year').year()}年`
      }
    }
    return ''
  })

  const compareName = computed(() => {
    if (customDateRange.value) return '对比期'
    switch (grain.value) {
      case 'week': {
        const i = weekOffset.value + 1
        return i === 1 ? '上周' : `前${i}周`
      }
      case 'month': {
        const i = monthOffset.value + 1
        return i === 1 ? '上月' : `前${i}月`
      }
      case 'quarter': {
        const i = quarterOffset.value + 1
        return i === 1 ? '上季度' : `前${i}季`
      }
      case 'year': {
        const i = yearOffset.value + 1
        return i === 1 ? '去年' : `前${i}年`
      }
    }
    return ''
  })

  const displayDate = computed(() => periodName.value)

  const rhythmSub = computed(() => {
    if (grain.value === 'week') return '按日聚合'
    if (grain.value === 'month') return '按日聚合'
    return `按${trendGrain.value === 'day' ? '日' : trendGrain.value === 'week' ? '周' : '月'}聚合`
  })

// ========== 日期范围计算 ==========
  function getDateRange() {
    if (customDateRange.value) {
      return { start: customDateRange.value[0], end: customDateRange.value[1] }
    }
    const now = dayjs()
    let start, end
    switch (grain.value) {
      case 'week': {
        // ⚠️ 2026-09-13 修复: 原 `now.subtract(offset,'week').startOf('week').add(1,'day')`
        //   在周日当天会把「本周」算成下一周（未来）→ 全部指标为 0。
        //   改用共享 getWeekRange（周一起始）+ clamp 到今天，避免拉取未来日期。
        const r = getWeekRangeStr(undefined, weekOffset.value, true)
        start = r.start
        end = r.end
        break
      }
      case 'month': {
        const offset = monthOffset.value
        const monthDate = now.subtract(offset, 'month')
        start = monthDate.startOf('month').format('YYYY-MM-DD')
        end = monthDate.endOf('month').format('YYYY-MM-DD')
        break
      }
      case 'quarter': {
        const offset = quarterOffset.value
        const quarterDate = now.subtract(offset, 'quarter')
        start = quarterDate.startOf('quarter').format('YYYY-MM-DD')
        end = quarterDate.endOf('quarter').format('YYYY-MM-DD')
        break
      }
      case 'year': {
        const offset = yearOffset.value
        const yearDate = now.subtract(offset, 'year')
        start = yearDate.startOf('year').format('YYYY-MM-DD')
        end = yearDate.endOf('year').format('YYYY-MM-DD')
        break
      }
    }
    return { start, end }
  }

// ========== KPI卡 ==========
  function _pctDelta(cur, prev) {
    if (prev == null || Number(prev) === 0) return null
    return Math.round(((Number(cur) - Number(prev)) / Number(prev)) * 1000) / 10
  }

  const kpiCards = computed(() => {
    const d = kpiData.value || {}
    const reception = d.reception || {}
    const refundCnt = d.refund_count || {}
    const refundAmt = d.refund_amount || {}
    const complaint = d.complaint || {}
    const recoveryRate = recoveryData.value?.kpi?.success_rate
    // ★ #80: 四路经营退款拆分（后端 `cash_routes_today` 已返回 route_a~d 的 count/amount）。
    //   用途: 「退款笔数」卡下方括弧显示 A/B/C/D 各自笔数，
    //        「退款金额」卡下方显示四路金额 —— 让「四路合计」不再是黑箱。
    //   ⚠️ 金额是元（后端已 rounded 2 位），前端统一折算为万元显示，避免长数字撑破卡片。
    const routes = d.cash_routes_today || {}
    const routeOf = (k) => routes[k] || { count: 0, amount: 0 }
    const _wan = (v) => `${(Number(v || 0) / 10000).toFixed(2)}万`
    const routeChips = REFUND_ROUTES.map((r) => ({
      label: r.short,
      value: routeOf(`route_${r.key}`).count,
      color: r.color,
    }))
    const routeAmtRows = REFUND_ROUTES.map((r) => ({
      label: r.short,
      text: _wan(routeOf(`route_${r.key}`).amount),
      color: r.color,
    }))
    // 实际现金流 E —— 与 ABCD 经营估算口径不同（E 才是真实现金退出）
    const cashE = d.actual_cashflow || { value: 0 }
    return [
      // ★ #78: stack 由后端 reception.breakdown 构造（null 时不渲染）
      { key: 'reception', label: '接待数', value: reception.device_count, unit: '个', icon: 'Service', bg: '#eff6ff', color: '#2563eb', yoy_key: 'reception', yoy_pct: _pctDelta(reception.device_count, reception.device_count_yoy), yoy_value: reception.device_count_yoy, device_count: null, stack: buildStack(reception.breakdown, 'devices'), tip: '接待设备去重数' },
      { key: 'session', label: '会话数', value: reception.value, unit: '次', icon: 'ChatDotRound', bg: '#f0fdf4', color: '#16a34a', yoy_key: 'session', yoy_pct: reception.yoy_pct, yoy_value: reception.yoy_value, stack: buildStack(reception.breakdown, 'sessions'), tip: '含客服接待+系统处理' },
      // ★ #80: 退款笔数 —— 括弧展开四路笔数（A线上直退/B钱包取现/C闪猫/D手动）
      //   四路之和 == 主值（后端 operational.count 就是 A+B+C+D）。
      {
        key: 'refund_count',
        label: '退款笔数',
        value: refundCnt.value,
        unit: '笔',
        icon: 'Warning',
        bg: '#fef2f2',
        color: '#dc2626',
        yoy_key: 'refund_count',
        yoy_pct: refundCnt.yoy_pct,
        yoy_value: refundCnt.yoy_value,
        chips: routeChips,
        // ★ #83: 退款笔数也接目标水位 —— #83 已修正其口径为四路合计（与看板同源）
        //   且阈值经分布验证（400/460）。修法: 与挽回卡同一个 `metricLevelByCode`。
        metricLevel: metricLevelByCode('refund_count'),
        tip: '四路经营合计 A线上直退+B钱包取现+C闪猫退款+D手动退款（按创建时间）。下方颜色与趋势图四路堆叠柱一致',
      },
      // ★ #80: 退款金额 —— 副行给四路金额；并补一条「实际现金流E」对照
      //   ⚠️ 铁律: ABCD 是**经营记账**（含退入钱包这类非现金退出），E 才是**真实现金流出**。
      //     两者不相等是正常的，不是 bug（见 docs/standards 与 #20）。
      {
        key: 'refund_amount',
        label: '退款金额',
        value: refundAmt.value,
        money: true,
        icon: 'Money',
        bg: '#fff7ed',
        color: '#ea580c',
        yoy_key: 'refund_amount',
        yoy_pct: refundAmt.yoy_pct,
        yoy_value: refundAmt.yoy_value,
        subRows: [
          ...routeAmtRows,
          { label: '实际现金流E', text: _wan(cashE.value ?? cashE), color: '#0f766e' },
        ],
        tip: '退款金额 = 四路经营合计 A+B+C+D（登记侧所有退款出款，含退入钱包等非现金退出）；「实际现金流E」= 退款记录报表退款成功合计（真实现金退出）。两者不等属正常',
      },
      // ★ #81: 括弧三源 —— ⚠️ 必须写 `.value`！
      //   `complaintChips` / `complaintTip` 是 computed ref，在**另一个 computed 内部**
      //   访问时不会被 Vue 自动解包（自动解包只发生在模板里）→ 直接写 `chips: complaintChips`
      //   会把 ref 对象塞进 props，`v-if="chips && chips.length"` 恒为 undefined → 静默不渲染。
      //   ★ 教训: props 传 computed 结果时必须显式 `.value`（实测踩过一次）。
      { key: 'complaint', label: '投诉数', value: complaint.value, unit: '条', icon: 'Bell', bg: '#faf5ff', color: '#7c3aed', yoy_key: 'complaint', yoy_pct: complaint.yoy_pct, yoy_value: complaint.yoy_value, chips: complaintChips.value, tip: complaintTip.value },
      // ★ #82: 挽回率卡接「目标达成水位」—— 阈值来自后端 metric_thresholds，
      //   前端只负责显示。这是本页**唯一**接入水位的指标，原因（重要）：
      //   ⚠️ 2026-09-13 生产实测发现其余指标的阈值**严重错配** + 口径与看板卡不同源：
      //        refund_count 实际 314 / 阈值 30-50（差10倍，且只算A路单表）
      //        ticket_backlog 实际 599 / 阈值 20-50（差30倍）
      //        internal_complaint_daily 实际 123 / 阈值 8-15（差15倍）
      //     若强行接上会显示「退款笔数严重告警」（其实很正常）→ **比不显示更糟**，
      //     会训练使用者忽略告警。已开 #83 治理，治理完成后再扩展接入。
      //   `recovery_rate` 通过双重验证：① 口径同源（底层 refund_recovery_analysis
      //     与看板挽回卡一致）② 阈值 40/30/20 与 Today.vue 既有业务规则吻合。
      { key: 'recovery', label: '挽回率', value: Number(recoveryRate || 0), unit: '%', icon: 'SuccessFilled', bg: '#ecfdf5', color: '#059669', yoy_key: 'recovery', yoy_pct: null, yoy_value: null, metricLevel: metricLevelByCode('recovery_rate'), tip: recoveryMetricTip },
    ]
  })

/**
 * ★ #82/#83 通用水位取值：从后端 `metric_levels` 按 code 取。
 *
 * ⚠️ 为什么抽成函数而非直接写 `kpiData.value?.metric_levels?.xxx`：
 *   ① 避免每个卡各写一遍可选链（写错即静默不渲染，实测踩过）；
 *   ② #83 起接入多个指标，统一入口便于后续扩展。
 * @param {string} code 指标 code（须与后端 METRIC_DEFS 一致）
 * @returns {object|null} null 时 KpiCard 不渲染水位（不编造目标线）
 */
  function metricLevelByCode(code) {
    return kpiData.value?.metric_levels?.[code] || null
  }

// ========== ★ #82 目标达成水位 ==========
/** 后端 `metric_levels.recovery_rate`（含阈值与级别）；未配阈值时为 null */
  const recoveryMetricLevel = computed(() => kpiData.value?.metric_levels?.recovery_rate || null)

/** 挽回率卡数据说明（含目标线来源 —— 让使用者知道"阈值可调"） */
  const recoveryMetricTip = [
    '<b>来源</b>：/analytics/today/kpi 的 reception/refund_recovery（与退款挽回专项同源）',
    '<b>口径</b>：挽回成功率 = 挽回成功SN / 申请SN（按 SN 计，T+7 窗口）',
    '<b>目标线</b>：来自指标注册表 <b>metric_thresholds</b>（可由管理员调整，前端不硬编码）',
    '　优秀 ≥40% · 预警 &lt;30% · 告警 &lt;20%',
    '<b>时点</b>：跟随顶部时间条；本页缓存 24h',
    '<b>「空」的含义</b>：显示 0 = 本窗口无挽回申请；徽标不显示 = 未配阈值或窗口非单日',
    '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
    '① <b>只在【单日】窗口判定达标</b> —— 目标线按日配置，拿日目标判周/月数据会持续虚警，',
    '　　故多日窗口显示「不判定」并说明原因（而非默默当作达标）',
    '② <b>未配阈值则不渲染徽标</b> —— 不编造目标线；看到"不判定"比看到假目标好',
    '③ <b>本页其余指标本期未接水位</b> —— 实测其阈值与真实量级差 10~30 倍且口径不同源，',
    '　　接上会产生恒告警（比不显示更糟）；见工单 #83',
  ].join('<br/>')

// ========== 图表配置 ==========
// 四路经营退款路线配色 (A 线上直退 / B 钱包取现 / C 闪猫退款 / D 手动退款)
  const REFUND_ROUTES = [
    { key: 'a', label: 'A线上直退', short: 'A直退', color: '#ef4444' },
    { key: 'b', label: 'B钱包取现', short: 'B取现', color: '#f97316' },
    { key: 'c', label: 'C闪猫退款', short: 'C闪猫', color: '#a855f7' },
    { key: 'd', label: 'D手动退款', short: 'D手动', color: '#94a3b8' },
  ]

  function _rrLabel(r) { return isMobile.value ? (r.short || r.label) : r.label }

// ★ #80 投诉三源趋势配色（与投诉专项四卡、饼图共用同一套色，规范 §五 禁止漂移）
  const COMPLAINT_TREND_SOURCES = [
    { key: 'internal', name: '内部投诉', short: '内部', color: '#2563eb' },
    { key: 'merchant', name: '商户号投诉', short: '商户号', color: '#7c3aed' },
    { key: 'external', name: '外部投诉', short: '外部', color: '#dc2626' },
  ]

/**
 * ★ #80 把「按日」的三源投诉趋势对齐到趋势图槽位（用户诉求 3）。
 *
 * 为什么需要对齐（而不是直接用接口顺序）：
 *   退款走 `today/hourly-trend`（槽位可能是小时/日/周/月）；
 *   而投诉三源只有**按日**趋势（`complaints/summary.*.trend`，源于补充表逐日聚合）。
 *   → 只有槽位标签本身就是「MM-DD」时才能一一对齐；否则返回 null，
 *     由调用方退化为单一「投诉量」柱。
 *   ★ 宁可少拆，不可拆错 —— 错配会把 A 天的投诉画到 B 天，比不拆更糟。
 *
 * @returns {null | Object} null=不可拆（非按日粒度）; 否则 {internal:[],merchant:[],external:[]}
 */
  function buildComplaintBySource(labels) {
    const src = complaintSummaryData.value || {}
    const trends = COMPLAINT_TREND_SOURCES.map((s) => src[s.key]?.trend || [])
    if (!trends.some((t) => Array.isArray(t) && t.length)) return null
    if (!labels.length || !labels.every((l) => /^\d{2}-\d{2}$/.test(String(l)))) return null
    const out = {}
    COMPLAINT_TREND_SOURCES.forEach((s, i) => {
      const map = {}
      ;(trends[i] || []).forEach((row) => {
        if (row && row.day) map[String(row.day).slice(5, 10)] = Number(row.count || 0)
      })
      out[s.key] = labels.map((l) => map[l] ?? 0)
    })
    return out
  }

/**
 * 组织趋势图 series。
 * ★ #80 改造（用户诉求 3）：
 *   ① 退款 —— A/B/C/D **四路堆叠柱**（配色与「退款笔数」卡 chips 一致）
 *   ② 投诉 —— **内部/商户号/外部 三源堆叠柱**（按日粒度时）
 * 取舍：
 *   ① **只堆叠笔数，金额改为半透明堆叠柱** —— 原来金额是四路面积线，
 *      与笔数柱叠在同一张图上互相遮挡；且 ABCD 金额混入非现金退出，
 *      相加无对账意义（真正的对账基准是 E，已在「退款金额」卡给出）。
 *   ② 三源堆叠仅在按日粒度可用，周/月聚合自动退化为单一「投诉量」柱，不强行拆。
 *   ③ 不在图例写「合计」字样 —— 投诉三源与「核心指标·投诉数」不同源（见 #81），
 *      写合计会让人误以为两处应相等。
 */
  function buildTrendSeries(data, complaintBySource) {
    const groups = trendSelectedGroups.value
    const series = []
    if (groups.includes('reception')) {
      series.push({ name: isMobile.value ? '会话' : '会话数', type: 'bar', data: data.reception || [], itemStyle: { color: '#3b82f6' }, barMaxWidth: isMobile.value ? 14 : 20 })
      series.push({ name: isMobile.value ? '接待' : '接待数', type: 'bar', data: data.reception_devices || [], itemStyle: { color: '#10b981' }, barMaxWidth: isMobile.value ? 14 : 20 })
    }
    if (groups.includes('complaint')) {
      if (complaintBySource) {
        COMPLAINT_TREND_SOURCES.forEach((s) => {
          series.push({
            name: isMobile.value ? s.short : s.name,
            type: 'bar',
            stack: 'complaint_src',
            data: complaintBySource[s.key] || [],
            itemStyle: { color: s.color },
            barMaxWidth: isMobile.value ? 14 : 20,
          })
        })
      } else {
        series.push({ name: isMobile.value ? '登记' : '投诉登记', type: 'bar', data: data.complaint || [], itemStyle: { color: '#f59e0b' }, barMaxWidth: isMobile.value ? 14 : 20 })
      }
      // ★ #85: **处理**单列一条线（柱=登记三源堆叠，线=处理总量）
      //   口径: 后端 `complaint_done`（finish 落桶 + 真实完成，含往期单办结，不含卡商）
      //   与四卡副行「处理」**同源**。
      //   取舍: 处理**不按三源拆**（拆了线太多、且处理人/渠道归属另有专项页承接），
      //         只给"该时段清掉多少"这一个总量信号 —— 与主管的动作直接对应。
      //   ⚠️ 处理线**不能用 #10b981** —— 那是「接待数」柱的颜色，
      //     同色会让同屏两根“绿色”无法区分（实测像素采样发现）→ 用 teal-700。
      series.push({
        name: isMobile.value ? '处理' : '投诉处理',
        type: 'line', data: data.complaint_done || [],
        smooth: true, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: '#0f766e' }, lineStyle: { width: 2, color: '#0f766e' },
      })
    }
    if (groups.includes('refund')) {
      REFUND_ROUTES.forEach(r => {
        series.push({ name: _rrLabel(r), type: 'bar', stack: 'refund_cnt', data: data[`refund_count_${r.key}`] || [], itemStyle: { color: r.color }, barMaxWidth: isMobile.value ? 14 : 20 })
      })
      REFUND_ROUTES.forEach(r => {
        series.push({ name: `${_rrLabel(r)}·金额`, type: 'bar', stack: 'refund_amt', yAxisIndex: 1, data: data[`refund_amount_${r.key}`] || [], itemStyle: { color: r.color, opacity: 0.35 }, barMaxWidth: isMobile.value ? 10 : 14 })
      })
    }
    return series
  }

  function buildTrendLegend(complaintBySource) {
    const groups = trendSelectedGroups.value
    const items = []
    if (groups.includes('reception')) {
      items.push(isMobile.value ? '会话' : '会话数')
      items.push(isMobile.value ? '接待' : '接待数')
    }
    if (groups.includes('complaint')) {
      if (complaintBySource) {
        COMPLAINT_TREND_SOURCES.forEach((s) => items.push(isMobile.value ? s.short : s.name))
      } else {
        items.push(isMobile.value ? '登记' : '投诉登记')
      }
      // ★ #85: 处理线图例（与柱分开，避免误读为"第四种来源"）
      items.push(isMobile.value ? '处理' : '投诉处理')
    }
    if (groups.includes('refund')) {
      REFUND_ROUTES.forEach(r => items.push(_rrLabel(r)))
    }
    return items
  }

  const hourlyOption = computed(() => {
    const data = hourlyData.value || {}
    const labels = data?.slot_labels || data?.hour_labels || data?.hours || []
    const mob = isMobile.value
    const showRefund = trendSelectedGroups.value.includes('refund')
    // ★ #80: 投诉三源堆叠（仅按日粒度可用，否则为 null → 退化单一柱）
    const complaintBySource = trendSelectedGroups.value.includes('complaint')
      ? buildComplaintBySource(labels)
      : null
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: {
        data: buildTrendLegend(complaintBySource),
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
        : { left: 50, right: showRefund ? 60 : 20, bottom: 30, top: 40 },
      xAxis: [{
        type: 'category',
        data: labels,
        axisPointer: { type: 'shadow' },
        axisLabel: { fontSize: mob ? 9 : 11, interval: 0, rotate: mob ? 30 : 0 },
      }],
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
          axisLine: { show: !mob, lineStyle: { color: '#ea580c' } },
          axisLabel: { fontSize: mob ? 9 : 11 },
          splitLine: { show: false },
        },
      ],
      series: buildTrendSeries(data, complaintBySource),
    }
  })

  const receptionPieOption = computed(() => {
    const data = receptionData.value
    const items = receptionPerspective.value === 'count'
      ? (data?.categories || [])
      : (data?.categories_device || [])
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { orient: 'vertical', right: 10, top: 'center' },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        label: { show: false },
        data: items.map(d => ({ name: d.name, value: d.value || 0 })),
      }],
    }
  })

  const refundReasonOption = computed(() => {
    const data = refundData.value?.reasons || []
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 120, right: 60, bottom: 30, top: 10 },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: data.map(d => d.name).reverse() },
      series: [
        { name: '订单数', type: 'bar', data: data.map(d => d.count).reverse(), itemStyle: { color: '#3b82f6' }, barMaxWidth: 16 },
        { name: '金额(元)', type: 'bar', data: data.map(d => d.amount).reverse(), itemStyle: { color: '#ea580c' }, barMaxWidth: 16 },
      ],
    }
  })

// ========== 人均效率表 ==========
  const departmentOptions = computed(() => {
    const depts = new Set()
    ;(staffData.value?.data || []).forEach(r => {
      if (r.department && r.department !== '未分配') depts.add(r.department)
    })
    return Array.from(depts).sort()
  })

// 判定"企微人员":部门=企微 / 姓名前缀"企微-" / qw_user_id 已绑定
  function isQwStaff(s) {
    if (!s) return false
    if ((s.qw_user_id || '').trim()) return true
    if ((s.department || s.dept || '') === '企微') return true
    const nm = (s.name || s.op || s.display_name || '').trim()
    return /^企微[-_·\s]?/.test(nm)
  }

  const staffRows = computed(() => {
    let rows = staffData.value?.data || []
    // ★ #78: AI/系统账户不进真人表（它们在下方单独一张小表展示）
    rows = rows.filter(r => !r.is_virtual)
    // 按人视图下默认仅显示企微人员(宽松判定), 勾选后显示所有人
    if (staffFilter.aggregateBy === 'person' && !showAllStaff.value) {
      rows = rows.filter(isQwStaff)
    }
    if (filterDepartment.value) {
      rows = rows.filter(r => r.department === filterDepartment.value)
    }
    if (filterGroupId.value) {
      rows = rows.filter(r => r.group === filterGroupId.value)
    }
    return rows
  })

// ★ #78 AI / 系统账户单列（用户要求：「区分出来，单列也可以」）
//   设计说明: 此前 `is_virtual` 账户被各页直接过滤丢弃，AI 工作量完全不可见。
//   这里单列展示 —— 既能看到 AI 规模，也不挤占真人排名。
//   取舍: 不给名次（AI 不是人，排名无意义）；空则不渲染整块。
  const virtualStaffList = computed(() => {
    const rows = staffData.value?.data || []
    return rows
      .filter(r => r.is_virtual)
      .filter(r => (Number(r.reception_count) || 0) + (Number(r.reception_device_count) || 0) > 0)
      .sort((a, b) => (Number(b.reception_count) || 0) - (Number(a.reception_count) || 0))
  })

/** 该 AI/系统账户名下会话数占全站会话数的百分比（可控指标：AI 接管率） */
  function aiSharePct(row) {
    const total = Number(kpiData.value?.reception?.value || 0)
    if (!total) return '0.0'
    return ((Number(row.reception_count || 0) * 100) / total).toFixed(1)
  }

// 部门列显示策略:移动端 + 按人视图 + 仅企微时隐藏(表头已筛选); 其余保留
  const showDeptColumn = computed(
    () => !(isMobile.value && staffFilter.aggregateBy === 'person' && !showAllStaff.value)
  )

// ========== 退款挽回 ==========
// ★ #80（用户诉求 4）：原样返回 staff_details 所有人 → 「为 0 的不显示」。
//   ⚠️ 关键区分（不能一刀切"为0就隐藏"）：
//     · total_count == 0        → 本期**确实没接到挽回任务**，属噪声 → 隐藏
//     · total_count > 0 但成功 = 0 → **接了活但全失败** → 必须保留！
//       这正是主管最该看见的一类；滤掉就是掩盖问题。
//   排序：全失败者前置（用 allFail 作为一级排序键），让问题浮到顶上。
  const recoveryStaffList = computed(() => {
    const details = recoveryData.value?.staff_details || recoveryData.value?.staff_detail || []
    return details
      .map(d => ({
        name: d.name,
        group: d.group,
        total_count: Number(d.total_sn || 0),
        success_count: Number(d.success_sn || 0),
        failed_count: Number(d.failed_sn || 0),
        success_rate: d.total_sn > 0 ? d.success_sn / d.total_sn : 0,
      }))
      // 隐藏「本期无挽回任务」（total=0）—— 这些是噪声，不是问题
      .filter((d) => d.total_count > 0)
      // 全失败（有任务、成功=0）置顶：主管一进来就看到谁需要帮扶
      .sort((a, b) => {
        const af = a.success_count === 0 ? 1 : 0
        const bf = b.success_count === 0 ? 1 : 0
        if (af !== bf) return bf - af
        return b.total_count - a.total_count
      })
  })

/** ★ #80: 本期无挽回任务（total=0）而被隐藏的人数 —— 必须告知，否则使用者
 *  会以为"人少了"是系统漏数据。规范 §二「空必须可解释」。 */
  const recoveryZeroHidden = computed(() => {
    const details = recoveryData.value?.staff_details || recoveryData.value?.staff_detail || []
    return details.filter((d) => Number(d.total_sn || 0) <= 0).length
  })

/** ★ #80: 全失败人数（有任务但成功 0）—— 标红提示 */
  const recoveryAllFailCount = computed(
    () => recoveryStaffList.value.filter((d) => d.success_count === 0).length,
  )

/** ★ #80: 成功占比（堆叠条用）。total=0 时给 0，避免除零。 */
  const recoverySuccessPct = computed(() => {
    const k = recoveryData.value?.kpi || {}
    const total = Number(k.total_sn || 0)
    if (!total) return 0
    return Math.round((Number(k.success_sn || 0) * 100) / total)
  })

/** ★ #80: 堆叠条悬浮说明（各段量级，规范 §二：视觉元素须说清含义） */
  const recoveryStackTip = computed(() => {
    const k = recoveryData.value?.kpi || {}
    return `挽回成功 ${formatNum(k.success_sn)} 个 (${recoverySuccessPct.value}%)\n`
      + `挽回失败 ${formatNum(k.failed_sn)} 个 (${100 - recoverySuccessPct.value}%)\n`
      + `合计 ${formatNum(k.total_sn)} 个SN（按 SN 计，T+7 窗口）`
  })

/** ★ #80: 成功率阈值配色（达标≥38% / 预警30-38% / 告警<30%）—— 与今日看板同阈值 */
  const recoveryRate = computed(() => Number(recoveryData.value?.kpi?.success_rate || 0))

  const rateColor = computed(() => (recoveryRate.value >= 38 ? '#16a34a' : recoveryRate.value >= 30 ? '#ca8a04' : '#dc2626'))

  const rateBg = computed(() => (recoveryRate.value >= 38 ? '#f0fdf4' : recoveryRate.value >= 30 ? '#fefce8' : '#fef2f2'))

// ========== 投诉专项 ==========
  const complaintSourcePie = computed(() => {
    const s = complaintSummaryData.value || {}
    // ★★ 2026-09-11 (#53): (外)卡商 **移出饼图** ★★
    //   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
    //   卡商改为独立区块展示(见模板 card 区块)。
    const data = [
      { name: '内部投诉', value: Number(s.internal?.total || 0), itemStyle: { color: '#2563eb' } },
      { name: '商户号投诉', value: Number(s.merchant?.total || 0), itemStyle: { color: '#7c3aed' } },
      { name: '外部投诉', value: Number(s.external?.total || 0), itemStyle: { color: '#dc2626' } },
    ]
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
      series: [{
        type: 'pie',
        radius: ['40%', '65%'],
        center: ['50%', '45%'],
        label: { show: false },
        labelLine: { show: false },
        data,
      }],
    }
  })

  const complaintHandlerOption = computed(() => {
    const s = complaintSummaryData.value || {}
    const map = {}
    ;[
      ...(s.internal?.by_handler || []),
      ...(s.merchant?.by_handler || []),
      ...(s.external?.by_handler || []),
    ].forEach(h => {
      const key = h.name || '未标注'
      map[key] = (map[key] || 0) + Number(h.count || 0)
    })
    const rows = Object.entries(map)
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10)
      .reverse()
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 100, right: 40, bottom: 30, top: 10 },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: rows.map(d => d.name) },
      series: [{ name: '投诉数', type: 'bar', data: rows.map(d => d.count), itemStyle: { color: '#7c3aed' }, barMaxWidth: 16 }],
    }
  })

  function complaintSourceType(source) {
    if (source === '内部投诉') return 'warning'
    if (source === '外部投诉') return 'danger'
    if (source === '商户号投诉') return 'primary'
    return 'info'
  }

// 被投诉主体 Top5 (商户号+外部 堆叠, 仅统计有主体的来源)
  const complaintSubjectTopOption = computed(() => {
    const rows = (complaintSubjectData.value?.subject_ranking || []).slice(0, 5)
    if (!rows.length) return null
    const items = [...rows].reverse()
    return {
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => {
          const p = ps[0]
          const row = rows.find(x => x.name === p?.name)
          if (!row) return ''
          return `${row.name}<br/>商户号: ${row.merchant_count}<br/>外部: ${row.external_count}<br/>关联退款: ${row.refund_count}笔 ¥${Number(row.refund_amount || 0).toLocaleString('zh-CN')}`
        }
      },
      legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
      grid: { left: 10, right: 10, bottom: 8, top: 28, containLabel: true },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
      series: [
        { name: '商户号投诉', type: 'bar', stack: 's', data: items.map(x => x.merchant_count), itemStyle: { color: '#7c3aed' }, barMaxWidth: 16 },
        { name: '外部投诉', type: 'bar', stack: 's', data: items.map(x => x.external_count), itemStyle: { color: '#dc2626' }, barMaxWidth: 16 },
      ],
    }
  })

  function formatShortTime(t) {
    if (t === null || t === undefined || t === '' || t === '-') return '-'
    const d = dayjs(t)
    if (!d.isValid()) return '-'
    return d.format('MM-DD HH:mm')
  }

  function formatNum(v) {
    if (v == null || v === '') return '0'
    const n = Number(v)
    if (Number.isNaN(n)) return '0'
    return n.toLocaleString('zh-CN')
  }

  function scoreTagType(score, a, b, c) {
    if (score >= a) return 'success'
    if (score >= b) return 'warning'
    if (score >= c) return 'danger'
    return 'info'
  }

// ========== 数据加载 ==========
  async function loadAll() {
    loading.value = true
    const { start, end } = getDateRange()
    const params = { start, end, trend_grain: trendGrain.value }
  
    try {
      const [kpiRes, hourlyRes, receptionRes, refundRes, staffRes, recoveryRes, complaintSummaryRes, complaintDetailRes, subjectRes, healthRes] = await Promise.all([
        today.kpi(params),
        today.hourlyTrend(params),
        today.reception(params),
        today.refund(params),
        today.staff({ ...params, aggregate_by: staffFilter.aggregateBy, topn: 200 }),
        today.refundRecovery(params),
        complaintSummary({ start, end }),
        complaintDetail({ start, end, limit: 500, include_supplementary: true }),
        complaintSubjectAnalysis({ start, end }),
        // ★ #87: 失败不阻断整页（新指标，后端老版本/异常时降级为不渲染）
        complaintHealthMetrics({ start, end }).catch(() => ({ data: null })),
      ])
  
      if (kpiRes.data) kpiData.value = kpiRes.data
      if (hourlyRes.data) hourlyData.value = hourlyRes.data
      if (receptionRes.data) receptionData.value = receptionRes.data
      if (refundRes.data) refundData.value = refundRes.data
      if (staffRes.data) staffData.value = staffRes.data
      if (recoveryRes.data) recoveryData.value = recoveryRes.data
      if (complaintSummaryRes.data) complaintSummaryData.value = complaintSummaryRes.data
      if (complaintDetailRes.data) complaintList.value = complaintDetailRes.data?.items || []
      if (subjectRes.data) complaintSubjectData.value = subjectRes.data
      // ★ #87: 仅在拿到有效数据时赋值（避免接口异常时渲染空壳）
      if (healthRes?.data) healthMetrics.value = healthRes.data
    } catch (e) {
      console.error('加载数据失败', e)
    } finally {
      loading.value = false
    }
  }

  async function loadGroups() {
    try {
      const res = await listGroups()
      groupsList.value = res.data || []
    } catch {}
  }

  function _onMediaChange(e) {
    isMobile.value = e.matches
  }

  onMounted(async () => {
    _mq = window.matchMedia('(max-width: 768px)')
    isMobile.value = _mq.matches
    _mq.addEventListener('change', _onMediaChange)
    await Promise.all([loadAll(), loadGroups()])
  })

  onBeforeUnmount(() => {
    if (_mq) {
      _mq.removeEventListener('change', _onMediaChange)
      _mq = null
    }
  })

  watch([grain, weekOffset, monthOffset, quarterOffset, yearOffset, customDateRange, trendGrain, filterDepartment, filterGroupId, staffFilter.aggregateBy], () => {
    loadAll()
  })

  return {
    loading,
    isMobile,
    _mq,
    grain,
    weekOffset,
    monthOffset,
    quarterOffset,
    yearOffset,
    customDateRange,
    trendGrain,
    quickGrainOpt,
    grainQuickOptions,
    filterDepartment,
    filterGroupId,
    groupsList,
    staffFilter,
    showAllStaff,
    openPersonAudit,
    openDeviceAudit,
    receptionPerspective,
    trendSelectedGroups,
    SPECIALTY_COLS,
    POSITION_META,
    positionMeta,
    getSpecialtyPeople,
    kpiData,
    hourlyData,
    receptionData,
    refundData,
    staffData,
    recoveryData,
    hasRecovery,
    complaintSummaryData,
    complaintList,
    proxy,
    complaintSubjectData,
    healthMetrics,
    supplExternal,
    supplCard,
    supplSummaryText,
    complaintThreeTotal,
    complaintTotalHint,
    complaintScopeTip,
    TREND_TIP,
    COMPLAINT_SOURCE_CHIPS,
    complaintChips,
    complaintTip,
    backlogAging,
    backlogAgingTotal,
    overdueCount,
    BACKLOG_AGING_SEGMENTS,
    healthTip,
    healthPositionTip,
    fmtRate,
    drillToComplaintAnalysis,
    drillToRepeatComplaint,
    healthOtherDuty,
    healthOtherDutyPct,
    backlogAgingTip,
    complaintSourceFilter,
    complaintCategoryFilter,
    complaintStatusFilter,
    COMPLAINT_DETAIL_LIMIT,
    complaintTruncated,
    toggleComplaintSourceFilter,
    clearComplaintFilters,
    sortTimeStr,
    complaintSourceFilters,
    complaintCategoryFilters,
    complaintStatusFilters,
    filteredComplaintList,
    weekOptions,
    monthOptions,
    quarterOptions,
    yearOptions,
    grainLabel,
    grainTagType,
    periodName,
    compareName,
    displayDate,
    rhythmSub,
    getDateRange,
    _pctDelta,
    kpiCards,
    metricLevelByCode,
    recoveryMetricLevel,
    recoveryMetricTip,
    REFUND_ROUTES,
    _rrLabel,
    COMPLAINT_TREND_SOURCES,
    buildComplaintBySource,
    buildTrendSeries,
    buildTrendLegend,
    hourlyOption,
    receptionPieOption,
    refundReasonOption,
    departmentOptions,
    isQwStaff,
    staffRows,
    virtualStaffList,
    aiSharePct,
    showDeptColumn,
    recoveryStaffList,
    recoveryZeroHidden,
    recoveryAllFailCount,
    recoverySuccessPct,
    recoveryStackTip,
    recoveryRate,
    rateColor,
    rateBg,
    complaintSourcePie,
    complaintHandlerOption,
    complaintSourceType,
    complaintSubjectTopOption,
    formatShortTime,
    formatNum,
    scoreTagType,
    loadAll,
    loadGroups,
    _onMediaChange,
  }
}
