<template>
  <el-card v-loading="displayLoading" shadow="hover" class="view-item-card">
    <template #header>
      <div class="card-header">
        <span class="card-title" :title="displayTitle">{{ displayTitle }}</span>
        <el-tag v-if="viewMeta?.is_preset" type="warning" size="small">预设</el-tag>
        <el-tooltip content="另存为视图(复制为可编辑副本)" placement="top">
          <el-button :icon="CopyDocument" text size="small" class="action-btn" @click="saveAsView" />
        </el-tooltip>
        <el-tooltip content="刷新" placement="top">
          <el-button :icon="Refresh" text size="small" class="action-btn" @click="loadData" />
        </el-tooltip>
      </div>
    </template>

    <div v-if="displayError" class="error-box">
      <el-icon color="#f56c6c"><Warning /></el-icon>
      <span>{{ displayError }}</span>
      <el-button text type="primary" size="small" @click="loadData">重试</el-button>
    </div>

    <div v-else-if="displayResult" class="chart-area">
      <BaseChart
        v-if="displayResult.chart_type !== 'table'"
        :option="chartOption"
        :height="chartHeight"
      />
      <el-table
        v-else
        :data="displayResult.data.rows"
        border
        stripe
        size="small"
        max-height="360"
      >
        <el-table-column
          v-for="col in displayResult.data.columns"
          :key="col"
          :prop="col"
          :label="col"
          show-overflow-tooltip
        >
          <template #default="{ row }">{{ fmtVal(row[col], col) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-empty v-else-if="!displayLoading" description="暂无数据" :image-size="60" />
  </el-card>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Warning, CopyDocument } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { getAnalysisView, executeAnalysisView, createAnalysisView } from '@/api/analysis-view'

const props = defineProps({
  viewId: { type: [Number, String], required: true },
  title: { type: String, default: '' },
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  reloadKey: { type: [Number, String], default: 0 },
  // 批量预取模式:父级(DashboardView)一次批量取全部结果后分发
  prefetchedResult: { type: Object, default: undefined },
  prefetchedError: { type: String, default: undefined },
  prefetchedView: { type: Object, default: undefined }, // { name, ratios, is_preset }
  batchLoading: { type: Boolean, default: false },
})

// 是否批量预取模式(undefined 表示未传,需区分 null 结果与未传)
const usePrefetched = computed(
  () => props.prefetchedResult !== undefined || props.prefetchedError !== undefined
)

// 视图元数据:预取模式用 prefetchedView,独立模式 getAnalysisView 拉取
const fetchedView = ref(null)
const viewMeta = computed(() => (usePrefetched.value ? props.prefetchedView : fetchedView.value))

// 独立请求状态(独立模式 + 刷新按钮)
const localResult = ref(null)
const localErrorMsg = ref('')
const localLoading = ref(false)
// 刷新后用本地结果覆盖预取结果(直到父级下次批量更新)
const hasLocalOverride = ref(false)

const displayResult = computed(() => {
  if (hasLocalOverride.value) return localResult.value
  if (usePrefetched.value) return props.prefetchedResult ?? null
  return localResult.value
})
const displayError = computed(() => {
  if (hasLocalOverride.value) return localErrorMsg.value
  if (usePrefetched.value) return props.prefetchedError || ''
  return localErrorMsg.value
})
const displayLoading = computed(() => {
  if (hasLocalOverride.value) return localLoading.value
  if (usePrefetched.value) return props.batchLoading
  return localLoading.value
})

const displayTitle = computed(() => props.title || viewMeta.value?.name || '加载中...')
const chartHeight = computed(() => (displayResult.value?.chart_type === 'table' ? 'auto' : '320px'))

const percentAliases = computed(
  () => new Set((viewMeta.value?.ratios || []).filter((r) => r.as_percent && r.alias).map((r) => r.alias))
)

const chartOption = computed(() => {
  if (!displayResult.value) return {}
  const r = displayResult.value
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
      ...(s.stack ? { stack: s.stack } : {}),
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
    fetchedView.value = res.data
  } catch (e) {
    localErrorMsg.value = '视图不存在或已归档'
  }
}

async function loadData() {
  // 独立请求(单卡刷新 / 独立模式),刷新后用本地结果覆盖
  if (!props.viewId) return
  localLoading.value = true
  localErrorMsg.value = ''
  try {
    if (!fetchedView.value && !usePrefetched.value) await loadView()
    if (!fetchedView.value && !usePrefetched.value) return
    const params = {}
    if (props.start) params.start = props.start
    if (props.end) params.end = props.end
    const res = await executeAnalysisView({ view_id: props.viewId, params })
    localResult.value = res.data
    hasLocalOverride.value = true
  } catch (e) {
    localErrorMsg.value = e.response?.data?.detail || e.message || '执行失败'
    localResult.value = null
    hasLocalOverride.value = true
  } finally {
    localLoading.value = false
  }
}

async function saveAsView() {
  try {
    await ElMessageBox.confirm('将复制该视图为可编辑的自定义视图?', '另存为视图', { type: 'info' })
  } catch {
    return // 取消
  }
  try {
    // 取视图完整配置(预取模式无完整 segments,需拉详情)
    const res = await getAnalysisView(props.viewId)
    const v = res.data
    await createAnalysisView({
      code: null,
      name: `${v.name}(副本)`,
      description: v.description,
      chart_type: v.chart_type,
      segments: v.segments,
      merge_strategy: v.merge_strategy,
      classification_rule_ids: v.classification_rule_ids || [],
      time_range_default: v.time_range_default,
      ratios: v.ratios || [],
      cumulative: v.cumulative || [],
    })
    ElMessage.success('已复制为自定义视图,可在视图构建器编辑')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '复制失败')
  }
}

// 预取数据变化(父级批量刷新)→ 清除本地覆盖,回到预取结果
watch(
  [() => props.prefetchedResult, () => props.prefetchedError],
  () => {
    if (usePrefetched.value) hasLocalOverride.value = false
  }
)

// 独立模式:start/end/reloadKey 变化 → 重新请求
watch(
  () => [props.start, props.end, props.reloadKey],
  () => {
    if (!usePrefetched.value) loadData()
  }
)

onMounted(() => {
  if (!usePrefetched.value) loadData()
  // 预取模式:数据与元数据均由父级批量结果传入,不主动拉取(避免 N 次额外请求)
})
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
.action-btn {
  margin-left: auto;
}
.action-btn + .action-btn {
  margin-left: 0;
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
