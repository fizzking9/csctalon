<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：客服主管 / 渠道负责人**逐个渠道**定位"问题出在哪个来源、哪个分类、哪位处理人"。   │
  │       与「决策概览」的分工：概览回答"要不要管"(整体)，本组件回答"管哪里"(定位)。       │
  │ 逻辑：① 四个渠道(内部/商户号/外部/卡商)用**同一套结构**渲染 —— 改造前是              │
  │         "外部/商户号各写一套、内部与卡商没有"，导致横向比较时找不到同一个位置；         │
  │       ② KPI→结构分布→趋势→明细，固定四段，符合"先看规模、再看结构、最后看人"的排查路径；│
  │       ③ 卡商渠道带「独立统计」标记 —— 卡商不纳入投诉总量(#53)，必须显式声明，         │
  │         否则使用者会以为总量漏了。                                                    │
  │ 口径：category/handler/trend 均来自 `/complaints/summary` 的对应分源对象；             │
  │       明细来自 `/complaints/detail?source_type=`。详见                                   │
  │       docs/standards/投诉渠道口径与对账说明.md §一                                     │
  │ 取舍：❌ 不做"四渠道叠加对比图" —— 那是概览的职责(总量堆积趋势)，此处避免重复展示；     │
  │       ❌ 卡商不做处理人排行 —— 卡商 result 是混合语义字段，无可靠处理人字段；          │
  │       ❌ 不做导出(放在执行跟踪页，避免每页都有导出按钮导致心智负担)。                   │
  │ 工单：#67 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §二.3         │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="ccp">
    <!-- ⓘ 页面层设计说明(规范要求: 使用者必须能看到"为什么这样设计") -->
    <div class="ccp-head">
      <div>
        <span class="ccp-title">{{ meta.title }}专项</span>
        <el-tag v-if="meta.independent" size="small" type="success" effect="plain" style="margin-left:8px">
          独立统计 · 不纳入投诉总量
        </el-tag>
        <span class="ccp-hint">{{ meta.hint }}</span>
      </div>
      <el-popover placement="bottom-end" :width="400" trigger="click">
        <template #reference>
          <el-icon class="ccp-info"><QuestionFilled /></el-icon>
        </template>
        <div class="dr-body">
          <div class="dr-title">{{ meta.title }}渠道 · 设计说明</div>
          <div class="dr-sec"><b>给谁用</b><div>渠道负责人 / 客服主管，用于定位该渠道的问题结构与处理人。</div></div>
          <div class="dr-sec"><b>为什么和概览分两处</b><div>概览回答"要不要管"（整体水位与结论），本页回答"管哪里"（分类 / 处理人 / 明细）。</div></div>
          <div v-if="meta.independent" class="dr-sec"><b>为什么标"独立统计"</b><div>(外)卡商为独立统计体系，<b>不计入投诉总量</b>（用户 2026-09-11 决策）。</div></div>
          <div v-else class="dr-sec"><b>本页不含什么</b><div>不展示其它渠道的对比（概览已做），避免同一指标两处各画一遍。</div></div>
          <div class="dr-sec"><b>数据口径</b><div>{{ meta.scope }}，详见 docs/standards/投诉渠道口径与对账说明.md</div></div>
        </div>
      </el-popover>
    </div>

    <!-- ① KPI 四卡 -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" :style="{ background: meta.bg }">
          <div class="kpi-label" :style="{ color: meta.color }">{{ meta.title }}总量</div>
          <div class="kpi-value" :style="{ color: meta.color }">{{ formatNum(srcData.total) }}</div>
          <div class="kpi-sub">{{ meta.totalHint }}</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#eff6ff">
          <div class="kpi-label" style="color:#2563eb">新登记</div>
          <div class="kpi-value">{{ formatNum(srcData.registered) }}</div>
          <div class="kpi-sub">窗口内新登记</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#f0fdf4">
          <div class="kpi-label" style="color:#16a34a">已处理</div>
          <div class="kpi-value" style="color:#16a34a">{{ formatNum(srcData.handled) }}</div>
          <div class="kpi-sub">窗口内完成（真实完成时间）</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" :style="{ background: completionBg }">
          <div class="kpi-label" :style="{ color: completionColor }">完成率</div>
          <div class="kpi-value" :style="{ color: completionColor }">
            {{ completionRate != null ? completionRate : '—' }}<span v-if="completionRate != null" class="unit">%</span>
          </div>
          <div class="kpi-sub">对称口径：窗口内登记中已完成占比</div>
        </div>
      </el-col>
    </el-row>

    <!-- ② 结构分布 + 趋势 + ③ 处理人
         ⚠️ 卡商无可靠处理人字段(result 是混合语义)，故不画处理人图 —— 画了就是误导。 -->
    <el-row :gutter="16" class="mb-16">
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="chart-title">{{ meta.catLabel }}</div>
          <BaseChart v-if="catOption" :option="catOption" height="260px" />
          <el-empty v-else description="暂无数据" :image-size="36" />
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="chart-title">趋势<span class="dim-text"> · 粒度由顶部时间条统一控制</span></div>
          <BaseChart v-if="trendOption" :option="trendOption" height="260px" />
          <el-empty v-else description="暂无趋势数据" :image-size="36" />
        </div>
      </el-col>
      <el-col v-if="!meta.independent" :xs="24" :md="8">
        <div class="chart-card">
          <div class="chart-title">处理人 Top10（客服）</div>
          <BaseChart v-if="handlerOption" :option="handlerOption" height="260px" />
          <el-empty v-else description="暂无处理人数据" :image-size="36" />
        </div>
      </el-col>
      <!-- 卡商: 用「去重与事件」信息替代处理人位，避免该列留空 -->
      <el-col v-else :xs="24" :md="8">
        <div class="chart-card">
          <div class="chart-title">卡商渠道说明</div>
          <div class="ccp-cardnote">
            <div>· <b>不画处理人排行</b>：卡商结果字段是混合语义（含自由文本诉求），无可靠处理人。</div>
            <div>· <b>独立统计</b>：不计入投诉总量，请与其它三源分开看。</div>
            <div>· 事件归并（多渠道/真重复）见「系统管理 → 数据治理」。</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- ④ 明细表 -->
    <el-card shadow="never">
      <template #header>
        <div class="flex-between">
          <span style="font-weight:600">
            {{ meta.title }}明细
            <el-tag size="small" effect="plain" :style="{ marginLeft: '6px', color: meta.color }">
              登记 {{ formatNum(srcData.registered) }}
            </el-tag>
            <el-tag size="small" effect="plain" style="color:#16a34a">处理 {{ formatNum(srcData.handled) }}</el-tag>
            <span class="dim" style="margin-left:6px;font-weight:400">共 {{ rows.length }} 条</span>
          </span>
          <el-button size="small" :icon="Refresh" @click="$emit('refresh')">刷新</el-button>
        </div>
      </template>
      <el-table v-loading="loading" :data="rows" border stripe size="small" max-height="420">
        <el-table-column label="时间" width="118" sortable :sort-method="(a, b) => sortTime(a.time, b.time)">
          <template #default="{ row }">{{ formatTime(row.time) }}</template>
        </el-table-column>
        <el-table-column label="分类" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.category || '—' }}</template>
        </el-table-column>
        <el-table-column v-if="showEntity" prop="entity" label="被投诉主体" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.entity" size="small" effect="plain" type="warning">{{ row.entity }}</el-tag>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="problem" label="投诉内容" min-width="220" show-overflow-tooltip />
        <el-table-column prop="device_sn" label="设备SN" width="130">
          <template #default="{ row }">
            <el-link v-if="row.device_sn" type="primary" underline="never" @click="$emit('device', row.device_sn)">{{ row.device_sn }}</el-link>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="!meta.independent" label="处理人" width="100">
          <template #default="{ row }">
            <el-link v-if="row.handler && row.handler !== '未分配'" type="primary" underline="never" @click="$emit('person', row.handler)">{{ row.handler }}</el-link>
            <span v-else class="dim">未分配</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status || '—' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !rows.length" description="窗口内无该渠道投诉明细" :image-size="50" />
    </el-card>
  </div>
