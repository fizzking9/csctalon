<template>
  <div class="page-container today-dashboard">
    <!-- 顶部标题区 -->
    <div class="flex-between mb-12 td-header">
      <div class="page-title" style="margin: 0">
        今日看板
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag size="small" type="success" effect="plain" style="margin-left: 8px">实时数据</el-tag>
      </div>
      <div class="header-actions">
        <el-button size="small" @click="loadAll" :loading="loading">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-tag v-if="lastUpdate" size="small" effect="plain" type="info">
          更新于 {{ lastUpdate }}
        </el-tag>
      </div>
    </div>

    <!-- 值班组长提示条 -->
    <div v-if="dutyLeaders.length" class="duty-leader-bar mb-12">
      <div class="duty-label">
        <el-icon><Timer /></el-icon>
        <span>今日值班</span>
      </div>
      <el-space wrap size="small" class="duty-chips">
        <el-tag
          v-for="(l, i) in dutyLeaders"
          :key="i"
          type="warning"
          effect="dark"
          size="small"
        >
          <span class="chip-name">{{ l.name }}</span>
          <span class="chip-sep">·</span>
          <span class="chip-group">{{ l.group || l.department }}</span>
        </el-tag>
      </el-space>
    </div>

    <!-- 模块1: 核心KPI (紧凑版) -->
    <section class="section-card mb-16">
      <el-row :gutter="12">
        <el-col :xs="12" :sm="4" :md="4" v-for="c in kpiCards" :key="c.key">
          <div class="mini-kpi-card" :class="c.colorClass">
            <div class="mini-kpi-icon">
              <el-icon :size="20"><component :is="c.icon" /></el-icon>
            </div>
            <div class="mini-kpi-info">
              <div class="mini-kpi-label">{{ c.label }}</div>
              <div class="mini-kpi-value">{{ c.displayValue }}</div>
              <div v-if="c.yoy" class="mini-kpi-yoy" :class="c.yoyClass">
                {{ c.yoy }}
              </div>
            </div>
          </div>
        </el-col>
      </el-row>
    </section>

    <!-- 模块2: 实时趋势 -->
    <section class="section-card mb-16">
      <div class="section-header-row">
        <h3 class="section-title">实时趋势</h3>
        <div class="section-desc">今日各时段接待与退款情况</div>
      </div>
      <ChartCard body-style="padding: 12px">
        <BaseChart :option="trendOption" height="220px" />
      </ChartCard>
    </section>

    <!-- 模块3: 异常事件 & 当前政策 -->
    <el-row :gutter="12" class="mb-16">
      <!-- 异常事件 -->
      <el-col :xs="24" :md="12">
        <section class="section-card h-full">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#dc2626; margin-right:4px"><WarningFilled /></el-icon>
              今日异常事件
            </h3>
            <el-tag v-if="queueData.incidents && queueData.incidents.length" type="danger" size="small" effect="dark">
              {{ queueData.incidents.length }} 条待处理
            </el-tag>
          </div>
          <div v-if="queueData.incidents && queueData.incidents.length" class="incident-list">
            <div v-for="(inc, i) in queueData.incidents.slice(0, 5)" :key="i" class="incident-item">
              <el-tag :type="inc.level === 'high' ? 'danger' : 'warning'" size="small" effect="plain">
                {{ inc.level === 'high' ? '高危' : '一般' }}
              </el-tag>
              <span class="incident-title">{{ inc.title }}</span>
              <span class="incident-time">{{ formatShortTime(inc.time) }}</span>
            </div>
          </div>
          <el-empty v-else description="今日暂无异常事件" :image-size="60" />
        </section>
      </el-col>

      <!-- 当前政策 -->
      <el-col :xs="24" :md="12">
        <section class="section-card h-full">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#2563eb; margin-right:4px"><Document /></el-icon>
              当前执行政策
            </h3>
          </div>
          <div v-if="currentPolicies.length" class="policy-list">
            <div v-for="(p, i) in currentPolicies" :key="i" class="policy-item">
              <el-icon class="policy-icon"><CircleCheckFilled /></el-icon>
              <div class="policy-info">
                <div class="policy-name">{{ p.name }}</div>
                <div class="policy-desc">{{ p.description }}</div>
              </div>
              <el-tag size="small" :type="p.type === 'warning' ? 'warning' : 'success'">
                {{ p.status }}
              </el-tag>
            </div>
          </div>
          <el-empty v-else description="暂无正在执行的政策" :image-size="60" />
        </section>
      </el-col>
    </el-row>

    <!-- 模块4: 待办 & 专项任务 -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#f59e0b; margin-right:4px"><Bell /></el-icon>
              我的待办
            </h3>
            <el-link type="primary" :underline="false" @click="$router.push('/todos')">全部 →</el-link>
          </div>
          <div v-if="queueData.todos && queueData.todos.length" class="todo-list">
            <div v-for="(t, i) in queueData.todos.slice(0, 3)" :key="i" class="todo-item">
              <el-tag :type="t.priority === 'high' ? 'danger' : 'info'" size="small">
                {{ t.priority === 'high' ? '高' : '中' }}
              </el-tag>
              <span class="todo-title">{{ t.title }}</span>
            </div>
          </div>
          <el-empty v-else description="暂无待办事项" :image-size="50" />
        </section>
      </el-col>
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#7c3aed; margin-right:4px"><List /></el-icon>
              进行中专项
            </h3>
            <el-link type="primary" :underline="false" @click="$router.push('/tasks')">全部 →</el-link>
          </div>
          <div v-if="queueData.tasks && queueData.tasks.length" class="task-list">
            <div v-for="(t, i) in queueData.tasks.slice(0, 3)" :key="i" class="task-item">
              <el-tag type="primary" size="small">{{ t.status }}</el-tag>
              <span class="task-title">{{ t.title }}</span>
              <el-progress :percentage="t.progress || 0" :stroke-width="6" :show-text="false" style="width:80px" />
            </div>
          </div>
          <el-empty v-else description="暂无进行中专项" :image-size="50" />
        </section>
      </el-col>
    </el-row>

    <!-- 模块5: 退款挽回快照 -->
    <section v-if="hasRecovery" class="section-card mb-16">
      <div class="section-header-row">
        <h3 class="section-title">退款挽回今日快照</h3>
        <el-link type="primary" :underline="false" @click="$router.push('/dashboards/refund')">详细分析 →</el-link>
      </div>
      <el-row :gutter="12" class="mb-12">
        <el-col :xs="12" :md="4">
          <div class="recovery-kpi" style="background:#eff6ff">
            <div class="rk-label">今日接待SN</div>
            <div class="rk-value">{{ formatNum(recoveryData.kpi.total_sn) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :md="4">
          <div class="recovery-kpi" :style="successRateColor">
            <div class="rk-label">挽回成功率</div>
            <div class="rk-value">{{ Number(recoveryData.kpi.success_rate || 0).toFixed(1) }}%</div>
          </div>
        </el-col>
        <el-col :xs="12" :md="4">
          <div class="recovery-kpi" style="background:#fef2f2">
            <div class="rk-label">今日失败</div>
            <div class="rk-value">{{ formatNum(recoveryData.kpi.failed_sn) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :md="4">
          <!-- 饼图:退款原因分布 -->
          <div class="recovery-pie-cell">
            <BaseChart :option="recoveryReasonPie" height="110px" />
          </div>
        </el-col>
        <el-col :xs="24" :md="8">
          <!-- 分组SN折叠列表 -->
          <el-collapse v-if="recoverySnList.length" class="recovery-sn-collapse">
            <el-collapse-item
              v-for="g in recoverySnGroups"
              :key="g.key"
              :name="g.key"
              :title="`${g.label} (${g.count})`"
            >
              <el-table :data="g.items" size="small" border stripe max-height="220">
                <el-table-column prop="sn" label="设备SN" width="140" />
                <el-table-column prop="staff" label="处理人" width="100" />
                <el-table-column prop="group" label="分组" width="90">
                  <template #default="{ row }">
                    <el-tag size="small" effect="plain">{{ row.group || '—' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="reason" label="退款原因" min-width="120" show-overflow-tooltip />
                <el-table-column label="失败金额" width="100" align="right">
                  <template #default="{ row }">
                    <span v-if="row.result === 'failed' && row.amount > 0" style="color:#dc2626;font-weight:600">
                      ¥{{ row.amount.toFixed(2) }}
                    </span>
                    <span v-else class="dim">—</span>
                  </template>
                </el-table-column>
                <el-table-column prop="time" label="时间" width="100">
                  <template #default="{ row }">
                    <span class="mono-small">{{ row.time }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </el-collapse-item>
          </el-collapse>
          <el-empty v-else description="暂无SN明细" :image-size="60" />
        </el-col>
      </el-row>
    </section>

    <!-- 模块6: 人均效率Top5 (紧凑版) -->
    <section class="section-card">
      <div class="section-header-row">
        <h3 class="section-title">今日人均效率 Top5</h3>
        <el-link type="primary" :underline="false" @click="$router.push('/dashboards/operation')">完整排名 →</el-link>
      </div>
      <el-table :data="topStaffList" size="small" border stripe>
        <el-table-column label="#" width="50" align="center">
          <template #default="{ $index }">
            <el-tag v-if="$index < 3" size="small" :type="['warning','success','info'][$index]" effect="dark">
              {{ $index + 1 }}
            </el-tag>
            <span v-else>{{ $index + 1 }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="人员" width="100" />
        <el-table-column prop="department" label="部门" width="80">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.department || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reception_count" label="会话数(次)" width="100" align="right" />
        <el-table-column prop="reception_device_count" label="接待数(个)" width="100" align="right" />
        <el-table-column label="工作量" width="100" align="right">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="scoreTagType(row.work_score)">
              {{ formatNum(row.work_score) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Timer, Refresh, WarningFilled, Document, Bell, List, CircleCheckFilled } from '@element-plus/icons-vue'
import { today } from '@/api/analytics'
import BaseChart from '@/components/BaseChart.vue'
import dayjs from 'dayjs'

const loading = ref(false)
const lastUpdate = ref('')

// 数据
const kpiData = ref({})
const hourlyData = ref({})
const staffData = ref({})
const queueData = ref({})
const recoveryData = ref({})
const hasRecovery = computed(() => !!recoveryData.value?.kpi)

// 当前政策 (静态配置，后续可从接口获取)
const currentPolicies = ref([
  {
    name: '退款挽回专项行动',
    description: '针对近期退款率上升，启动退款挽回专项行动，目标挽回率提升至40%',
    status: '执行中',
    type: 'warning',
  },
  {
    name: '客户满意度提升计划',
    description: '加强售前咨询质量，确保响应时长<3分钟，解决率>95%',
    status: '进行中',
    type: 'success',
  },
])

// 格式化
function formatNum(v) {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'number') {
    if (v >= 10000) return (v / 10000).toFixed(1) + 'w'
    if (v >= 1000) return (v / 1000).toFixed(1) + 'k'
    return Math.round(v).toString()
  }
  return v.toString()
}

function formatShortTime(t) {
  if (!t) return '-'
  return dayjs(t).format('HH:mm')
}

function formatMoney(v) {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'number') return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return v
}

function scoreTagType(score) {
  if (score >= 500) return 'danger'
  if (score >= 300) return 'warning'
  if (score >= 100) return 'success'
  return 'info'
}

// 今日日期
const displayDate = computed(() => {
  const d = dayjs()
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.day()]
  return `${d.format('YYYY年MM月DD日')} ${wd}`
})

// 快捷时间范围
function getTodayRange() {
  const today = dayjs()
  return {
    start: today.startOf('day').format('YYYY-MM-DD'),
    end: today.endOf('day').format('YYYY-MM-DD'),
  }
}

// KPI卡片
const kpiCards = computed(() => {
  const d = kpiData.value
  return [
    {
      key: 'reception',
      label: '接待数',
      displayValue: formatNum(d.reception_device_count),
      icon: 'Service',
      colorClass: 'kpi-blue',
      yoy: d.reception_yoy_pct ? `${d.reception_yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(d.reception_yoy_pct)}%` : '',
      yoyClass: d.reception_yoy_pct >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'session',
      label: '会话数',
      displayValue: formatNum(d.session_count),
      icon: 'ChatDotRound',
      colorClass: 'kpi-green',
      yoy: d.session_yoy_pct ? `${d.session_yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(d.session_yoy_pct)}%` : '',
      yoyClass: d.session_yoy_pct >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'refund_count',
      label: '退款笔数',
      displayValue: formatNum(d.refund_count),
      icon: 'Warning',
      colorClass: 'kpi-red',
      yoy: d.refund_count_yoy_pct ? `${d.refund_count_yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(d.refund_count_yoy_pct)}%` : '',
      yoyClass: d.refund_count_yoy_pct >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'refund_amount',
      label: '退款金额',
      displayValue: '¥' + formatMoney(d.refund_amount),
      icon: 'Money',
      colorClass: 'kpi-orange',
      yoy: d.refund_amount_yoy_pct ? `${d.refund_amount_yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(d.refund_amount_yoy_pct)}%` : '',
      yoyClass: d.refund_amount_yoy_pct >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'complaint',
      label: '投诉数',
      displayValue: formatNum(d.complaint_count),
      icon: 'Bell',
      colorClass: 'kpi-purple',
      yoy: '',
      yoyClass: '',
    },
    {
      key: 'on_duty',
      label: '值班人员',
      displayValue: `${dutyCount}人`,
      icon: 'User',
      colorClass: 'kpi-teal',
      yoy: '',
      yoyClass: '',
    },
  ]
})

const dutyCount = computed(() => dutyLeaders.value.length)

// 值班组长 (今日有接待)
const dutyLeaders = computed(() => {
  const staff = staffData.value?.items || []
  // 简化处理：取工作量Top5作为值班组长
  return staff.slice(0, 5).map(s => ({
    name: s.name,
    group: s.group,
    department: s.department,
  }))
})

// 趋势图
const trendOption = computed(() => {
  const data = hourlyData.value
  const hours = data?.hours || []
  const reception = data?.reception || []
  const refund = data?.refund_amount || []
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['接待数', '退款金额'], bottom: 0 },
    grid: { left: 40, right: 60, bottom: 30, top: 10 },
    xAxis: { type: 'category', data: hours, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '接待', position: 'left' },
      { type: 'value', name: '金额(元)', position: 'right' },
    ],
    series: [
      { name: '接待数', type: 'bar', data: reception, itemStyle: { color: '#3b82f6' }, barMaxWidth: 20 },
      { name: '退款金额', type: 'line', yAxisIndex: 1, data: refund, smooth: true, itemStyle: { color: '#ea580c' } },
    ],
  }
})

// 人均效率Top5
const topStaffList = computed(() => {
  const items = staffData.value?.items || []
  return items.slice(0, 5)
})

// 退款挽回SN列表 (按结果分组)
const recoverySnList = computed(() => {
  const details = recoveryData.value?.sn_details || []
  return details.map(d => ({
    sn: d.sn,
    result: d.result,
    staff: d.staff,
    group: d.staff_group,
    reason: d.reason,
    time: d.time ? dayjs(d.time).format('HH:mm:ss') : '-',
    amount: d.amount,
  }))
})

// 按结果分组的SN统计
const recoverySnGroups = computed(() => {
  const list = recoverySnList.value
  const success = list.filter(d => d.result === 'success')
  const failed = list.filter(d => d.result === 'failed')
  return [
    { key: 'success', label: '成功SN', color: '#22c55e', count: success.length, items: success },
    { key: 'failed', label: '失败SN', color: '#ef4444', count: failed.length, items: failed },
  ]
})

// 退款原因分布 (饼图)
const recoveryReasonPie = computed(() => {
  const dist = recoveryData.value?.reason_dist || []
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, left: 'center', itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 11 } },
    series: [{
      name: '退款原因',
      type: 'pie',
      radius: ['35%', '60%'],
      center: ['50%', '45%'],
      avoidLabelOverlap: false,
      label: { show: false },
      labelLine: { show: false },
      data: dist.map(d => ({
        name: d.reason,
        value: d.order_cnt,
      })),
    }],
  }
})

