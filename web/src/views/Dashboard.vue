<template>
  <div class="page-container dashboard">
    <!-- 顶部问候 -->
    <div class="greeting">
      <div>
        <div class="page-title" style="margin: 0">{{ greeting }},{{ authStore.displayName }}</div>
        <div class="page-sub">{{ todayText }}</div>
      </div>
      <el-tag :type="roleTagType" effect="plain">{{ roleLabel }}</el-tag>
    </div>

    <!-- ============ 管理层工作台 ============ -->
    <template v-if="authStore.isManager">
      <!-- 汇总卡片 -->
      <div class="card-row" v-loading="loadingStats">
        <el-card class="stat-card" shadow="hover" @click="$router.push('/analytics/views')">
          <div class="stat">
            <div class="stat-icon" style="background: #ecf5ff"><el-icon :size="26" color="#409eff"><Money /></el-icon></div>
            <div class="stat-body">
              <div class="stat-label">退款总额(元)</div>
              <div class="stat-value">{{ formatMoney(summary.refund_total) }}</div>
            </div>
          </div>
        </el-card>
        <el-card class="stat-card" shadow="hover" @click="$router.push('/analytics/views')">
          <div class="stat">
            <div class="stat-icon" style="background: #fdf6ec"><el-icon :size="26" color="#e6a23c"><Document /></el-icon></div>
            <div class="stat-body">
              <div class="stat-label">退费笔数</div>
              <div class="stat-value">{{ formatNum(summary.refund_count) }}</div>
            </div>
          </div>
        </el-card>
        <el-card class="stat-card" shadow="hover" @click="$router.push('/analytics/cash')">
          <div class="stat">
            <div class="stat-icon" style="background: #fef0f0"><el-icon :size="26" color="#f56c6c"><Wallet /></el-icon></div>
            <div class="stat-body">
              <div class="stat-label">真实现金流出(元)</div>
              <div class="stat-value">{{ formatMoney(summary.cash_out_total) }}</div>
            </div>
          </div>
        </el-card>
        <el-card class="stat-card" shadow="hover" @click="$router.push('/analytics/operator')">
          <div class="stat">
            <div class="stat-icon" style="background: #f0f9eb"><el-icon :size="26" color="#67c23a"><ChatDotRound /></el-icon></div>
            <div class="stat-body">
              <div class="stat-label">客服接待量</div>
              <div class="stat-value">{{ formatNum(summary.cs_reception_count) }}</div>
            </div>
          </div>
        </el-card>
        <el-card class="stat-card" shadow="hover" @click="$router.push('/todos')">
          <div class="stat">
            <div class="stat-icon" style="background: #f4f4f5"><el-icon :size="26" color="#909399"><Bell /></el-icon></div>
            <div class="stat-body">
              <div class="stat-label">待办事项</div>
              <div class="stat-value">{{ todoCount }}<span class="stat-unit">条</span></div>
            </div>
          </div>
        </el-card>
      </div>

      <!-- 空数据提示 -->
      <el-alert
        v-if="!loadingStats && !summary.refund_count"
        class="mb-16"
        title="暂无业务数据"
        type="info"
        :closable="false"
        description="请前往 数据中心-数据上传 导入退费单、退款工单、钱包提现、商户号现金流等数据后查看分析。"
      />

      <!-- 趋势图 -->
      <el-card class="mb-16" v-if="summary.refund_count">
        <template #header>
          <div class="flex-between" style="margin: -8px 0">
            <span>退款趋势(近30天)</span>
            <el-button link type="primary" @click="$router.push('/analytics/operation')">查看更多 ›</el-button>
          </div>
        </template>
        <BaseChart :option="trendOption" height="280px" />
      </el-card>

      <!-- 两栏:预设视图 + 右侧动态 -->
      <el-row :gutter="16">
        <el-col :xs="24" :md="15">
          <el-card class="mb-16">
            <template #header>
              <div class="flex-between" style="margin: -8px 0">
                <span>预设分析视图</span>
                <el-button link type="primary" @click="$router.push('/analytics/views')">全部视图 ›</el-button>
              </div>
            </template>
            <div class="preset-grid" v-loading="loadingViews">
              <div v-for="v in presetViews" :key="v.id" class="preset-item" @click="$router.push(`/analytics/views/${v.id}`)">
                <el-icon :size="20" :color="chartIconColor(v.chart_type)"><component :is="chartIcon(v.chart_type)" /></el-icon>
                <span class="preset-name">{{ v.name }}</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
              <div v-if="!loadingViews && !presetViews.length" class="text-muted" style="grid-column: 1/-1; text-align: center; padding: 20px">
                暂无预设视图
              </div>
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="9">
          <!-- 未读策略 -->
          <el-card class="mb-16" v-if="unreadStrategies.length">
            <template #header><span>未读策略 <el-tag size="small" type="warning" round>{{ unreadStrategies.length }}</el-tag></span></template>
            <div class="strategy-list">
              <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
                <span class="unread-dot" />
                <span class="strategy-title">{{ s.title }}</span>
                <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
              </div>
            </div>
          </el-card>

          <!-- 待办与专项 -->
          <el-card class="mb-16">
            <template #header><span>待办与专项</span></template>
            <div class="mini-row" @click="$router.push('/todos')">
              <el-icon color="#e6a23c"><Bell /></el-icon>
              <span class="mini-label">待办事项</span>
              <span class="mini-count">{{ todoCount }}</span>
              <el-icon class="mini-arrow"><ArrowRight /></el-icon>
            </div>
            <div class="mini-row" @click="$router.push('/tasks')">
              <el-icon color="#409eff"><List /></el-icon>
              <span class="mini-label">专项任务</span>
              <span class="mini-count">{{ taskCount }}</span>
              <el-icon class="mini-arrow"><ArrowRight /></el-icon>
            </div>
          </el-card>

          <!-- 最近操作(仅 admin) -->
          <el-card v-if="authStore.isAdmin">
            <template #header>
              <div class="flex-between" style="margin: -8px 0">
                <span>最近操作</span>
                <el-button link type="primary" @click="$router.push('/system/operation-logs')">全部 ›</el-button>
              </div>
            </template>
            <div class="op-list" v-loading="loadingOps">
              <div v-for="op in recentOps" :key="op.id" class="op-item">
                <div class="op-top">
                  <el-tag size="small" :type="opTagType(op.action)">{{ op.action }}</el-tag>
                  <span class="op-time">{{ formatTime(op.created_at) }}</span>
                </div>
                <div class="op-meta">{{ op.username || '(未登录)' }} · {{ op.method }} {{ op.path }}</div>
              </div>
              <div v-if="!loadingOps && !recentOps.length" class="text-muted" style="text-align: center; padding: 16px">
                暂无操作记录
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- ============ 客服工作台 ============ -->
    <template v-else>
      <!-- 未读策略(最关键) -->
      <el-card class="mb-16">
        <template #header>
          <div class="flex-between" style="margin: -8px 0">
            <span>策略规范 <el-tag v-if="unreadStrategies.length" size="small" type="warning" round>{{ unreadStrategies.length }} 未读</el-tag></span>
            <el-button link type="primary" @click="$router.push('/strategy/publish')">查看全部 ›</el-button>
          </div>
        </template>
        <div v-if="unreadStrategies.length" class="strategy-list">
          <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
            <span class="unread-dot" />
            <span class="strategy-title">{{ s.title }}</span>
            <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
          </div>
        </div>
        <el-result v-else icon="success" title="策略已全部阅读" sub-title="当前没有未读的策略与规范">
          <template #extra>
            <el-button type="primary" @click="$router.push('/strategy/publish')">前往策略发布页</el-button>
          </template>
        </el-result>
      </el-card>

      <!-- 快捷入口 -->
      <el-card>
        <template #header>快捷入口</template>
        <div class="agent-shortcuts">
          <div class="shortcut-tile" @click="$router.push('/strategy/publish')">
            <el-icon :size="32" color="#409eff"><Document /></el-icon>
            <span>策略发布</span>
          </div>
          <div class="shortcut-tile" @click="$router.push('/todos')">
            <el-icon :size="32" color="#e6a23c"><Bell /></el-icon>
            <span>事项跟进</span>
            <el-badge v-if="todoCount" :value="todoCount" class="shortcut-badge" />
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import BaseChart from '@/components/BaseChart.vue'
import { useAuthStore } from '@/stores/auth'
import { getSummary, operation } from '@/api/analytics'
import { listTodos } from '@/api/todo'
import { listStrategy } from '@/api/strategy'
import { listAnalysisViews } from '@/api/analysis-view'
import { listOperationLogs } from '@/api/operationLog'
import { listTasks } from '@/api/task'
import dayjs from 'dayjs'

