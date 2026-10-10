<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：客服主管在晨会前 5 分钟内切换「本周/本月/自定义」口径，判断投诉是否失控。        │
  │ 逻辑：① 周期(日/周/月) 决定粒度 → 分析视角；快捷(本周/上周…) 决定区间 → 常用拍点。     │
  │       两者正交，合一排比「一堆单选按钮」更快选中；② 快捷用业务语言(本周/上月)           │
  │       而非「近 7/30/90 天」，因为会议是按自然周期开的，不是按天数；                     │
  │       ③ 对比期(环比) 内建在组件里，避免每个图各自实现导致口径漂移(#67 病灶之一:        │
  │       5 个图表各有一个粒度开关 = 5 处漂移点)。                                        │
  │ 口径：周 = ISO 自然周(周一~周日)；月 = 自然月；日 = 北京时间日历日。                    │
  │       起止经 `emit` 交由父级统一请求，不在本组件内做数据加工。                          │
  │       时间字段规范见 docs/standards/数据时间字段使用规范.md                            │
  │ 取舍：❌ 不做「日粒度」作为快捷项 —— 单日样本噪声大，业务决策按周/月看(用户 2026-09-11   │
  │       决策)。「周期=日」仅在自定义区间 ≤31 天时自动生效。                              │
  │       ❌ 不做同比(去年同周) —— 去年投诉登记口径未对齐(五桶分类 2026-09 才定型)，         │
  │       同比会给出不可比的数字。数据口径成熟后再由 #68 体系研究承接。                     │
  │ 工单：#67 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §四           │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="time-bar">
    <!-- ① 周期(粒度) —— 分析视角 -->
    <el-radio-group :model-value="grain" size="small" @update:model-value="$emit('update:grain', $event)">
      <el-radio-button value="auto">自动</el-radio-button>
      <el-radio-button value="day">日</el-radio-button>
      <el-radio-button value="week">周</el-radio-button>
      <el-radio-button value="month">月</el-radio-button>
    </el-radio-group>

    <!-- ② 快捷区间 —— 业务自然周期 -->
    <el-radio-group :model-value="preset" size="small" @update:model-value="onPreset">
      <el-radio-button value="this_week">本周</el-radio-button>
      <el-radio-button value="last_week">上周</el-radio-button>
      <el-radio-button value="this_month">本月</el-radio-button>
      <el-radio-button value="last_month">上月</el-radio-button>
      <el-radio-button value="last7">近7天</el-radio-button>
      <el-radio-button value="last30">近30天</el-radio-button>
      <el-radio-button value="last90">近90天</el-radio-button>
      <el-radio-button value="custom">自定义</el-radio-button>
    </el-radio-group>

    <!-- ③ 自定义区间 -->
    <el-date-picker
      v-if="preset === 'custom'"
      :model-value="customRange"
      type="daterange"
      size="small"
      value-format="YYYY-MM-DD"
      start-placeholder="开始"
      end-placeholder="结束"
      :clearable="false"
      style="width: 240px"
      @update:model-value="onCustomRange"
    />

    <!-- ④ 对比期(环比) -->
    <el-tooltip placement="top" content="对比期为紧邻的等长前一段区间；基数为 0 时不显示环比">
      <el-select :model-value="compare" size="small" style="width: 104px" @update:model-value="onCompare">
        <el-option label="环比" value="prev" />
        <el-option label="不对比" value="none" />
      </el-select>
    </el-tooltip>

    <!-- ⑤ 生效区间回显 + 设计说明(ⓘ) —— 让使用者随时看清"我看到的是哪一段" -->
    <span class="time-bar-range">
      {{ rangeLabel }}
      <span v-if="compareLabel" class="time-bar-prev">· 对比 {{ compareLabel }}</span>
    </span>

    <el-popover placement="bottom-end" :width="380" trigger="click">
      <template #reference>
        <el-icon class="time-bar-info"><QuestionFilled /></el-icon>
      </template>
      <div class="dr-body">
        <div class="dr-title">时间筛选说明</div>
        <div class="dr-sec">
          <b>粒度怎么选</b>
          <div>「自动」＝按跨度推导：≤31 天→日；32~120 天→周；&gt;120 天→月。也可手动指定。</div>
        </div>
        <div class="dr-sec">
          <b>为什么没有「昨日」</b>
          <div>单日样本噪声大，投诉决策按<b>周/月</b>看更稳（阈值报警同理，已按周/月对齐）。</div>
        </div>
        <div class="dr-sec">
          <b>为什么没有同比</b>
          <div>去年投诉分类口径尚未对齐，同比会给出不可比的数字。待指标体系专项（#68）后开放。</div>
        </div>
        <div class="dr-sec">
          <b>周/月怎么算</b>
          <div>周＝ISO 自然周（周一~周日）；月＝自然月；日＝北京时间日历日。</div>
        </div>
      </div>
    </el-popover>
  </div>