</template>

<script setup>
/**
 * 渠道同构面板（#67 P2）。
 *
 * ★ 为什么是"一个组件渲染四次"而不是四份页面：
 *   改造前 外部 / 商户号 各写一套模板，内部与卡商缺失 —— 三个后果：
 *     ① 同一指标在两页的口径与配色不一致，横向比较要"心算"；
 *     ② 新增一个渠道要复制一整页；
 *     ③ 修一个 bug 要改四处，漏改即不一致。
 *   → 统一为 prop 驱动：`source` 决定取哪个分源数据，模板与口径只有一份。
 *
 * 纯展示组件：不发请求、不持有区间状态，数据与时间范围由父级注入。
 */
import { computed } from 'vue'
import { Refresh, QuestionFilled } from '@element-plus/icons-vue'
import dayjs from 'dayjs'

const props = defineProps({
  /** 渠道: internal | merchant | external | card */
  source: { type: String, required: true },
  /** 该渠道数据对象(`/summary` 的对应分源) */
  srcData: { type: Object, default: () => ({}) },
  /** 明细行 */
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  /** 趋势粒度(由统一时间条控制) */
  grain: { type: String, default: 'day' },
})
defineEmits(['refresh', 'device', 'person'])

/** 渠道元信息：标题/配色/口径提示集中一处，避免散落在模板里 */
const SOURCE_META = {
  internal: {
    title: '内部投诉',
    color: '#2563eb',
    bg: '#eff6ff',
    hint: '商品投诉 / 服务投诉 · 工单直接登记，未走外部渠道',
    catLabel: '投诉分类分布',
    totalHint: '计入投诉总量',
    noHandlerHint: '暂无处理人数据',
    scope: '工单 service_item ∈ {商品投诉, 服务投诉}',
  },
  merchant: {
    title: '商户号投诉',
    color: '#ca8a04',
    bg: '#fefce8',
    hint: '微信投诉 · 被投诉主体=商户主体，处理人=客服',
    catLabel: '投诉分类分布',
    totalHint: '计入投诉总量',
    noHandlerHint: '暂无处理人数据',
    scope: '工单 service_item = 微信投诉',
  },
  external: {
    title: '外部投诉',
    color: '#dc2626',
    bg: '#fef2f2',
    hint: '315 / 12315 / 监所市监局等平台 · 含监所误录软纠正(#58)',
    catLabel: '投诉问题分类',
    totalHint: '按投诉日期 · 含补充表去重后净额',
    noHandlerHint: '暂无处理人数据',
    scope: '工单外部平台词 + 315 补充表（已按 SN 关联去重）',
  },
  card: {
    title: '(外)卡商',
    color: '#059669',
    bg: '#ecfdf5',
    hint: '卡商渠道 · 独立统计体系',
    catLabel: '卡商投诉分类',
    totalHint: '独立统计 · 不纳入投诉总量',
    noHandlerHint: '卡商无可靠处理人字段（result 为混合语义）',
    independent: true,
    scope: 'card_complaints 补充表 ∪ 工单侧「工信部」真卡商（已去重）',
  },
}