const authStore = useAuthStore()

const summary = ref({ refund_total: 0, refund_count: 0, cash_out_total: 0, cs_reception_count: 0 })
const todoCount = ref(0)
const taskCount = ref(0)
const trend = ref([])
const presetViews = ref([])
const unreadStrategies = ref([])
const recentOps = ref([])
const loadingStats = ref(false)
const loadingViews = ref(false)
const loadingOps = ref(false)

const greeting = computed(() => {
  const h = dayjs().hour()
  if (h < 6) return '凌晨好'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const todayText = computed(() => {
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][dayjs().day()]
  return dayjs().format('YYYY年MM月DD日') + ' ' + wd + ' · ' + (authStore.isManager ? '管理层工作台' : '客服工作台')
})
const roleLabel = computed(() => ({ admin: '管理员', supervisor: '主管', agent: '客服' }[authStore.role] || ''))
const roleTagType = computed(() => (authStore.role === 'admin' ? 'danger' : authStore.role === 'supervisor' ? 'warning' : 'info'))

const formatMoney = (v) => (v == null ? '0.00' : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const formatNum = (v) => (v == null ? '0' : Number(v).toLocaleString('zh-CN'))
const formatTime = (t) => (t ? dayjs(t).format('MM-DD HH:mm') : '-')

const chartIcon = (t) => ({ line: 'TrendCharts', bar: 'Histogram', pie: 'PieChart', table: 'Grid' }[t] || 'DataAnalysis')
const chartIconColor = (t) => ({ line: '#409eff', bar: '#67c23a', pie: '#e6a23c', table: '#909399' }[t] || '#409eff')

const opTagType = (a) => {
  if (a.includes('失败') || a.includes('拒绝') || a.includes('删除')) return 'danger'
  if (a.includes('发布') || a.includes('创建')) return 'success'
  if (a.includes('更新') || a.includes('跟进')) return 'warning'
  if (a.includes('登录')) return 'info'
  return 'info'
}

const trendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['退款金额', '退款笔数'], top: 0 },
  grid: { left: 56, right: 56, bottom: 30, top: 36 },
  xAxis: { type: 'category', data: trend.value.map((d) => d.date), axisLabel: { fontSize: 10 } },
  yAxis: [
    { type: 'value', name: '金额(元)', axisLabel: { formatter: (v) => (v >= 10000 ? v / 10000 + '万' : v) } },
    { type: 'value', name: '笔数' },
  ],
  series: [
    { name: '退款金额', type: 'line', smooth: true, areaStyle: { opacity: 0.15 }, data: trend.value.map((d) => d.value) },
    { name: '退款笔数', type: 'bar', yAxisIndex: 1, data: trend.value.map((d) => d.count) },
  ],
}))