// 成功率颜色
const successRateColor = computed(() => {
  const rate = Number(recoveryData.value?.kpi?.success_rate || 0)
  if (rate >= 38) return { background: '#f0fdf4' }
  if (rate >= 30) return { background: '#fefce8' }
  return { background: '#fef2f2' }
})

// 加载数据
async function loadAll() {
  loading.value = true
  try {
    const { start, end } = getTodayRange()
    const params = { start, end }
    
    const [kpiRes, hourlyRes, staffRes, queueRes, recoveryRes] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend({ ...params, trend_grain: 'hour' }),
      today.staff({ ...params, aggregate_by: 'person', topn: 30 }),
      today.queue(params),
      today.refundRecovery(params),
    ])
    
    if (kpiRes.data) kpiData.value = kpiRes.data
    if (hourlyRes.data) hourlyData.value = hourlyRes.data
    if (staffRes.data) staffData.value = staffRes.data
    if (queueRes.data) queueData.value = queueRes.data
    if (recoveryRes.data) recoveryData.value = recoveryRes.data
    
    lastUpdate.value = dayjs().format('HH:mm:ss')
  } catch (e) {
    console.error('加载今日数据失败', e)
  } finally {
    loading.value = false
  }
}

let autoRefreshTimer = null

onMounted(() => {
  loadAll()
  // 每60秒自动刷新
  autoRefreshTimer = setInterval(loadAll, 60000)
})