const meta = computed(() => SOURCE_META[props.source] || SOURCE_META.internal)

/** 被投诉主体仅商户号/外部有意义（内部无主体字段，卡商是群名） */
const showEntity = computed(() => props.source === 'merchant' || props.source === 'external')

const completionRate = computed(() => {
  const reg = props.srcData?.registered || 0
  const done = props.srcData?.handled || 0
  if (!reg) return null
  return Math.min(Math.round(done / reg * 1000) / 10, 100)
})
const completionColor = computed(() => {
  const r = completionRate.value
  if (r == null) return '#909399'
  if (r >= 90) return '#16a34a'
  if (r >= 70) return '#ea580c'
  return '#dc2626'
})
const completionBg = computed(() => {
  const r = completionRate.value
  if (r == null) return '#f9fafb'
  if (r >= 90) return '#f0fdf4'
  if (r >= 70) return '#fff7ed'
  return '#fef2f2'
})

const catOption = computed(() => {
  const d = props.srcData?.by_category || []
  if (!d.length) return null
  const items = [...d].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 96, right: 40, bottom: 20, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar', barMaxWidth: 16,
      data: items.map(x => x.count),
      itemStyle: { color: meta.value.color, borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', fontSize: 11 },
    }],
  }
})

const handlerOption = computed(() => {
  const d = props.srcData?.by_handler || []
  if (!d.length) return null
  const items = [...d].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 90, right: 40, bottom: 20, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar', barMaxWidth: 16,
      data: items.map(x => x.count),
      itemStyle: { color: '#7c3aed', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', fontSize: 11 },
    }],
  }
})