async function loadStats() {
  loadingStats.value = true
  try {
    const [s, t] = await Promise.all([getSummary(), listTodos({ page: 1, page_size: 1 })])
    summary.value = s.data
    todoCount.value = t.data.total
  } catch {
    /* 忽略 */
  } finally {
    loadingStats.value = false
  }
}

async function loadTrend() {
  try {
    const end = dayjs()
    const start = end.subtract(30, 'day')
    const res = await operation.trend({ start: start.format('YYYY-MM-DD'), end: end.format('YYYY-MM-DD') })
    trend.value = res.data || []
  } catch {
    /* 忽略 */
  }
}

async function loadPresetViews() {
  loadingViews.value = true
  try {
    const res = await listAnalysisViews({ preset: true, page: 1, page_size: 100 })
    presetViews.value = (res.data.items || []).filter((v) => v.status === 'active')
  } catch {
    /* 忽略 */
  } finally {
    loadingViews.value = false
  }
}

async function loadStrategies() {
  try {
    const res = await listStrategy({ status: 'published', page: 1, page_size: 50 })
    unreadStrategies.value = (res.data.items || []).filter((s) => !s.has_read).slice(0, 5)
  } catch {
    /* 忽略 */
  }
}

async function loadTasks() {
  try {
    const res = await listTasks({ page: 1, page_size: 1 })
    taskCount.value = res.data.total
  } catch {
    /* 忽略 */
  }
}

