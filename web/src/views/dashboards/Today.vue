<template>
  <div class="page-container today-dashboard">
    <!-- 顶部标题区 -->
    <div class="flex-between mb-12 td-header">
      <div class="page-title" style="margin: 0">
        {{ pageTitle }}
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag v-if="isToday" size="small" type="success" effect="plain" style="margin-left: 8px">实时数据</el-tag>
      </div>
      <div class="header-actions">
        <el-date-picker
          v-model="selectedDate"
          type="date"
          value-format="YYYY-MM-DD"
          format="YYYY-MM-DD"
          :clearable="false"
          :disabled-date="(d) => d > new Date()"
          size="small"
          style="width: 150px"
          @change="onDateChange"
        />
        <el-button size="small" @click="loadAll" :loading="loading">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-tag v-if="lastUpdate && isToday" size="small" effect="plain" type="info">
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
          class="duty-chip"
          @click="openPersonAudit(l.name)"
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
              <el-tag :type="incidentTagType(inc.status)" size="small" effect="plain">
                {{ incidentStatusLabel(inc.status) }}
              </el-tag>
              <span class="incident-title">{{ inc.title }}</span>
              <span class="incident-time">{{ formatShortTime(inc.occurred_at) }}</span>
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
              <el-tag type="primary" size="small">{{ taskStatusLabel(t.status) }}</el-tag>
              <span class="task-title">{{ t.title }}</span>
              <el-tag v-if="t.type" size="small" type="info" effect="plain">{{ t.type }}</el-tag>
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
        <el-col :xs="24" :md="8">
          <div class="recovery-kpi" style="background:#eff6ff">
            <div class="rk-label">今日接待SN</div>
            <div class="rk-value">{{ formatNum(recoveryData.kpi.total_sn) }}</div>
          </div>
        </el-col>
        <el-col :xs="24" :md="8">
          <div class="recovery-kpi" :style="successRateColor">
            <div class="rk-label">挽回成功率</div>
            <div class="rk-value">{{ Number(recoveryData.kpi.success_rate || 0).toFixed(1) }}%</div>
          </div>
        </el-col>
        <el-col :xs="24" :md="8">
          <div class="recovery-kpi" style="background:#fef2f2">
            <div class="rk-label">今日失败</div>
            <div class="rk-value">{{ formatNum(recoveryData.kpi.failed_sn) }}</div>
          </div>
        </el-col>
        <el-col :xs="24" :md="24">
          <!-- 饼图:退款原因分布 -->
          <div class="recovery-pie-cell" style="height:200px">
            <div class="pie-cell-title">退款原因分布</div>
            <BaseChart :option="recoveryReasonPie" height="168px" />
          </div>
        </el-col>
      </el-row>

      <!-- 折叠: 退款挽回 SN 明细 (默认收起) -->
      <el-collapse v-if="recoverySnList.length" v-model="recoveryCollapseActive" class="recovery-sn-collapse">
        <el-collapse-item name="sn">
          <template #title>
            <span class="collapse-title">退款挽回 SN 明细（{{ recoverySnList.length }} 条）</span>
          </template>
          <el-table :data="recoverySnList" size="small" border stripe max-height="320">
            <el-table-column label="设备SN" width="150">
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="openDeviceAudit(row.sn)">{{ row.sn }}</el-link>
              </template>
            </el-table-column>
            <el-table-column label="处理人" width="110">
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="openPersonAudit(row.staff)">{{ row.staff || '—' }}</el-link>
              </template>
            </el-table-column>
            <el-table-column label="结果" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="row.result === 'success' ? 'success' : 'danger'" size="small">
                  {{ row.result === 'success' ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="退款原因" min-width="140" show-overflow-tooltip />
            <el-table-column label="时间" width="100">
              <template #default="{ row }">{{ formatShortTime(row.time) }}</template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </section>

    <!-- 模块6: 投诉专项(含明细) -->
    <section class="section-card mb-16">
      <div class="section-header-row">
        <h3 class="section-title">
          <el-icon style="color:#7c3aed; margin-right:4px"><Bell /></el-icon>
          投诉专项
        </h3>
        <el-link type="primary" :underline="false" @click="$router.push('/dashboards/complaint-analysis')">投诉分析 →</el-link>
      </div>
      <el-row :gutter="12" class="mb-12">
        <el-col :xs="8" :md="8">
          <div class="recovery-kpi" style="background:#eff6ff">
            <div class="rk-label">内部投诉</div>
            <div class="rk-value">{{ formatNum(complaintSummaryData.internal?.total) }}</div>
          </div>
        </el-col>
        <el-col :xs="8" :md="8">
          <div class="recovery-kpi" style="background:#f5f3ff">
            <div class="rk-label">商户号投诉</div>
            <div class="rk-value">{{ formatNum(complaintSummaryData.merchant?.total) }}</div>
          </div>
        </el-col>
        <el-col :xs="8" :md="8">
          <div class="recovery-kpi" style="background:#fef2f2">
            <div class="rk-label">外部投诉</div>
            <div class="rk-value">{{ formatNum(complaintSummaryData.external?.total) }}</div>
          </div>
        </el-col>
      </el-row>
      <el-collapse v-model="complaintCollapseActive" class="complaint-collapse">
        <el-collapse-item name="detail">
          <template #title>
            <span class="collapse-title">投诉明细（{{ complaintList.length }} 条）</span>
          </template>
          <el-table :data="complaintList" size="small" border stripe max-height="360">
            <el-table-column label="来源" width="100">
              <template #default="{ row }">
                <el-tag :type="complaintSourceType(row.source)" size="small" effect="plain">{{ row.source }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="90">
              <template #default="{ row }">{{ formatShortTime(row.time) }}</template>
            </el-table-column>
            <el-table-column label="设备SN" width="140" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn || '—' }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="分类" width="130" show-overflow-tooltip />
            <el-table-column prop="problem" label="投诉内容" min-width="180" show-overflow-tooltip />
            <el-table-column label="处理/跟进" width="110" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler || '—' }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="结果/状态" width="110" show-overflow-tooltip />
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </section>

    <!-- 模块7: 人均效率 (全量, 默认仅企微) -->
    <section class="section-card">
      <div class="section-header-row">
        <h3 class="section-title">今日人均效率</h3>
        <div class="section-header-actions">
          <el-checkbox v-model="showAllStaff" size="small">显示所有人(含非企微)</el-checkbox>
          <el-link type="primary" :underline="false" @click="$router.push('/dashboards/operation')">运营看板 →</el-link>
        </div>
      </div>
      <el-table :data="topStaffList" size="small" border stripe max-height="420">
        <el-table-column label="#" width="50" align="center">
          <template #default="{ $index }">
            <el-tag v-if="$index < 3" size="small" :type="['warning','success','info'][$index]" effect="dark">
              {{ $index + 1 }}
            </el-tag>
            <span v-else>{{ $index + 1 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="人员" width="100">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="openPersonAudit(row.name, row.person_id)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
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
import { complaintSummary, complaintDetail } from '@/api/complaint'
import BaseChart from '@/components/BaseChart.vue'
import { useAudit } from '@/composables/useAudit'
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

// 投诉专项
const complaintSummaryData = ref({})
const complaintList = ref([])
const complaintCollapseActive = ref(['detail'])

// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
const showAllStaff = ref(false)

// 审计弹窗(点击 SN / 人员 唤起)
const { openDeviceAudit, openPersonAudit } = useAudit()

// 退款挽回 SN 明细(折叠, 默认收起)
const recoveryCollapseActive = ref([])

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
  if (typeof v === 'object') {
    // 防御:误把 {value,yoy_pct} 对象传进来
    if ('value' in v) return formatNum(v.value)
    return '-'
  }
  const n = Number(v)
  if (Number.isNaN(n)) return '-'
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return Math.round(n).toString()
}

function formatShortTime(t) {
  if (!t) return '-'
  return dayjs(t).format('HH:mm')
}

function formatMoney(v) {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'object') {
    if ('value' in v) return formatMoney(v.value)
    return '-'
  }
  const n = Number(v)
  if (Number.isNaN(n)) return '-'
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function scoreTagType(score) {
  if (score >= 500) return 'danger'
  if (score >= 300) return 'warning'
  if (score >= 100) return 'success'
  return 'info'
}

function complaintSourceType(source) {
  if (source === '内部投诉') return 'warning'
  if (source === '外部投诉') return 'danger'
  if (source === '商户号投诉') return 'primary'
  return 'info'
}

// 异常事件状态映射 (后端 Incident.status: registered/processing/calmed/reviewed)
const INCIDENT_STATUS = {
  registered: { label: '待确认', type: 'warning' },
  processing: { label: '处理中', type: 'danger' },
  calmed: { label: '已平息', type: 'info' },
  reviewed: { label: '已复盘', type: 'info' },
}
function incidentStatusLabel(s) {
  return INCIDENT_STATUS[s]?.label || s || '一般'
}
function incidentTagType(s) {
  return INCIDENT_STATUS[s]?.type || 'warning'
}

// 专项任务状态映射 (后端 SpecialTask.status)
const TASK_STATUS = {
  pending_start: '待启动',
  executing: '执行中',
  following: '跟进中',
  done: '已完成',
  archived: '已归档',
}
function taskStatusLabel(s) {
  return TASK_STATUS[s] || s || '—'
}

// 选中的看板日期 (默认今天, 支持切换到历史任意一天)
const selectedDate = ref(dayjs().format('YYYY-MM-DD'))
const isToday = computed(() => selectedDate.value === dayjs().format('YYYY-MM-DD'))

// 展示日期文案
const displayDate = computed(() => {
  const d = dayjs(selectedDate.value)
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.day()]
  return `${d.format('YYYY年MM月DD日')} ${wd}`
})

// 看板标题: 今天显示「今日看板」, 历史日期显示「看板」
const pageTitle = computed(() => (isToday.value ? '今日看板' : '看板'))

// 选中的日期对应的全天范围 (传给后端 start/end)
function getRange() {
  const d = dayjs(selectedDate.value)
  return {
    start: d.startOf('day').format('YYYY-MM-DD'),
    end: d.endOf('day').format('YYYY-MM-DD'),
  }
}

function onDateChange() {
  // 切换日期后重新加载全部数据
  loadAll()
}

// KPI卡片
const kpiCards = computed(() => {
  const d = kpiData.value
  // 后端 today_kpi 返回嵌套结构 {value, yoy_value, yoy_pct, ...}
  const reception = d.reception || {}
  const refundCnt = d.refund_count || {}
  const refundAmt = d.refund_amount || {}
  const complaint = d.complaint || {}
  const staffOnDuty = d.staff_on_duty || {}
  return [
    {
      key: 'reception',
      label: '接待数',
      displayValue: formatNum(reception.device_count),
      icon: 'Service',
      colorClass: 'kpi-blue',
      yoy: (reception.device_count_yoy_pct != null && reception.device_count_yoy_pct !== 0)
        ? `${reception.device_count_yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(reception.device_count_yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (reception.device_count_yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'session',
      label: '会话数',
      displayValue: formatNum(reception.value),
      icon: 'ChatDotRound',
      colorClass: 'kpi-green',
      yoy: (reception.yoy_pct != null && reception.yoy_pct !== 0)
        ? `${reception.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(reception.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (reception.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'refund_count',
      label: '退款笔数',
      displayValue: formatNum(refundCnt.value),
      icon: 'Warning',
      colorClass: 'kpi-red',
      yoy: (refundCnt.yoy_pct != null && refundCnt.yoy_pct !== 0)
        ? `${refundCnt.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(refundCnt.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (refundCnt.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'refund_amount',
      label: '退款金额',
      displayValue: '¥' + formatMoney(refundAmt.value),
      icon: 'Money',
      colorClass: 'kpi-orange',
      yoy: (refundAmt.yoy_pct != null && refundAmt.yoy_pct !== 0)
        ? `${refundAmt.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(refundAmt.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (refundAmt.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'complaint',
      label: '投诉数',
      displayValue: formatNum(complaint.value),
      icon: 'Bell',
      colorClass: 'kpi-purple',
      yoy: (complaint.yoy_pct != null && complaint.yoy_pct !== 0)
        ? `${complaint.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(complaint.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (complaint.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'on_duty',
      label: '值班人员',
      displayValue: `${formatNum(staffOnDuty.value)}人`,
      icon: 'User',
      colorClass: 'kpi-teal',
      yoy: '',
      yoyClass: '',
    },
  ]
})

// 值班组长/主管 (仅显示管理岗: 主管/组长/副主管)
const dutyLeaders = computed(() => {
  const staff = staffData.value?.data || []
  return staff
    .filter(s => s.is_management || ['主管', '组长', '副主管'].includes(s.position))
    .map(s => ({
      name: s.name,
      group: s.group,
      department: s.department,
    }))
})

// 趋势图
const trendOption = computed(() => {
  const data = hourlyData.value
  const labels = data?.hour_labels || data?.slot_labels || data?.hours || []
  const sessions = data?.reception || []           // 会话数(次)
  const devices = data?.reception_devices || []    // 接待数(个)
  const complaint = data?.complaint || []          // 投诉量
  const refundCnt = data?.refund_count || []       // 退款笔数
  const refundAmt = data?.refund_amount || []      // 退款金额
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['会话数', '接待数', '投诉量', '退款笔数', '退款金额'], bottom: 0 },
    grid: { left: 40, right: 60, bottom: 30, top: 10 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '数量', position: 'left' },
      { type: 'value', name: '金额(元)', position: 'right' },
    ],
    series: [
      { name: '会话数', type: 'bar', data: sessions, itemStyle: { color: '#3b82f6' }, barMaxWidth: 20 },
      { name: '接待数', type: 'bar', data: devices, itemStyle: { color: '#10b981' }, barMaxWidth: 20 },
      { name: '投诉量', type: 'bar', data: complaint, itemStyle: { color: '#f59e0b' }, barMaxWidth: 20 },
      { name: '退款笔数', type: 'bar', data: refundCnt, itemStyle: { color: '#ef4444' }, barMaxWidth: 20 },
      { name: '退款金额', type: 'line', yAxisIndex: 1, data: refundAmt, smooth: true, itemStyle: { color: '#ea580c' } },
    ],
  }
})

// 人均效率 (全量排序, 默认仅企微, 勾选后显示所有人)
const topStaffList = computed(() => {
  let items = staffData.value?.data || []
  // 排除虚拟账号(sys/系统自动)
  items = items.filter(s => !s.is_virtual)
  if (!showAllStaff.value) {
    items = items.filter(s => (s.qw_user_id || '').trim())
  }
  return items
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
    const { start, end } = getRange()
    const params = { start, end }
    
    const [kpiRes, hourlyRes, staffRes, queueRes, recoveryRes, complaintSummaryRes, complaintDetailRes] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend({ ...params, trend_grain: 'hour' }),
      today.staff({ ...params, aggregate_by: 'person', topn: 200 }),
      today.queue(params),
      today.refundRecovery(params),
      complaintSummary(params),
      complaintDetail({ ...params, limit: 500 }),
    ])
    
    if (kpiRes.data) kpiData.value = kpiRes.data
    if (hourlyRes.data) hourlyData.value = hourlyRes.data
    if (staffRes.data) staffData.value = staffRes.data
    if (queueRes.data) queueData.value = queueRes.data
    if (recoveryRes.data) recoveryData.value = recoveryRes.data
    if (complaintSummaryRes.data) complaintSummaryData.value = complaintSummaryRes.data
    if (complaintDetailRes.data) complaintList.value = complaintDetailRes.data?.items || []
    
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
  // 仅「今天」时每60秒自动刷新; 历史日期数据是静态的, 不需要轮询
  autoRefreshTimer = setInterval(() => {
    if (isToday.value) loadAll()
  }, 60000)
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
  cursor: pointer;
}
.chip-name { font-weight: 500; }
.chip-sep { color: #d1d5db; }
.chip-group { color: #6b7280; font-size: 11px; }

/* 退款挽回 SN 折叠明细 */
.recovery-sn-collapse {
  margin-top: 8px;
}
.recovery-sn-collapse :deep(.el-collapse-item__header) {
  height: 40px;
  padding: 0 8px;
}
.collapse-title {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}
.complaint-collapse {
  margin-top: 4px;
}

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
.section-header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
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