/** 趋势聚合：把按日序列按 grain 归并为 日/周/月（与概览同算法，避免两处口径漂移） */
const trendOption = computed(() => {
  const t = props.srcData?.trend || []
  if (!t.length) return null
  const dayMap = {}
  for (const r of t) {
    if (r.day != null) dayMap[r.day] = (dayMap[r.day] || 0) + (r.count || 0)
  }
  const agg = aggregate(dayMap, props.grain)
  if (!agg.labels.length) return null
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 44, right: 20, bottom: 40, top: 12 },
    xAxis: {
      type: 'category', data: agg.labels,
      axisLabel: { fontSize: 10, rotate: agg.labels.length > 12 ? 45 : 0 },
    },
    yAxis: { type: 'value', name: '条' },
    series: [{
      type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
      data: agg.values,
      itemStyle: { color: meta.value.color },
      areaStyle: { opacity: 0.15 },
    }],
  }
})

/** 与 ComplaintTimeBar/后端 `_agg_by_bucket` 同规则：ISO 周(周一起)、自然月 */
function aggregate(dayMap, grain) {
  if (grain === 'day' || !grain) {
    const ks = Object.keys(dayMap).sort()
    return { labels: ks.map(d => d.slice(5)), values: ks.map(k => dayMap[k]) }
  }
  const groups = new Map()
  for (const [d, v] of Object.entries(dayMap)) {
    const dt = dayjs(d)
    if (!dt.isValid()) continue
    let key, label
    if (grain === 'week') {
      const ws = dt.startOf('isoWeek')
      key = ws.format('YYYY-MM-DD')
      label = `${ws.format('MM-DD')}~${ws.add(6, 'day').format('MM-DD')}`
    } else {
      key = dt.format('YYYY-MM'); label = key
    }
    const g = groups.get(key) || { label, value: 0 }
    g.value += v
    groups.set(key, g)
  }
  const sorted = [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
  return { labels: sorted.map(([, g]) => g.label), values: sorted.map(([, g]) => g.value) }
}

function statusType(s) {
  if (s === '已完成') return 'success'
  if (s === '处理中') return 'warning'
  if (s === '待跟进') return 'info'
  return 'danger'
}
function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  return Number.isNaN(n) ? '0' : n.toLocaleString('zh-CN')
}
function formatTime(t) {
  if (!t) return '—'
  const d = dayjs(t)
  return d.isValid() ? d.format('MM-DD HH:mm') : '—'
}
function sortTime(a, b) {
  const va = a || ''; const vb = b || ''
  if (va === vb) return 0
  if (!va) return 1
  if (!vb) return -1
  return va < vb ? -1 : 1
}
</script>

<style scoped>
.ccp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.ccp-title { font-size: 15px; font-weight: 600; color: #1f2937; }
.ccp-hint { font-size: 12px; color: #9ca3af; margin-left: 6px; }
.ccp-info { color: #909399; cursor: help; font-size: 15px; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.dim { color: #909399; }
.dim-text { color: #a0a4ab; font-size: 12px; font-weight: 400; }
.unit { font-size: 14px; font-weight: 400; color: #909399; }
.kpi-card { border-radius: 10px; padding: 12px 14px; min-height: 68px; display: flex; flex-direction: column; gap: 4px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 11px; color: #6b7280; }
.chart-card { background: #fff; border-radius: 8px; padding: 12px; border: 1px solid #ebeef5; }
.chart-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 8px; }
.dr-body { font-size: 12px; line-height: 1.6; color: #4b5563; }
.dr-title { font-weight: 700; font-size: 13px; color: #1f2937; margin-bottom: 8px; }
.dr-sec { margin-bottom: 8px; }
.dr-sec b { color: #374151; }
.ccp-cardnote { font-size: 12px; line-height: 1.9; color: #6b7280; }

@media (max-width: 768px) {
  .kpi-value { font-size: 19px; }
  .ccp-title { font-size: 14px; }
  .ccp-hint { display: block; margin-left: 0; }
}
</style>
