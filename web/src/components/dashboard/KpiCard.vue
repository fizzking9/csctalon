<template>
  <el-card shadow="hover" class="kpi-card" :class="{ 'is-compact': compact }" :body-style="compact ? 'padding: 10px 14px' : 'padding: 14px 18px'">
    <div class="kpi-head">
      <div v-if="icon" class="kpi-icon" :style="{ background: bg || '#ecf5ff', color: color || '#409eff' }">
        <el-icon :size="compact ? 18 : 22"><component :is="icon" /></el-icon>
      </div>
      <div class="kpi-label" :style="{ cursor: tip ? 'help' : 'default' }">
        {{ label }}
        <el-tooltip v-if="tip" effect="dark" placement="top" :content="tip">
          <el-icon style="margin-left:4px; color:#909399; vertical-align:-1px"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
    </div>
    <div class="kpi-value">
      <template v-if="money">{{ fmtMoney(value) }}</template>
      <template v-else>{{ fmtNum(value) }}<span v-if="unit" class="kpi-unit">{{ unit }}</span></template>
    </div>
    <!-- ★ #82 目标达成水位（可控指标前置）
         目的: 替主管回答「这个数**够不够**」—— 环比只说"变了多少"，
               目标线才说"是否可接受"。「涨了5%」与「还差得远」是两件完全不同的事。
         逻辑: 主值下方一条紧凑徽标 + 目标线文字（如「达标 ≥40%」），
               颜色即水位（绿=达标/黄=预警/红=告警），不点也能看到。
         口径: 阈值**从后端 `metric_thresholds` 读取**（`target/warning/critical`），
               不在前端硬编码 —— 防漂移（管理员调阈值，前端自动跟随）。
         取舍: ① 无法判定时**必须显示原因**（如"多日窗口不做达标判定"），
                 不能默默当作达标；② 未配阈值则整条不渲染（**不编造目标线**）。
         工单: #82 -->
    <div v-if="levelMeta" class="kpi-level" :style="{ color: levelMeta.color, borderColor: levelMeta.color + '55', background: levelMeta.bg }">
      <span class="kpi-level-badge">{{ levelMeta.label }}</span>
      <span v-if="targetText" class="kpi-level-target">{{ targetText }}</span>
    </div>
    <div v-else-if="levelReason" class="kpi-level kpi-level-na" :title="levelReason">
      <span class="kpi-level-badge">不判定</span>
      <span class="kpi-level-target">{{ levelReason }}</span>
    </div>
    <!-- 接待量专用追加行: 接待数(去重设备近似独立客户人数) -->
    <div v-if="deviceCount != null" class="kpi-device-sub">
      <span class="kpi-device-sub-label">接待数</span>
      <span class="kpi-device-sub-val">{{ fmtNum(deviceCount) }}<span class="kpi-device-sub-unit"> 个</span></span>
    </div>
    <!-- ★ #80 卡内「括弧」分项 chips —— 离散拆分（如退款笔数 = A+B+C+D）
         设计说明: 原来要把 A/B/C/D 四个笔数塞进主数字后面成为「括弧」，
         但 4 个数字同行会把主值挤变形 → 改为紧随主值的紧凑 chips 行，
         保持主值字号不变，且每个分项有色点对应下方趋势图/堆叠条配色。
         取舍: 不放在 hover tooltip 里 —— 括弧的意义就是「不点也能看到拆分」。 -->
    <div v-if="chips && chips.length" class="kpi-chips">
      <span
        v-for="ch in chips"
        :key="ch.label"
        class="kpi-chip"
        :style="{ color: ch.color, borderColor: ch.color + '55' }"
      >
        <i class="kpi-chip-dot" :style="{ background: ch.color }" />{{ ch.label }} {{ fmtNum(ch.value) }}
      </span>
    </div>
    <!-- ★ #80 卡内副行 —— 长文本拆分（如四路金额、口径对比）
         与 chips 分两行的原因: 金额是「万元」级长数字，与笔数同挤一行会换行错位。 -->
    <div v-for="(r, i) in subRows || []" :key="i" class="kpi-subrow">
      <span class="kpi-subrow-label">{{ r.label }}</span>
      <span class="kpi-subrow-text" :style="r.color ? { color: r.color } : null">{{ r.text }}</span>
    </div>
    <!-- ★ #78 接待来源堆叠比例条 + 图例（仅接待数/会话数两张卡传 stack）
         设计说明: 用一条 100% 堆叠细条替代额外图表 —— 卡片高度几乎不增，
         但一眼可看出「AI 占了多少」。取舍: 不做饼图（KPI 卡空间小，会挤压信息密度）。 -->
    <template v-if="stack && stack.total > 0">
      <div class="kpi-stack" :title="stackTitle">
        <span
          v-for="seg in stack.segs"
          :key="seg.key"
          class="kpi-stack-seg"
          :style="{ width: seg.pct + '%', background: seg.color }"
        />
      </div>
      <div class="kpi-stack-legend">
        <span v-for="seg in positiveSegs" :key="seg.key" class="kpi-stack-legend-item">
          <i class="kpi-stack-dot" :style="{ background: seg.color }" />{{ seg.short }}{{ seg.pct }}%
        </span>
      </div>
    </template>
    <div v-if="!hideYoy" class="kpi-yoy">
      <el-tag v-if="yoyPct != null" size="small" effect="plain" :type="yoyTagType(yoyPct)">
        {{ yoyPct > 0 ? '↑' : '↓' }} {{ Math.abs(yoyPct) }}%
      </el-tag>
      <el-tag v-else size="small" type="info" effect="plain">{{ yoyLabel }} 0</el-tag>
      <span class="kpi-yoy-val">
        <template v-if="money">{{ fmtMoney(yoyValue) }}</template>
        <template v-else>{{ fmtNum(yoyValue) }}{{ unit || '' }}</template>
        · {{ yoyLabel }}
      </span>
    </div>
    <slot name="extra" />
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [Number, String], default: 0 },
  unit: { type: String, default: '' },
  money: { type: Boolean, default: false },
  icon: { type: [String, Object], default: null },
  bg: { type: String, default: '#ecf5ff' },
  color: { type: String, default: '#409eff' },
  // 环比百分比数值,正数=上升,负数=下降; 配合 yoyKey 判断颜色方向
  yoyPct: { type: Number, default: null },
  // 对比期绝对值(用于显示)
  yoyValue: { type: [Number, String], default: 0 },
  // 对比期名称 (日=昨日/周=上周/月=上月/季度=上季度)
  yoyLabel: { type: String, default: '昨日' },
  // 当 yoyKey 在 BAD_UP_KEYS 时, 数值越大越差(退款金额/投诉/现金流出): 上升显示红
  yoyKey: { type: String, default: '' },
  // 接待量追加行: 去重设备近似人数
  deviceCount: { type: Number, default: null },
  tip: { type: String, default: null },
  // 隐藏同比/环比行 (用于工作台首页, 没有同比数据的简洁卡片)
  hideYoy: { type: Boolean, default: false },
  // 紧凑模式: 缩小padding/字体/间距,适合核心指标条
  compact: { type: Boolean, default: false },
  // ★ #78 接待来源分层堆叠段（由 utils/receptionSource.buildStack 构造）
  //   为 null / total=0 时不渲染（无数据不占版面）
  stack: { type: Object, default: null },
  // ★ #80 卡内「括弧」分项: [{ label, value, color }]
  //   用于「退款笔数 = A直退+B取现+C闪猫+D手动」这类离散拆分。空则不渲染。
  chips: { type: Array, default: null },
  // ★ #80 卡内副行: [{ label, text, color? }]
  //   用于四路金额、口径对比（如「经营口径 vs 实际现金流」）。空则不渲染。
  subRows: { type: Array, default: null },
  // ★ #82 目标达成水位: { level, target, warning, critical, unit, direction, desc, reason, source }
  //   null 时不渲染（未配阈值 → **不编造目标线**）
  metricLevel: { type: Object, default: null },
})

