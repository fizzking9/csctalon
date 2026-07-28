<template>
  <el-card v-loading="loading" shadow="hover" class="view-item-card">
    <template #header>
      <div class="card-header">
        <span class="card-title" :title="displayTitle">{{ displayTitle }}</span>
        <el-tag v-if="view?.is_preset" type="warning" size="small">预设</el-tag>
        <el-button :icon="Refresh" text size="small" class="refresh-btn" @click="loadData" />
      </div>
    </template>

    <div v-if="errorMsg" class="error-box">
      <el-icon color="#f56c6c"><Warning /></el-icon>
      <span>{{ errorMsg }}</span>
      <el-button text type="primary" size="small" @click="loadData">重试</el-button>
    </div>

    <div v-else-if="result" class="chart-area">
      <BaseChart
        v-if="result.chart_type !== 'table'"
        :option="chartOption"
        :height="chartHeight"
      />
      <el-table
        v-else
        :data="result.data.rows"
        border
        stripe
        size="small"
        max-height="360"
      >
        <el-table-column
          v-for="col in result.data.columns"
          :key="col"
          :prop="col"
          :label="col"
          show-overflow-tooltip
        >
          <template #default="{ row }">{{ fmtVal(row[col], col) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-empty v-else-if="!loading" description="暂无数据" :image-size="60" />
  </el-card>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { Refresh, Warning } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { getAnalysisView, executeAnalysisView } from '@/api/analysis-view'

const props = defineProps({
  viewId: { type: [Number, String], required: true },
  title: { type: String, default: '' },
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  reloadKey: { type: [Number, String], default: 0 },
})

const view = ref(null)
const result = ref(null)
const loading = ref(false)
const errorMsg = ref('')

const displayTitle = computed(() => props.title || view.value?.name || '加载中...')
const chartHeight = computed(() => (result.value?.chart_type === 'table' ? 'auto' : '320px'))

// 百分比比率别名集合(表格列格式化用)
const percentAliases = computed(
  () => new Set((view.value?.ratios || []).filter((r) => r.as_percent && r.alias).map((r) => r.alias))
)

const chartOption = computed(() => {
  if (!result.value) return {}
  const r = result.value
  const data = r.data
  if (r.chart_type === 'pie') {
    return {
      tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
      legend: { type: 'scroll', orient: 'vertical', right: 10, top: 'middle' },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        data: data.items?.map((i) => ({ name: i.name, value: Number(i.value) })) || [],
      }],
    }
  }
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const lines = [params[0]?.axisValue || '']
        params.forEach((p) => {
          lines.push(`${p.marker} ${p.seriesName}: ${Number(p.value).toFixed(2)}`)
        })
        return lines.join('<br/>')
      },
    },
    legend: { data: data.series?.map((s) => s.name) || [] },
    grid: { left: 60, right: 30, bottom: 40, top: 40 },
    xAxis: { type: 'category', data: data.x_axis || [], axisLabel: { rotate: data.x_axis?.length > 15 ? 30 : 0 } },
    yAxis: { type: 'value' },
    series: (data.series || []).map((s) => ({
      name: s.name,
      type: r.chart_type,
      smooth: r.chart_type === 'line',
      data: s.data,
    })),
  }
})

function fmtVal(v, col) {
  if (v == null) return '-'
  if (typeof v === 'number') {
    if (col && percentAliases.value.has(col)) return `${v.toFixed(2)}%`
    return v.toFixed(2)
  }
  return v
}

async function loadView() {
  try {
    const res = await getAnalysisView(props.viewId)
    view.value = res.data
  } catch (e) {
    errorMsg.value = '视图不存在或已归档'
  }
}

async function loadData() {
  if (!props.viewId) return
  loading.value = true
  errorMsg.value = ''
  try {
    if (!view.value) await loadView()
    if (!view.value) return
    const params = {}
    if (props.start) params.start = props.start
    if (props.end) params.end = props.end
    const res = await executeAnalysisView({ view_id: props.viewId, params })
    result.value = res.data
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || e.message || '执行失败'
    result.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.start, props.end, props.reloadKey],
  () => loadData()
)

onMounted(loadData)
</script>

<style scoped>
.view-item-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-title {
  font-weight: 600;
  font-size: 14px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.refresh-btn {
  margin-left: auto;
}
.chart-area {
  min-height: 320px;
}
.error-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: #f56c6c;
  background: #fef0f0;
  border-radius: 4px;
  font-size: 13px;
}
</style>
