/**
 * 接待来源分层（#78）——跨页面共用的配色与堆叠段构造
 *
 * 【设计说明（Design Rationale）】
 * - **目的**：替运营主管回答「AI 客服接管了多少接待？真人还剩多少工作量？」
 * - **逻辑**：抽到独立模块，让「今日值班看板」与「运营分析看板」用**同一套配色**。
 *   规范 §五「禁止重复展示」要求同一指标全站唯一「真身」——
 *   配色如果各页自写会漂移，使用者会以为蓝=真人、绿=真人，产生歧义。
 * - **口径**：来源判据见后端 `app/services/system_accounts.py`（唯一来源）。
 *   本模块只负责**把后端返回的 breakdown 转成可渲染的堆叠段**，不做判定。
 * - **取舍**：① 不给 AI 用红色 —— AI 不是错误，只是「非人工」，红会误导为异常；
 *   ② 不做饼图 —— KPI 卡空间小，100% 堆叠细条信息密度更高；
 *   ③ 不做「AI 转人工」细分（平台无稳定标记列，见工单 #78）。
 * - **工单**：#78；架构：`docs/arch/A-20260913-001-AI接待口径分层.md`
 */

/** 四个来源的配色（顺序即堆叠顺序）。与后端 SourceKind 枚举一一对应。 */
export const SOURCE_KINDS = [
  { key: 'human', label: '真人接待', short: '真人', color: '#2563eb' },
  { key: 'ai_auto', label: 'AI全自动', short: 'AI', color: '#f59e0b' },
  { key: 'ai_system', label: '系统内部', short: '系统', color: '#94a3b8' },
  { key: 'customer_self', label: '客户自助', short: '自助', color: '#10b981' },
]

/**
 * 由后端 `reception.breakdown` 构造堆叠段。
 *
 * @param {object|null} breakdown 后端分层结构 `{human:{sessions,devices,events}, ...}`
 * @param {'sessions'|'devices'|'events'} metric 取哪个口径
 * @returns {{total:number, metric:string, segs:Array}|null} 无数据时返回 null（前端不渲染）
 */
export function buildStack(breakdown, metric) {
  if (!breakdown) return null
  const segs = SOURCE_KINDS.map((k) => ({
    ...k,
    value: Number(breakdown[k.key]?.[metric] || 0),
  }))
  const total = segs.reduce((a, s) => a + s.value, 0)
  if (!total) return null
  return {
    total,
    metric,
    // pct 保留 1 位小数；四段之和因四舍五入可能为 99.9/100.1，仅用于显示宽度
    segs: segs.map((s) => ({ ...s, pct: Math.round((s.value * 1000) / total) / 10 })),
  }
}

/** 堆叠条悬浮说明（规范 §二：每个视觉元素都要说清含义） */
export function stackTip(stack) {
  if (!stack) return ''
  const unit = stack.metric === 'sessions' ? '次' : '个'
  return stack.segs
    .map((s) => `${s.label} ${s.value.toLocaleString('zh-CN')}${unit} (${s.pct}%)`)
    .join('\n')
}

/** 来源 key → 配色/名称（用于 AI 单列表的标签） */
export function sourceKindMeta(kind) {
  return SOURCE_KINDS.find((k) => k.key === kind) || SOURCE_KINDS[2]
}

/**
 * 接待来源分层的统一数据说明（页面 ⓘ 用）。
 *
 * 规范 §二 要求：来源 / 口径 / 时点 + **「空」的含义**；
 * 界面设计说明规范 §一 要求含**「取舍」段**。
 * 抽成常量是为了两个页面**文案完全一致** —— 各写一份必然漂移。
 */
export const RECEPTION_BREAKDOWN_TIP = [
  '<b>来源</b>：/analytics/today/kpi（接待 = cs_records）',
  '<b>口径</b>：',
  '· 接待数 = 去重设备 SN（1 台设备多次进线算 1 次）',
  '· 会话数 = 接待记录条数（2026-09-13 起已按业务键去重，见 #77）',
  '· 来源分层按「操作人 + 说明文本」判定：<b>真人</b>（企微/店铺客服，含人工点 AI 助手）',
  '· <b>AI 全自动</b>（ai-acc 平台 WeChat 账户，全程无人工）',
  '· <b>系统内部</b>（无人工痕迹的标记/执行流水）',
  '· <b>客户自助</b>（客户在公众号自行操作）',
  '<b>时点</b>：今日视图 60s 自动刷新；历史窗口缓存 24h',
  '<b>「空」的含义</b>：分层条不显示 = 该来源本期无记录；四项全空 = 本期无接待数据',
  '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
  '① <b>不做</b>「AI 转人工」细分 —— BMS 与智能客服仍在迭代，字段不稳定，现在做会返工',
  '② <b>不删</b> AI 记录，只分层展示 —— 「AI 接管率」本身是可控指标，删了就看不见',
  '③ 真人客服名下若有「系统标记」行，<b>仍计入真人</b> —— 实测过半与真人操作同秒发生，属连带记录；拆出会虚减人工工作量约 18%',
  '④ <b>不与历史直接比较</b> —— 会话数自 2026-09-13 起才按去重口径，前期数字偏大',
].join('<br/>')
