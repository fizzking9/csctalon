<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">运营分析</div>
      <div class="filter-bar">
        <el-radio-group v-model="range" size="small" @change="loadAll">
          <el-radio-button value="7">近7天</el-radio-button>
          <el-radio-button value="30">近30天</el-radio-button>
          <el-radio-button value="90">近90天</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="grain" size="small" @change="loadAll">
          <el-radio-button value="day">按日</el-radio-button>
          <el-radio-button value="week">按周</el-radio-button>
          <el-radio-button value="month">按月</el-radio-button>
        </el-radio-group>
      </div>
    </div>

    <el-row :gutter="16" class="mb-16">
      <el-col :span="16">
        <el-card header="退款趋势">
          <BaseChart :option="trendOption" height="320px" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card header="退费状态分布">
          <BaseChart :option="statusOption" height="320px" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="12">
        <el-card header="退费原因 TOP10">
          <BaseChart :option="reasonOption" height="320px" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card header="退款方式占比">
          <BaseChart :option="methodOption" height="320px" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import BaseChart from '@/components/BaseChart.vue'
import { operation } from '@/api/analytics'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
dayjs.extend(isoWeek)

const range = ref('30')
const grain = ref('day') // 趋势粒度: 日/周/月; 前端对后端按日数据做汇聚
const trend = ref([])
const status = ref([])
const reason = ref([])
const method = ref([])

function dateRange() {
  const end = dayjs()
  const start = end.subtract(Number(range.value), 'day')
  return { start: start.format('YYYY-MM-DD'), end: end.format('YYYY-MM-DD') }
}

// 把按日序列(date→{value,count})按周/月聚合成带标签的序列(前端纯计算, 后端无改动)
function aggregateTrend(rows, g) {
  if (g === 'day') {
    return {
      labels: rows.map(d => d.date.slice(5)),
      values: rows.map(d => d.value),
      counts: rows.map(d => d.count),
    }
  }
  const groups = new Map()
  rows.forEach(r => {
    const dt = dayjs(r.date)
    if (!dt.isValid()) return
    let key, label
    if (g === 'week') {
      const ws = dt.startOf('isoWeek')
      const we = ws.add(6, 'day')
      key = ws.format('YYYY-MM-DD')
      label = `${ws.format('MM-DD')}~${we.format('MM-DD')}`
    } else {
      key = dt.format('YYYY-MM')
      label = dt.format('YYYY-MM')
    }
    const g0 = groups.get(key) || { label, value: 0, count: 0 }
    g0.value += r.value || 0
    g0.count += r.count || 0
    groups.set(key, g0)
  })
  const sorted = [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
  return {
    labels: sorted.map(([, g0]) => g0.label),
    values: sorted.map(([, g0]) => g0.value),
    counts: sorted.map(([, g0]) => g0.count),
  }
}

const trendOption = computed(() => {
  const agg = aggregateTrend(trend.value, grain.value)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['退款金额', '退款笔数'] },
    grid: { left: 50, right: 50, bottom: 30, top: 40 },
    xAxis: { type: 'category', data: agg.labels, axisLabel: { rotate: agg.labels.length > 12 ? 30 : 0 } },
    yAxis: [
      { type: 'value', name: '金额(元)' },
      { type: 'value', name: '笔数' },
    ],
    series: [
      { name: '退款金额', type: 'line', smooth: true, areaStyle: {}, data: agg.values },
      { name: '退款笔数', type: 'bar', yAxisIndex: 1, data: agg.counts },
    ],
  }
})

const statusOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
  legend: { bottom: 0 },
  series: [{
    type: 'pie', radius: ['40%', '70%'], data: status.value.map((s) => ({ name: s.name, value: s.count })),
  }],
}))

const reasonOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 120, right: 30, bottom: 30, top: 20 },
  xAxis: { type: 'value' },
  yAxis: { type: 'category', data: reason.value.map((r) => r.name).reverse() },
  series: [{ type: 'bar', data: reason.value.map((r) => r.value).reverse(), itemStyle: { color: '#ee6666' } }],
}))

const methodOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {c}元 ({d}%)' },
  legend: { bottom: 0 },
  series: [{
    type: 'pie', radius: '60%', data: method.value.map((m) => ({ name: m.name, value: m.value })),
  }],
}))

async function loadAll() {
  const r = dateRange()
  try {
    const [t, s, re, m] = await Promise.all([
      operation.trend(r),
      operation.status(),
      operation.reason({ limit: 10 }),
      operation.method(),
    ])
    trend.value = t.data
    status.value = s.data
    reason.value = re.data
    method.value = m.data
  } catch (e) {}
}

onMounted(loadAll)
</script>
