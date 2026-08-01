<template>
  <div class="page-container today-dashboard">
    <!-- 顶部标题 + 时间筛选 -->
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">
        今日看板
        <el-tag size="small" type="success" effect="plain" style="margin-left: 8px">单日切面</el-tag>
        <el-tag v-if="kpiData.merchant_tip" size="small" type="warning" effect="plain" style="margin-left: 6px">
          {{ kpiData.merchant_tip }}
        </el-tag>
      </div>
      <div class="filter-bar">
        <el-radio-group v-model="quickDate" size="small" @change="onQuickDate">
          <el-radio-button label="today">今日</el-radio-button>
          <el-radio-button label="yesterday">昨日</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-model="customDate"
          type="date"
          size="small"
          value-format="YYYY-MM-DD"
          placeholder="选择日期"
          style="width: 160px; margin-left: 8px"
          @change="onCustomDate"
        />
        <el-button :icon="Refresh" :loading="loading" @click="loadAll" style="margin-left: 8px">刷新</el-button>
      </div>
    </div>

    <!-- 模块1: KPI卡 -->
    <section class="section-card mb-24">
      <h2 class="section-title">1. 核心指标 <span class="section-sub">（对比昨日同时段）</span></h2>
      <el-row :gutter="16">
        <el-col :xs="12" :sm="8" :md="4" v-for="c in kpiCards" :key="c.key">
          <el-card shadow="hover" class="kpi-card" body-style="padding: 14px 18px">
            <div class="kpi-icon" :style="{ background: c.bg, color: c.color }">
              <el-icon :size="22"><component :is="c.icon" /></el-icon>
            </div>
            <div class="kpi-label">{{ c.label }}</div>
            <div class="kpi-value">
              <template v-if="c.money">{{ formatMoney(c.value) }}</template>
              <template v-else>{{ formatNum(c.value) }}<span class="kpi-unit">{{ c.unit || '' }}</span></template>
            </div>
            <div class="kpi-yoy">
              <el-tag v-if="c.yoy_pct != null" size="small" effect="plain" :type="yoyTagType(c.key, c.yoy_pct)">
                {{ c.yoy_pct > 0 ? '↑' : '↓' }} {{ Math.abs(c.yoy_pct) }}%
              </el-tag>
              <el-tag v-else size="small" type="info" effect="plain">昨日 0</el-tag>
              <span class="kpi-yoy-val">
                <template v-if="c.money">{{ formatMoney(c.yoy_value) }}</template>
                <template v-else>{{ formatNum(c.yoy_value) }}{{ c.unit || '' }}</template>
                 · 昨日
              </span>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </section>

    <!-- 模块2: 小时节奏 -->
    <section class="section-card mb-24">
      <h2 class="section-title">2. 今日实时节奏 <span class="section-sub">（小时级接待量柱 + 退款金额线）</span></h2>
      <el-card shadow="never" body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="320px" />
      </el-card>
    </section>

    <!-- 模块3: 接待分项 + 模块4: 退款分项 → 并排两栏 -->
    <el-row :gutter="16" class="mb-24">
      <el-col :xs="24" :lg="12">
        <section class="section-card h-full">
          <h2 class="section-title">3. 今日接待分项</h2>
          <el-row :gutter="12">
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">接待分类分布</div>
                <BaseChart :option="receptionPieOption" height="280px" />
              </el-card>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">接待量 Top 10 客服</div>
                <BaseChart :option="receptionStaffOption" height="280px" />
              </el-card>
            </el-col>
          </el-row>
        </section>
      </el-col>
      <el-col :xs="24" :lg="12">
        <section class="section-card h-full">
          <h2 class="section-title">4. 今日退款分项</h2>
          <el-row :gutter="12">
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">退款原因 Top 10（按金额）</div>
                <BaseChart :option="refundReasonOption" height="280px" />
              </el-card>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">退款方式分布</div>
                <BaseChart :option="refundMethodOption" height="280px" />
              </el-card>
            </el-col>
          </el-row>
        </section>
      </el-col>
    </el-row>

    <!-- 模块5: 处理队列 三栏 -->
    <section class="section-card mb-24">
      <h2 class="section-title">5. 今日处理队列</h2>
      <el-row :gutter="16">
        <el-col :xs="24" :md="8">
          <el-card shadow="hover" class="queue-card">
            <template #header>
              <div class="flex-between">
                <span class="queue-title">
                  <el-icon color="#e6a23c"><Bell /></el-icon>
                  今日截止待办
                </span>
                <el-badge :value="queueData.todos?.length || 0" :max="99" class="queue-badge" />
              </div>
            </template>
            <div class="queue-list">
              <div v-for="t in queueData.todos || []" :key="t.id" class="queue-item"
                   @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="priorityTagType(t.priority)" effect="dark" class="mr-8">
                  {{ priorityLabel(t.priority) }}
                </el-tag>
                <span class="queue-item-title">{{ t.title }}</span>
                <el-tag size="small" effect="plain" class="queue-status">{{ todoStatusLabel(t.status) }}</el-tag>
              </div>
              <el-empty v-if="!queueData.todos?.length" description="今日无待办截止" :image-size="60" />
            </div>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-card shadow="hover" class="queue-card">
            <template #header>
              <div class="flex-between">
                <span class="queue-title">
                  <el-icon color="#409eff"><List /></el-icon>
                  进行中专项
                </span>
                <el-badge :value="queueData.tasks?.length || 0" :max="99" class="queue-badge" />
              </div>
            </template>
            <div class="queue-list">
              <div v-for="t in queueData.tasks || []" :key="t.id" class="queue-item"
                   @click="$router.push(`/tasks/${t.id}`)">
                <el-tag size="small" :type="taskStatusTagType(t.status)" effect="dark" class="mr-8">
                  {{ taskStatusLabel(t.status) }}
                </el-tag>
                <span class="queue-item-title">{{ t.title }}</span>
                <span v-if="t.deadline" class="queue-deadline">{{ formatDeadline(t.deadline) }}</span>
              </div>
              <el-empty v-if="!queueData.tasks?.length" description="暂无进行中专项" :image-size="60" />
            </div>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-card shadow="hover" class="queue-card">
            <template #header>
              <div class="flex-between">
                <span class="queue-title">
                  <el-icon color="#f56c6c"><Warning /></el-icon>
                  今日异常事件
                </span>
                <el-badge :value="queueData.incidents?.length || 0" :max="99" class="queue-badge" />
              </div>
            </template>
            <div class="queue-list">
              <div v-for="inc in queueData.incidents || []" :key="inc.id" class="queue-item"
                   @click="$router.push('/management/incidents')">
                <el-tag size="small" :type="incidentStatusTagType(inc.status)" effect="dark" class="mr-8">
                  {{ incidentStatusLabel(inc.status) }}
                </el-tag>
                <span class="queue-item-title">{{ inc.title }}</span>
                <span class="queue-deadline">{{ formatDeadline(inc.occurred_at, 'time') }}</span>
              </div>
              <el-empty v-if="!queueData.incidents?.length" description="今日无异常事件 ✅" :image-size="60" />
            </div>
          </el-card>
        </el-col>
      </el-row>
    </section>

    <!-- 模块6: 人均效率表 -->
    <section class="section-card mb-24">
      <h2 class="section-title">6. 今日人均效率</h2>
      <el-card shadow="never">
        <el-table :data="staffRows" border stripe size="small" max-height="520">
          <el-table-column label="排名" width="64" align="center" fixed>
            <template #default="{ $index }">
              <el-tag v-if="$index < 3" size="small" :type="['danger','warning','success'][$index]" effect="dark">
                Top{{ $index + 1 }}
              </el-tag>
              <span v-else class="text-muted">{{ $index + 1 }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="人员" width="110" />
          <el-table-column prop="department" label="部门" width="130">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.department === '未设置' ? 'info' : 'success'">{{ row.department }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="group" label="组别" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.group === '未分组' ? 'info' : 'primary'">{{ row.group }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reception_count" label="接待量" width="100" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
          </el-table-column>
          <el-table-column prop="refund_op_count" label="退款操作笔数" width="120" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.refund_op_count) }}</template>
          </el-table-column>
          <el-table-column prop="refund_op_amount" label="退款操作金额" width="140" align="right" sortable>
            <template #default="{ row }">{{ formatMoney(row.refund_op_amount) }}</template>
          </el-table-column>
          <el-table-column prop="ticket_done_count" label="已完工单" width="110" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.ticket_done_count) }}</template>
          </el-table-column>
          <el-table-column prop="work_score" label="工作量合成" width="120" align="right" sortable>
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="workScoreTagType(row.work_score)">
                {{ formatNum(row.work_score) }} 分
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!staffRows.length" description="今日暂无人员工作数据" />
      </el-card>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { today } from '@/api/analytics'