async function loadRecentOps() {
  loadingOps.value = true
  try {
    const res = await listOperationLogs({ page: 1, page_size: 6 })
    recentOps.value = res.data.items || []
  } catch {
    /* 忽略 */
  } finally {
    loadingOps.value = false
  }
}

onMounted(async () => {
  await loadStats()
  if (authStore.isManager) {
    loadStrategies()
    loadTasks()
    if (summary.value.refund_count) loadTrend()
    loadPresetViews()
    if (authStore.isAdmin) loadRecentOps()
  } else {
    loadStrategies()
    loadTasks()
  }
})
</script>

<style scoped>
.dashboard {
  padding-bottom: 24px;
}
.greeting {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.card-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.stat-card {
  cursor: pointer;
  transition: transform 0.15s;
}
.stat-card:hover {
  transform: translateY(-2px);
}
.stat {
  display: flex;
  align-items: center;
  gap: 14px;
}
.stat-icon {
  width: 52px;
  height: 52px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-label {
  color: #909399;
  font-size: 13px;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  margin-top: 2px;
}
.stat-unit {
  font-size: 13px;
  font-weight: 400;
  color: #909399;
  margin-left: 4px;
}
.preset-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}
.preset-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.preset-item:hover {
  border-color: #409eff;
  background: #ecf5ff;
}
.preset-name {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preset-arrow {
  color: #c0c4cc;
}
.strategy-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.strategy-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
}
.strategy-item:hover {
  background: #f5f7fa;
}
.unread-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e6a23c;
  flex-shrink: 0;
}
.strategy-title {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mini-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
}
.mini-row:last-child {
  border-bottom: none;
}
.mini-row:hover .mini-label {
  color: #409eff;
}
.mini-label {
  flex: 1;
  font-size: 14px;
  color: #303133;
}
.mini-count {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}
.mini-arrow {
  color: #c0c4cc;
}
.op-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.op-item {
  padding: 6px 0;
  border-bottom: 1px solid #f5f5f5;
}
.op-item:last-child {
  border-bottom: none;
}
.op-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2px;
}
.op-time {
  font-size: 12px;
  color: #909399;
}
.op-meta {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', Menlo, monospace;
}
.agent-shortcuts {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.shortcut-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 28px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
  min-width: 120px;
}
.shortcut-tile:hover {
  border-color: #409eff;
  background: #ecf5ff;
  transform: translateY(-2px);
}
.shortcut-tile span {
  font-size: 14px;
  color: #303133;
}
.shortcut-badge {
  position: absolute;
  top: 8px;
  right: 8px;
}
.text-muted {
  color: #c0c4cc;
}
.mb-16 {
  margin-bottom: 16px;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
