<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">投诉分析</div>
        <div class="page-sub">三源统一统计 · 内部投诉 / 商户号投诉 / 外部投诉 · 趋势 / 归因类型 / 接待人员</div>
      </div>
      <div class="flex gap-8">
        <el-date-picker
          v-model="range"
          type="daterange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          value-format="YYYY-MM-DD"
          style="width: 260px"
          @change="loadData"
        />
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </div>

    <el-row :gutter="16" class="mb-16">
      <el-col v-for="c in kpiCards" :key="c.key" :xs="12" :sm="8" :md="6">
        <div class="kpi-card" :style="{ background: c.bg }">
          <div class="kpi-label" :style="{ color: c.color }">{{ c.label }}</div>
          <div class="kpi-value" :style="{ color: c.color }">{{ c.value }}</div>
        </div>
      </el-col>
    </el-row>

    <el-tabs v-model="tab">
      <el-tab-pane v-for="src in sources" :key="src.key" :label="src.label" :name="src.key">
        <el-row :gutter="16">
          <el-col :xs="24" :lg="12">
            <el-card class="mb-16">
              <template #header><span style="font-weight: 600">归因/分类分布</span></template>
              <el-table :data="data[src.key].by_category" border stripe size="small" max-height="360">
                <el-table-column prop="name" label="类型" />
                <el-table-column prop="count" label="数量" width="100" align="right" />
              </el-table>
              <el-empty v-if="!data[src.key].by_category.length" description="暂无数据" :image-size="50" />
            </el-card>
          </el-col>
          <el-col :xs="24" :lg="12">
            <el-card class="mb-16">
              <template #header><span style="font-weight: 600">涉及接待/处理人员</span></template>
              <el-table :data="data[src.key].by_handler" border stripe size="small" max-height="360">
                <el-table-column prop="name" label="人员" />
                <el-table-column prop="count" label="数量" width="100" align="right" />
              </el-table>
              <el-empty v-if="!data[src.key].by_handler.length" description="暂无数据" :image-size="50" />
            </el-card>
          </el-col>
          <el-col :xs="24">
            <el-card>
              <template #header><span style="font-weight: 600">趋势(按日)</span></template>
              <BaseChart v-if="trendOption(src.key)" :option="trendOption(src.key)" height="320px" />
              <el-empty v-else description="暂无趋势数据" />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { complaintSummary } from '@/api/complaint'
import dayjs from 'dayjs'

const tab = ref('internal')
const range = ref(null)
const loading = ref(false)
const sources = [
  { key: 'internal', label: '内部投诉' },
  { key: 'merchant', label: '商户号投诉' },
  { key: 'external', label: '外部投诉' },
]
const data = reactive({
  internal: { total: 0, by_category: [], by_handler: [], trend: [] },
  merchant: { total: 0, by_category: [], by_handler: [], trend: [] },
  external: { total: 0, by_category: [], by_handler: [], trend: [] },
})

const kpiCards = computed(() => [
  { key: 'internal', label: '内部投诉', value: data.internal.total, color: '#2563eb', bg: '#eff6ff' },
  { key: 'merchant', label: '商户号投诉', value: data.merchant.total, color: '#7c3aed', bg: '#f5f3ff' },
  { key: 'external', label: '外部投诉', value: data.external.total, color: '#dc2626', bg: '#fef2f2' },
  { key: 'total', label: '投诉合计', value: data.internal.total + data.merchant.total + data.external.total, color: '#0f766e', bg: '#f0fdfa' },
])

function trendOption(key) {
  const rows = data[key].trend || []
  if (!rows.length) return null
  const x = rows.map((r) => String(r.day).slice(0, 10))
  const y = rows.map((r) => Number(r.count) || 0)
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 24, bottom: 40, top: 24 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '投诉量' },
    series: [{ type: 'bar', data: y, barMaxWidth: 18, itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] } }],
  }
}

async function loadData() {
  loading.value = true
  try {
    const params = {}
    if (range.value && range.value.length === 2) {
      params.start = range.value[0]
      params.end = range.value[1]
    }
    const res = await complaintSummary(params)
    const d = res.data || {}
    data.internal = d.internal || { total: 0, by_category: [], by_handler: [], trend: [] }
    data.merchant = d.merchant || { total: 0, by_category: [], by_handler: [], trend: [] }
    data.external = d.external || { total: 0, by_category: [], by_handler: [], trend: [] }
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.kpi-card { border-radius: 10px; padding: 14px 16px; min-height: 76px; display: flex; flex-direction: column; gap: 6px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 13px; font-weight: 600; }
.kpi-value { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }
</style>
