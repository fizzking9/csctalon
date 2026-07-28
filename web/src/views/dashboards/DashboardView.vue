<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="flex-between" style="gap: 12px">
        <div class="page-title" style="margin: 0">{{ dashboard?.name || '加载中...' }}</div>
        <el-tag v-if="dashboard" size="small">{{ dashboard.category }}</el-tag>
        <el-tag v-if="dashboard?.is_preset" type="warning" size="small">预设</el-tag>
      </div>
      <div v-if="dashboard" class="filter-bar">
        <el-radio-group v-model="rangeDays" size="small" @change="onRangeChange">
          <el-radio-button :label="7">近7天</el-radio-button>
          <el-radio-button :label="30">近30天</el-radio-button>
          <el-radio-button :label="90">近90天</el-radio-button>
          <el-radio-button :label="0">默认</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" @click="reloadKey++">刷新</el-button>
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
          :reload-key="reloadKey"
        />
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import ViewItemCard from '@/components/ViewItemCard.vue'
import { getDashboard, listDashboards } from '@/api/dashboard'

const route = useRoute()

// 预设看板路由名 → code 映射
const CODE_BY_NAME = {
  DashboardRefund: 'preset_dashboard_refund',
  DashboardComplaint: 'preset_dashboard_complaint',
  DashboardReception: 'preset_dashboard_reception',
  DashboardPerformance: 'preset_dashboard_performance',
}

const props = defineProps({
  dashboardId: { type: [Number, String], default: null },
})

const dashboard = ref(null)
const rangeDays = ref(30)
const reloadKey = ref(0)
const errorMsg = ref('')

const visibleItems = computed(() => (dashboard.value?.items || []).filter((i) => i.visible !== false))

const effectiveStart = computed(() => {
  if (rangeDays.value <= 0) return ''
  return dayjs().subtract(rangeDays.value, 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => (rangeDays.value <= 0 ? '' : dayjs().format('YYYY-MM-DD')))

function onRangeChange() {
  // rangeDays 变化 → effectiveStart/End 变 → ViewItemCard watch 自动重载
}

async function resolveDashboardId() {
  if (props.dashboardId) return Number(props.dashboardId)
  // 从路由推断 code
  const code = route.params.code || CODE_BY_NAME[route.name]
  if (!code) return null
  const res = await listDashboards({ page: 1, page_size: 100 })
  const d = res.data.items.find((x) => x.code === code)
  return d?.id || null
}

async function loadDashboard() {
  errorMsg.value = ''
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
      rangeDays.value = dashboard.value.time_range_default.days
    }
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载看板失败'
  }
}

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