onBeforeUnmount(() => {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer)
})
</script>

<style scoped>
.today-dashboard {
  padding-bottom: 24px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 值班组长 */
.duty-leader-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
  border-radius: 8px;
  border: 1px solid #fde68a;
}
.duty-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: #92400e;
}
.duty-chips {
  flex: 1;
}
.duty-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.chip-name { font-weight: 500; }
.chip-sep { color: #d1d5db; }
.chip-group { color: #6b7280; font-size: 11px; }

/* KPI卡片 */
.mini-kpi-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  margin-bottom: 0;
}
.mini-kpi-icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
}
.kpi-blue .mini-kpi-icon { background: linear-gradient(135deg, #3b82f6, #2563eb); }
.kpi-green .mini-kpi-icon { background: linear-gradient(135deg, #10b981, #059669); }
.kpi-red .mini-kpi-icon { background: linear-gradient(135deg, #ef4444, #dc2626); }
.kpi-orange .mini-kpi-icon { background: linear-gradient(135deg, #f97316, #ea580c); }
.kpi-purple .mini-kpi-icon { background: linear-gradient(135deg, #8b5cf6, #7c3aed); }
.kpi-teal .mini-kpi-icon { background: linear-gradient(135deg, #14b8a6, #0d9488); }

.mini-kpi-info {
  flex: 1;
  min-width: 0;
}
.mini-kpi-label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 2px;
}
.mini-kpi-value {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
  color: #111827;
}
.mini-kpi-yoy {
  font-size: 11px;
  margin-top: 2px;
}
.yoy-up { color: #16a34a; }
.yoy-down { color: #dc2626; }

/* 区块样式 */
.section-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  border: 1px solid #f3f4f6;
}
.section-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.section-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #111827;
  display: flex;
  align-items: center;
}
.section-desc {
  font-size: 12px;
  color: #9ca3af;
}

/* 异常事件 */
.incident-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.incident-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #fef2f2;
  border-radius: 6px;
  border-left: 3px solid #dc2626;
}
.incident-title {
  flex: 1;
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.incident-time {
  font-size: 11px;
  color: #9ca3af;
  font-family: ui-monospace, monospace;
}

/* 政策列表 */
.policy-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.policy-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px;
  background: #eff6ff;
  border-radius: 6px;
}
.policy-icon {
  color: #2563eb;
  font-size: 18px;
  margin-top: 2px;
}
.policy-info {
  flex: 1;
}
.policy-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e40af;
  margin-bottom: 2px;
}
.policy-desc {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.4;
}

/* 待办/任务列表 */
.todo-list, .task-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.todo-item, .task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #f9fafb;
  border-radius: 6px;
}
.todo-title, .task-title {
  flex: 1;
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 退款挽回快照 */
.recovery-kpi {
  text-align: center;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}
.rk-label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.rk-value {
  font-size: 22px;
  font-weight: 700;
  color: #111827;
}
.recovery-pie-cell {
  height: 110px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 4px;
}
.recovery-sn-collapse {
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  padding: 4px 8px;
}
.recovery-sn-collapse :deep(.el-collapse-item__header) {
  font-weight: 600;
  font-size: 13px;
  padding: 0 8px;
  height: 32px;
  line-height: 32px;
  border-bottom: none;
}
.recovery-sn-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}
.recovery-sn-collapse :deep(.el-collapse-item__content) {
  padding: 4px 0 8px;
}
.mono-small {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
.dim { color: #9ca3af; }

/* 辅助 */
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.h-full { height: 100%; }

/* 窄屏自适应 */
@media (max-width: 768px) {
  .td-header {
    flex-wrap: wrap;
    gap: 8px;
  }
  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .page-title {
    font-size: 16px;
  }
  .duty-leader-bar {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .section-card {
    padding: 12px;
  }
  .section-header-row {
    flex-wrap: wrap;
    gap: 6px;
  }
  .mini-kpi-card {
    gap: 8px;
    padding: 10px;
  }
  .mini-kpi-icon {
    width: 32px;
    height: 32px;
  }
  .mini-kpi-value {
    font-size: 16px;
  }
  .rk-value {
    font-size: 18px;
  }
}
</style>