import BaseChart from '@/components/BaseChart.vue'
import { formatMoney } from '@/utils/format'
import dayjs from 'dayjs'
import {
  Refresh, ChatDotRound, Document, Money, Wallet, Checked, UserFilled,
  Bell, List, Warning
} from '@element-plus/icons-vue'

const loading = ref(false)
const quickDate = ref('today')  // today / yesterday
const customDate = ref('')      // YYYY-MM-DD, 空表示用quickDate

// 当前实际传参date: 空=今天, 'YYYY-MM-DD'=指定日期
const currentDate = computed(() => {
  if (customDate.value) return customDate.value
  if (quickDate.value === 'yesterday') return dayjs().subtract(1, 'day').format('YYYY-MM-DD')
  return ''  // 今天不传参数(实时窗口)
})

// ---------- 模块1 KPI ----------
const kpiData = ref({})
const kpiCards = computed(() => {
  const k = kpiData.value || {}
  const def = [
    { key: 'reception',      label: '今日接待量',   icon: 'ChatDotRound', bg: '#ecf5ff', color: '#409eff', unit: '次',
      money: false, ...(k.reception || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'refund_count',   label: '今日退款笔数', icon: 'Document',    bg: '#fdf6ec', color: '#e6a23c', unit: '笔',
      money: false, ...(k.refund_count || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'refund_amount',  label: '今日退款金额', icon: 'Money',       bg: '#fef0f0', color: '#f56c6c', unit: '',
      money: true,  ...(k.refund_amount || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'cash_out',       label: '真实现金流出', icon: 'Wallet',      bg: '#fdf6ec', color: '#e6a23c', unit: '',
      money: true,  ...(k.cash_out || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'ticket_done',    label: '今日已完工单', icon: 'Checked',     bg: '#f0f9eb', color: '#67c23a', unit: '单',
      money: false, ...(k.ticket_done || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'staff_on_duty',  label: '今日在岗人数', icon: 'UserFilled',  bg: '#f4f4f5', color: '#909399', unit: '人',
      money: false, ...(k.staff_on_duty || { value: 0, yoy_value: 0, yoy_pct: null }) },
  ]
  return def
})

// KPI 环比 tag 颜色: 接待量/完工单/在岗/退款笔数(这些越大越好↑绿↓红); 退款金额/现金流出(越小越好↑红↓绿)
const BAD_UP_KEYS = new Set(['refund_amount', 'cash_out'])
function yoyTagType(key, pct) {
  if (pct == null) return 'info'
  const good = BAD_UP_KEYS.has(key) ? pct < 0 : pct > 0
  if (pct === 0) return 'info'
  return good ? 'success' : 'danger'
}

// ---------- 模块2 小时节奏 ----------
const hourlyData = ref({ hour_labels: [], reception: [], refund_amount: [] })
const hourlyOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['接待量', '退款金额'], top: 0 },
  grid: { left: 56, right: 72, bottom: 36, top: 40 },
  xAxis: { type: 'category', data: hourlyData.value.hour_labels || [], axisLabel: { fontSize: 10, interval: 1 } },
  yAxis: [
    { type: 'value', name: '接待量(次)', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: 'dashed' } } },
    { type: 'value', name: '退款金额(元)', axisLabel: { formatter: (v) => (v >= 10000 ? (v/10000).toFixed(0)+'万' : v), fontSize: 10 } },
  ],
  series: [
    {
      name: '接待量', type: 'bar', data: hourlyData.value.reception || [],
      itemStyle: { color: '#409eff', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 22,
    },
    {
      name: '退款金额', type: 'line', yAxisIndex: 1, smooth: true,
      data: hourlyData.value.refund_amount || [],
      itemStyle: { color: '#f56c6c' },
      areaStyle: { color: 'rgba(245,108,108,0.12)' },
      symbol: 'circle', symbolSize: 6,
    },
  ],
}))

