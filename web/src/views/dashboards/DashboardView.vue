<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="flex-between" style="gap: 12px">
        <div class="page-title" style="margin: 0">{{ dashboard?.name || '加载中...' }}</div>
        <el-tag v-if="dashboard" size="small">{{ dashboard.category }}</el-tag>
        <el-tag v-if="dashboard?.is_preset" type="warning" size="small">预设</el-tag>
      </div>
      <div v-if="dashboard" class="filter-bar">
        <el-radio-group v-model="rangeMode" size="small" @change="onRangeChange">
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
          <el-radio-button label="default">默认</el-radio-button>
          <el-radio-button label="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="rangeMode === 'custom'"
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width: 240px"
          @change="onRangeChange"
        />
        <el-radio-group v-model="grainMode" size="small" @change="onGrainChange">
          <el-radio-button label="day">按日</el-radio-button>
          <el-radio-button label="week">按周</el-radio-button>
          <el-radio-button label="month">按月</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" :loading="batchLoading" @click="loadAllItems">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="dashboard?.description"
      :title="dashboard.description"
      type="info"
      :closable="false"
      class="mb-16"
    />

    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <el-row v-if="dashboard" :gutter="16">
      <el-col
        v-for="item in visibleItems"
        :key="item.view_id"
        :span="item.span"
      >
        <ViewItemCard
          :view-id="item.view_id"
          :title="item.title || ''"
          :start="effectiveStart"
          :end="effectiveEnd"
          :prefetched-result="itemResult(item.view_id).result"
          :prefetched-error="itemResult(item.view_id).error"
          :prefetched-view="itemView(item.view_id)"
          :batch-loading="batchLoading"
        />
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import ViewItemCard from '@/components/ViewItemCard.vue'
import { getDashboard, listDashboards, executeDashboard } from '@/api/dashboard'

const route = useRoute()

// 预设看板路由名 → code 映射(接待看板已独立为 Reception.vue,此处无)
const CODE_BY_NAME = {
  DashboardRefund: 'preset_dashboard_refund',
  DashboardComplaint: 'preset_dashboard_complaint',
  DashboardPerformance: 'preset_dashboard_performance',
}

const props = defineProps({
  dashboardId: { type: [Number, String], default: null },
})

const dashboard = ref(null)
const rangeMode = ref('30') // '7'|'30'|'90'|'default'|'custom'
const customRange = ref([])
const grainMode = ref('day') // 时间粒度:day/week/month,看板级一次性应用到所有趋势项(饼图/表格自动忽略)
const batchLoading = ref(false)
const batchResults = ref({}) // { view_id: { result, error, view_name, ratios, is_preset } }
const errorMsg = ref('')

const visibleItems = computed(() => (dashboard.value?.items || []).filter((i) => i.visible !== false))

const effectiveStart = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().format('YYYY-MM-DD')
})

// 批量结果取值(null 而非 undefined,确保 ViewItemCard usePrefetched 为 true)
function itemResult(viewId) {
  const r = batchResults.value[viewId]
  return r || { result: null, error: null }
}
function itemView(viewId) {
  const r = batchResults.value[viewId]
  if (!r || r.view_name === null || r.view_name === undefined) return undefined
  return { name: r.view_name, ratios: r.ratios, is_preset: r.is_preset }
}

function onRangeChange() {
  // rangeMode/customRange 变化 → effectiveStart/End 变 → watch 触发 loadAllItems
}

function onGrainChange() {
  // 粒度变化不触发 effectiveStart/End 的 watch,需显式重载
  loadAllItems()
}

async function resolveDashboardId() {
  if (props.dashboardId) return Number(props.dashboardId)
  if (route.params.id) return Number(route.params.id) // /dashboards/view/:id
  const code = route.params.code || CODE_BY_NAME[route.name]
  if (!code) return null
  const res = await listDashboards({ page: 1, page_size: 100 })
  const d = res.data.items.find((x) => x.code === code)
  return d?.id || null
}

async function loadDashboard() {
  errorMsg.value = ''
  // 重置状态,避免路由切换时残留旧看板内容(4 个预设看板共用本组件,实例被复用)
  dashboard.value = null
  batchResults.value = {}
  try {
    const id = await resolveDashboardId()
    if (!id) {
      errorMsg.value = '看板不存在或无权访问'
      return
    }
    const res = await getDashboard(id)
    dashboard.value = res.data
    // 初始化时间范围为看板默认
    if (dashboard.value.time_range_default?.days) {
      rangeMode.value = String(dashboard.value.time_range_default.days)
    } else {
      rangeMode.value = 'default'
    }
    await loadAllItems()
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载看板失败'
  }
}

async function loadAllItems() {
  if (!dashboard.value) return
  batchLoading.value = true
  try {
    const params = {}
    if (effectiveStart.value) params.start = effectiveStart.value
    if (effectiveEnd.value) params.end = effectiveEnd.value
    if (grainMode.value !== 'day') params.time_grain = grainMode.value
    const res = await executeDashboard(dashboard.value.id, params)
    const map = {}
    for (const it of res.data.items || []) {
      map[it.view_id] = it
    }
    batchResults.value = map
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '批量加载失败'
  } finally {
    batchLoading.value = false
  }
}

// 时间范围变化 → 重新批量加载
watch([effectiveStart, effectiveEnd], () => {
  if (dashboard.value) loadAllItems()
})

// 路由切换 → 重新加载看板
// 4 个预设看板(退款/投诉/接待/绩效)共用本组件,Vue Router 复用实例,
// onMounted 只在首次挂载触发,切换看板时需 watch 路由才能重新加载。
watch(
  () => [route.name, route.params.id, route.params.code],
  () => { loadDashboard() }
)

onMounted(loadDashboard)
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.error-msg {
  color: #f56c6c;
  padding: 12px;
  background: #fef0f0;
  border-radius: 4px;
}
</style>
