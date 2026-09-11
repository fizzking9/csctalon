<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="flex-between" style="gap: 12px">
        <el-button :icon="ArrowLeft" text @click="goBack">返回</el-button>
        <div class="page-title" style="margin: 0">{{ view?.name || '加载中...' }}</div>
        <el-tag v-if="view" :type="view.is_preset ? 'warning' : 'success'" size="small">
          {{ view.is_preset ? '预设' : '自定义' }}
        </el-tag>
      </div>
      <div v-if="view" class="filter-bar">
        <el-radio-group v-model="rangeDays" size="small" @change="loadData">
          <el-radio-button :value="7">近7天</el-radio-button>
          <el-radio-button :value="30">近30天</el-radio-button>
          <el-radio-button :value="90">近90天</el-radio-button>
          <el-radio-button :value="0">全部</el-radio-button>
        </el-radio-group>
        <el-radio-group v-if="hasTimeDim" v-model="grainMode" size="small" @change="loadData">
          <el-radio-button value="day">按日</el-radio-button>
          <el-radio-button value="week">按周</el-radio-button>
          <el-radio-button value="month">按月</el-radio-button>
        </el-radio-group>
        <el-button :icon="Promotion" @click="publishDialogVisible = true">发布为看板</el-button>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </div>

    <el-card v-if="view?.description" class="mb-16" shadow="never">
      <span class="desc-label">说明:</span>{{ view.description }}
    </el-card>

    <el-card v-loading="loading">
      <div v-if="result" class="chart-area">
        <BaseChart v-if="result.chart_type !== 'table'" :option="chartOption" :height="chartHeight" />
        <el-table v-else :data="result.data.rows" border stripe size="small" max-height="600">
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
      <el-empty v-else-if="!loading" description="暂无数据" />
      <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>
    </el-card>

    <!-- 视图配置详情(可折叠) -->
    <el-card v-if="view" class="mt-16" shadow="never">
      <template #header>
        <div class="flex-between">
          <span>视图配置</span>
          <span class="config-hint">共 {{ view.segments.length }} 个数据源片段</span>
        </div>
      </template>
      <div v-if="view.ratios?.length" class="detail-row" style="margin-bottom: 12px">
        <span class="detail-label">比率:</span>
        <el-tag v-for="(r, ri) in view.ratios" :key="ri" type="info" size="small" style="margin-right: 4px">
          {{ r.alias }} = {{ r.numerator }}÷{{ r.denominator }}{{ r.as_percent ? '%' : '' }}
        </el-tag>
      </div>
      <el-collapse>
        <el-collapse-item v-for="(seg, idx) in view.segments" :key="idx" :name="idx">
          <template #title>
            <span>片段 {{ idx + 1 }}: {{ seg.label || seg.source_code }}</span>
            <el-tag size="small" style="margin-left: 8px">{{ seg.source_code }}</el-tag>
          </template>
          <div class="seg-detail">
            <div class="detail-row">
              <span class="detail-label">维度:</span>
              <el-tag v-for="(d, di) in seg.dimensions" :key="di" size="small" style="margin-right: 4px">
                {{ d.alias || d.field }}<span v-if="d.role === 'time'">[{{ d.time_grain || 'day' }}]</span>
              </el-tag>
            </div>
            <div class="detail-row">
              <span class="detail-label">度量:</span>
              <el-tag v-for="(m, mi) in seg.measures" :key="mi" type="warning" size="small" style="margin-right: 4px">
                {{ m.alias || m.field }}({{ m.agg }})
              </el-tag>
            </div>
            <div v-if="seg.filters?.length" class="detail-row">
              <span class="detail-label">筛选:</span>
              <span v-for="(f, fi) in seg.filters" :key="fi" class="filter-text">
                {{ f.field }} {{ f.op }} {{ Array.isArray(f.value) ? f.value.join(',') : f.value }}
              </span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <PublishDashboardDialog v-model="publishDialogVisible" :view="view" @success="onPublishSuccess" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Refresh, Promotion } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import PublishDashboardDialog from '@/components/PublishDashboardDialog.vue'
import { getAnalysisView, executeAnalysisView } from '@/api/analysis-view'
import dayjs from 'dayjs'

const route = useRoute()
const router = useRouter()

const view = ref(null)
const result = ref(null)
const loading = ref(false)
const rangeDays = ref(30)
const grainMode = ref('day') // 时间粒度:day/week/month,仅趋势类(line/bar)视图可切
const errorMsg = ref('')
const publishDialogVisible = ref(false)

const chartHeight = computed(() => (result.value?.chart_type === 'table' ? 'auto' : '400px'))

// 仅含 time 维度的趋势类(line/bar)视图才显示粒度选择器;饼图/表格/明细无 time 维度自动忽略
const hasTimeDim = computed(() =>
  ['line', 'bar'].includes(view.value?.chart_type) &&
  (view.value?.segments || []).some((s) => (s.dimensions || []).some((d) => d.role === 'time'))
)

// 百分比比率别名集合(从已存视图的 ratios 读取,用于表格列格式化:追加 % 后缀)
const percentAliases = computed(() => new Set((view.value?.ratios || []).filter((r) => r.as_percent && r.alias).map((r) => r.alias)))

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
    series: (data.series || []).map((s) => {
      const t = s.series_type || r.chart_type
      return {
        name: s.name,
        type: t,
        smooth: t === 'line',
        data: s.data,
        ...(s.stack ? { stack: s.stack } : {}),
      }
    }),
  }
})

function fmtVal(v, col) {
  if (v == null) return '-'
  if (typeof v === 'number') {
    // 百分比比率列:追加 % 后缀
    if (col && percentAliases.value.has(col)) return `${v.toFixed(2)}%`
    return v.toFixed(2)
  }
  return v
}

function goBack() {
  router.push('/analytics/views')
}

function onPublishSuccess(dashboard) {
  if (dashboard?.id) router.push(`/dashboards/view/${dashboard.id}`)
}

async function loadView() {
  const id = Number(route.params.id)
  if (!id) return
  const res = await getAnalysisView(id)
  view.value = res.data
}

async function loadData() {
  if (!view.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const params = {}
    if (rangeDays.value > 0) {
      const end = dayjs()
      const start = end.subtract(rangeDays.value, 'day')
      params.start = start.format('YYYY-MM-DD')
      params.end = end.format('YYYY-MM-DD')
    }
    if (grainMode.value !== 'day') params.time_grain = grainMode.value
    const res = await executeAnalysisView({ view_id: view.value.id, params })
    result.value = res.data
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || e.message || '执行失败'
    result.value = null
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadView()
  // 如果视图有默认时间范围,初始化 rangeDays
  if (view.value?.time_range_default?.days) {
    rangeDays.value = view.value.time_range_default.days
  }
  loadData()
})
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.chart-area {
  min-height: 400px;
}
.desc-label {
  color: #909399;
  margin-right: 8px;
}
.error-msg {
  color: #f56c6c;
  padding: 12px;
  background: #fef0f0;
  border-radius: 4px;
  margin-top: 12px;
}
.config-hint {
  font-size: 12px;
  color: #909399;
}
.seg-detail {
  padding: 8px 0;
}
.detail-row {
  margin-bottom: 8px;
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 4px;
}
.detail-label {
  color: #909399;
  min-width: 50px;
  font-size: 13px;
}
.filter-text {
  font-size: 12px;
  color: #606266;
  margin-right: 12px;
  padding: 2px 6px;
  background: #f5f7fa;
  border-radius: 3px;
}
</style>