// ---------- 模块3 接待分项 ----------
const receptionData = ref({ categories: [], top_staff: [] })
const receptionPieOption = computed(() => {
  const data = (receptionData.value.categories || []).filter(d => d.value > 0)
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    color: ['#f56c6c', '#e6a23c', '#409eff', '#67c23a', '#909399', '#9b59b6'],
    series: [{
      name: '接待分类', type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: '{b}\n{d}%', fontSize: 10 },
      data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '暂无数据' } }],
    }],
  }
})
const receptionStaffOption = computed(() => {
  const list = (receptionData.value.top_staff || []).slice(0, 10)
  const dept = (d) => d.department && d.department !== '未设置' ? `(${d.department})` : ''
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const i = list.length - 1 - p[0].dataIndex
        const d = list[i]
        const grp = d.group && d.group !== '未分组' ? `组别: ${d.group}` : ''
        return `${d.name}<br/>部门: ${d.department || '未设置'}${grp ? '<br/>' + grp : ''}<br/>接待量: ${formatNum(d.count)}`
      },
    },
    grid: { left: 150, right: 60, bottom: 20, top: 10 },
    xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: list.map(d => `${d.name}${dept(d)}`).reverse(),
      axisLabel: { fontSize: 10, width: 140, overflow: 'truncate' },
    },
    series: [{
      type: 'bar',
      data: list.map(d => d.count).reverse(),
      itemStyle: { color: '#67c23a', borderRadius: [0, 3, 3, 0] },
      barMaxWidth: 14,
      label: { show: true, position: 'right', fontSize: 10, formatter: (p) => formatNum(p.value) },
    }],
  }
})