/** 水位展示元数据（四级 → 中文标签 + 配色）
 *  与「今日值班看板」既有业务约定一致：达标 / 预警 / 告警。 */
const LEVEL_META = {
  excellent: { label: '优秀', color: '#059669', bg: '#ecfdf5' },
  green: { label: '达标', color: '#16a34a', bg: '#f0fdf4' },
  warning: { label: '预警', color: '#ca8a04', bg: '#fefce8' },
  critical: { label: '告警', color: '#dc2626', bg: '#fef2f2' },
}

/** 有明确级别时才渲染徽标（level=null 走下面的「不判定」分支） */
const levelMeta = computed(() => {
  const lv = props.metricLevel?.level
  return lv ? (LEVEL_META[lv] || null) : null
})

/** 目标线文字（如「达标 ≥40%」）—— 让使用者知道徽标是按什么判的。
 *  ⚠️ 数字全部来自后端阈值，不硬编码。 */
const targetText = computed(() => {
  const m = props.metricLevel
  if (!m) return ''
  const unit = m.unit || ''
  const higher = m.direction === 'higher_better'
  // 优先展示"优秀线(目标)"；无优秀线则展示告警线（也能回答"超没超线"）
  if (m.target != null) return `${higher ? '≥' : '≤'}${m.target}${unit}`
  if (m.critical != null) return `${higher ? '≥' : '≤'}${m.critical}${unit}为告警线`
  if (m.warning != null) return `${higher ? '≥' : '≤'}${m.warning}${unit}为预警线`
  return ''
})

/** 不判定的原因（后端给，如"多日窗口不做达标判定"）—— 必须显示，不能默默当达标 */
const levelReason = computed(() => props.metricLevel?.reason || '')