</template>

<script setup>
/**
 * 统一时间条（#67 P1）—— 全页唯一的时间控件。
 *
 * ★ 为什么必须"唯一"：改造前该页面有 5 个独立的粒度 ref
 *   (trendStackGrain / externalTrendGrain / merchantEntityTrendGrain /
 *    subjectTrendGrain / topSubjectTrendGrain)，同屏可能显示 3 种粒度，
 *   使用者无法横向比较，且每次新增图表都会再加一个漂移点。
 *
 * 本组件是**受控组件**：不自己持有区间状态，只把选择结果 emit 给父级，
 * 由父级统一请求（保证一屏数据来自同一区间）。
 */
import { computed } from 'vue'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import { QuestionFilled } from '@element-plus/icons-vue'

dayjs.extend(isoWeek)

const props = defineProps({
  preset: { type: String, default: 'last30' },
  grain: { type: String, default: 'auto' },
  compare: { type: String, default: 'prev' },
  customRange: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:preset', 'update:grain', 'update:compare', 'update:customRange', 'update:range'])

/** 快捷区间 → 起止日（区间口径在此集中，不在父级散落） */
function presetToRange(preset) {
  const today = dayjs()
  switch (preset) {
    case 'this_week':
      return [today.startOf('isoWeek').format('YYYY-MM-DD'), today.format('YYYY-MM-DD')]
    case 'last_week': {
      const ws = today.subtract(1, 'week').startOf('isoWeek')
      return [ws.format('YYYY-MM-DD'), ws.add(6, 'day').format('YYYY-MM-DD')]
    }
    case 'this_month':
      return [today.startOf('month').format('YYYY-MM-DD'), today.format('YYYY-MM-DD')]
    case 'last_month': {
      const ms = today.subtract(1, 'month').startOf('month')
      return [ms.format('YYYY-MM-DD'), ms.endOf('month').format('YYYY-MM-DD')]
    }
    case 'last7':
      return [today.subtract(6, 'day').format('YYYY-MM-DD'), today.format('YYYY-MM-DD')]
    case 'last30':
      return [today.subtract(29, 'day').format('YYYY-MM-DD'), today.format('YYYY-MM-DD')]
    case 'last90':
      return [today.subtract(89, 'day').format('YYYY-MM-DD'), today.format('YYYY-MM-DD')]
    default:
      return null  // custom: 由父级提供 customRange
  }
}

function _emitRange(preset, customRange) {
  const r = preset === 'custom' ? customRange : presetToRange(preset)
  if (r?.length === 2) emit('update:range', { start: r[0], end: r[1] })
}

function onPreset(p) {
  emit('update:preset', p)
  _emitRange(p, props.customRange)
}

function onCustomRange(v) {
  emit('update:customRange', v || [])
  if (v?.length === 2) emit('update:range', { start: v[0], end: v[1] })
}

function onCompare(v) {
  emit('update:compare', v)
}

/** 生效区间回显（父级若不生成 range，则由本组件按 preset 推导展示） */
const rangeLabel = computed(() => {
  const r = props.preset === 'custom' ? props.customRange : presetToRange(props.preset)
  if (!r?.length || r.length !== 2) return '—'
  return `${r[0]} ~ ${r[1]}`
})

/** 对比期回显：紧邻的等长前一段（与后端 prev 口径一致） */
const compareLabel = computed(() => {
  if (props.compare !== 'prev') return ''
  const r = props.preset === 'custom' ? props.customRange : presetToRange(props.preset)
  if (!r?.length || r.length !== 2) return ''
  const end = dayjs(r[1])
  const start = dayjs(r[0])
  const span = end.diff(start, 'day')  // 等长区间天数差
  const pe = start.subtract(1, 'day')
  const ps = pe.subtract(span, 'day')
  return `${ps.format('MM-DD')}~${pe.format('MM-DD')}`
})
</script>

<style scoped>
.time-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.time-bar-range {
  font-size: 12px;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}
.time-bar-prev {
  color: #a0a4ab;
}
.time-bar-info {
  color: #909399;
  cursor: help;
  font-size: 15px;
}
.dr-body { font-size: 12px; line-height: 1.6; color: #4b5563; }
.dr-title { font-weight: 700; font-size: 13px; color: #1f2937; margin-bottom: 8px; }
.dr-sec { margin-bottom: 8px; }
.dr-sec b { color: #374151; }

@media (max-width: 768px) {
  .time-bar { gap: 6px; }
  .time-bar-range { font-size: 11px; width: 100%; }
}
</style>