// ---------- 模块4 退款分项 ----------
const refundData = ref({ reasons: [], methods: [] })
const refundReasonOption = computed(() => {
  const list = (refundData.value.reasons || []).slice(0, 10).reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const item = p[0]
        return `${item.name}<br/>金额: ${formatMoney(item.data)}`
      }
    },
    grid: { left: 90, right: 48, bottom: 20, top: 10 },
    xAxis: { type: 'value', axisLabel: { formatter: (v) => v >= 10000 ? (v/10000).toFixed(0)+'万' : v, fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: list.map(d => d.name.length > 10 ? d.name.slice(0,10)+'…' : d.name),
      axisLabel: { fontSize: 10, width: 80, overflow: 'truncate' },
    },
    series: [{
      type: 'bar',
      data: list.map(d => d.amount),
      itemStyle: { color: '#f56c6c', borderRadius: [0, 3, 3, 0] },
      barMaxWidth: 14,
      label: { show: true, position: 'right', fontSize: 10, formatter: (p) => formatMoney(p.value) },
    }],
  }
})
const refundMethodOption = computed(() => {
  const data = (refundData.value.methods || []).filter(d => d.value > 0)
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 笔 ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    color: ['#409eff', '#67c23a', '#e6a23c', '#909399', '#f56c6c'],
    series: [{
      name: '退款方式', type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'],
      label: { show: true, formatter: '{b}\n{c}笔', fontSize: 10 },
      data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '暂无数据' } }],
    }],
  }
})