// 只显示有值的段（避免「AI0%」这种无意义图例）
const positiveSegs = computed(() => (props.stack?.segs || []).filter((s) => s.value > 0))
// 悬浮说明: 各来源的量级与占比（规范 §二: 视觉元素须说清含义）
const stackTitle = computed(() => {
  if (!props.stack) return ''
  const unit = props.stack.metric === 'sessions' ? '次' : '个'
  return props.stack.segs
    .map((s) => `${s.label} ${s.value.toLocaleString('zh-CN')}${unit} (${s.pct}%)`)
    .join('\n')
})

const BAD_UP_KEYS = new Set(['refund_amount', 'cash_out', 'complaint', 'refund_count'])

function yoyTagType(pct) {
  if (pct == null) return 'info'
  const good = BAD_UP_KEYS.has(props.yoyKey) ? pct < 0 : pct > 0
  if (pct === 0) return 'info'
  return good ? 'success' : 'danger'
}

function fmtNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}
function fmtMoney(v) {
  if (v == null) return '0.00'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
</script>

<style scoped>
.kpi-card { position: relative; transition: transform 0.15s; }
.kpi-card:hover { transform: translateY(-2px); }
.kpi-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.kpi-icon {
  width: 42px; height: 42px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 10px; flex-shrink: 0;
}
.kpi-label { color: #909399; font-size: 13px; flex: 1; line-height: 1.4; }
.kpi-value { font-size: 24px; font-weight: 600; color: #303133; margin-top: 4px; line-height: 1.2; font-variant-numeric: tabular-nums; }
.kpi-unit { font-size: 13px; font-weight: 400; color: #909399; margin-left: 4px; }
.kpi-yoy { margin-top: 8px; display: flex; align-items: center; gap: 6px; font-size: 12px; color: #909399; }
.kpi-yoy-val { color: #c0c4cc; font-size: 12px; }
.kpi-device-sub { margin-top: 5px; display: flex; align-items: baseline; gap: 6px; }
.kpi-device-sub-label { font-size: 11px; color: #909399; }
.kpi-device-sub-val { font-size: 15px; font-weight: 600; color: #409eff; letter-spacing: -0.3px; }
.kpi-device-sub-unit { font-size: 11px; font-weight: 400; color: #909399; margin-left: 2px; }

/* ★ #82 目标达成水位徽标
   设计说明: 4px 高度不增多少，但把「够不够」这一最重要判断从"要自己算"
   变成"一眼看到"。放在主值下方、环比上方 —— 视觉优先级：值 > 够不够 > 变化。 */
.kpi-level {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 5px;
  padding: 1px 6px;
  border: 1px solid;
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.6;
  max-width: 100%;
}
.kpi-level-badge { font-weight: 600; flex-shrink: 0; }
.kpi-level-target {
  opacity: 0.85;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 不判定态: 灰色中性，避免被误读为"达标/告警" */
.kpi-level-na {
  color: #909399 !important;
  border-color: #dcdfe6 !important;
  background: #fafafa !important;
  cursor: help;
}

/* ★ #80 括弧分项 chips: 紧跟主值的一行小标签（4 路拆分不再挤压主值） */
.kpi-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 5px;
}
.kpi-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
  padding: 0 5px;
  border: 1px solid;
  border-radius: 3px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.kpi-chip-dot {
  width: 5px; height: 5px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
/* ★ #80 副行: 长文本（四路金额 / 口径对比） */
.kpi-subrow {
  display: flex;
  align-items: baseline;
  gap: 5px;
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.5;
}
.kpi-subrow-label {
  color: #909399;
  flex-shrink: 0;
}
.kpi-subrow-text {
  color: #606266;
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}

/* ★ #78 接待来源堆叠比例条 + 图例
   设计说明: 100% 堆叠细条 + 小字图例，一眼看出 AI 占比；抽到组件层让
   「今日值班看板」与「运营分析看板」共用同一套配色（规范 §五 禁止重复展示）。 */
.kpi-stack {
  display: flex;
  height: 4px;
  border-radius: 2px;
  overflow: hidden;
  margin-top: 5px;
  background: #f0f1f3;
}
.kpi-stack-seg { display: block; height: 100%; }
.kpi-stack-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 3px;
  font-size: 10px;
  color: #909399;
  line-height: 1.3;
}
.kpi-stack-legend-item { display: inline-flex; align-items: center; gap: 3px; white-space: nowrap; }
.kpi-stack-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}

/* 紧凑模式 */
.kpi-card.is-compact .kpi-icon {
  width: 32px; height: 32px; border-radius: 6px;
  margin-bottom: 6px;
}
.kpi-card.is-compact .kpi-label { font-size: 12px; }
.kpi-card.is-compact .kpi-value { font-size: 20px; margin-top: 2px; }
.kpi-card.is-compact .kpi-unit { font-size: 12px; }
.kpi-card.is-compact .kpi-yoy { margin-top: 5px; font-size: 11px; }
.kpi-card.is-compact .kpi-yoy-val { font-size: 11px; }
.kpi-card.is-compact .kpi-device-sub { margin-top: 3px; }
.kpi-card.is-compact .kpi-device-sub-val { font-size: 13px; }
</style>