// ---------- 模块5 处理队列 ----------
const queueData = ref({ todos: [], tasks: [], incidents: [] })
function priorityTagType(p) {
  return { urgent: 'danger', high: 'warning', medium: 'primary', low: 'info' }[p] || 'info'
}
function priorityLabel(p) {
  return { urgent: '紧急', high: '高', medium: '中', low: '低' }[p] || '中'
}
function todoStatusLabel(s) {
  return { pending: '待办', processing: '处理中', confirming: '待确认', done: '已完成' }[s] || s
}
function taskStatusTagType(s) {
  return { pending_start: 'info', executing: 'warning', following: 'primary', done: 'success', archived: 'info' }[s] || 'info'
}
function taskStatusLabel(s) {
  return { pending_start: '待启动', executing: '执行中', following: '跟进中', done: '已完成', archived: '已归档' }[s] || s
}
function incidentStatusTagType(s) {
  return { registered: 'danger', processing: 'warning', calmed: 'primary', reviewed: 'success' }[s] || 'info'
}
function incidentStatusLabel(s) {
  return { registered: '登记', processing: '处理中', calmed: '已平息', reviewed: '已复盘' }[s] || s
}
function formatDeadline(d, type='date') {
  if (!d) return ''
  if (type === 'time') return dayjs(d).format('MM-DD HH:mm')
  return dayjs(d).format('MM-DD')
}

// ---------- 模块6 人均效率表 ----------
const staffRows = ref([])
function workScoreTagType(score) {
  if (score >= 100) return 'danger'
  if (score >= 50)  return 'warning'
  if (score >= 20)  return 'success'
  return 'info'
}

// ---------- 通用格式化 ----------
function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

// ---------- 日期切换 ----------
function onQuickDate() {
  customDate.value = ''
  loadAll()
}
function onCustomDate(val) {
  if (!val) {
    quickDate.value = 'today'
  } else {
    const y = dayjs().format('YYYY-MM-DD')
    const yst = dayjs().subtract(1, 'day').format('YYYY-MM-DD')
    if (val === y) quickDate.value = 'today'
    else if (val === yst) quickDate.value = 'yesterday'
  }
  loadAll()
}

// ---------- 数据加载 ----------
async function loadAll() {
  loading.value = true
  const params = currentDate.value ? { date: currentDate.value } : {}
  try {
    const [k, h, r, rf, q, s] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend(params),
      today.reception(params),
      today.refund(params),
      today.queue(params),
      today.staff(params),
    ])
    kpiData.value = k.data || {}
    hourlyData.value = h.data || { hour_labels: [], reception: [], refund_amount: [] }
    receptionData.value = r.data || { categories: [], top_staff: [] }
    refundData.value = rf.data || { reasons: [], methods: [] }
    queueData.value = q.data || { todos: [], tasks: [], incidents: [] }
    staffRows.value = s.data || []
  } catch (e) {
    console.error('[Today] load error', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.today-dashboard { padding-bottom: 24px; }
.filter-bar { display: flex; align-items: center; }
.section-card { }
.section-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 14px;
  padding-left: 10px;
  border-left: 3px solid #409eff;
}
.section-sub { font-size: 12px; color: #909399; font-weight: 400; margin-left: 6px; }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.h-full { height: 100%; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.text-muted { color: #909399; }
.mr-8 { margin-right: 8px; }

/* KPI卡 */
.kpi-card { position: relative; transition: transform 0.15s; }
.kpi-card:hover { transform: translateY(-2px); }
.kpi-icon {
  width: 42px; height: 42px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 10px;
}
.kpi-label { color: #909399; font-size: 13px; }
.kpi-value { font-size: 24px; font-weight: 600; color: #303133; margin-top: 4px; line-height: 1.2; }
.kpi-unit { font-size: 13px; font-weight: 400; color: #909399; margin-left: 4px; }
.kpi-yoy { margin-top: 8px; display: flex; align-items: center; gap: 6px; font-size: 12px; color: #909399; }
.kpi-yoy-val { color: #c0c4cc; font-size: 12px; }

.chart-title { font-size: 13px; color: #606266; font-weight: 500; padding: 4px 8px; }

/* 处理队列 */
.queue-card { height: 100%; }
.queue-title { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 500; color: #303133; }
.queue-badge { }
.queue-list { max-height: 320px; overflow-y: auto; }
.queue-item {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 8px; border-bottom: 1px solid #f5f7fa;
  cursor: pointer; transition: background 0.15s;
}
.queue-item:hover { background: #f5f7fa; }
.queue-item:last-child { border-bottom: none; }
.queue-item-title {
  flex: 1;
  font-size: 13px; color: #303133;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.queue-status { margin-left: auto; font-size: 11px; }
.queue-deadline { font-size: 12px; color: #909399; }
</style>
