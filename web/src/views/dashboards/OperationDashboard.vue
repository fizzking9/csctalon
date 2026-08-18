<template>
  <div class="page-container operation-dashboard">
    <!-- 顶部标题 + 时间粒度切换 -->
    <div class="flex-between mb-16 td-header" style="position: relative; z-index: 2">
      <div class="page-title" style="margin: 0; white-space: nowrap">
        运营看板
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag size="small" :type="grainTagType" effect="plain" style="margin-left: 8px">{{ grainLabel }}</el-tag>
      </div>
      <FilterBar
        v-model:modelValue="quickGrainOpt"
        v-model:range="customDateRange"
        :quick-options="grainQuickOptions"
        :show-custom="true"
        show-grain="false"
        :loading="loading"
        @refresh="loadAll"
      >
        <template #left>
          <el-radio-group v-model="grain" size="small" style="margin-right: 12px">
            <el-radio-button value="week">周</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
            <el-radio-button value="quarter">季度</el-radio-button>
            <el-radio-button value="year">年</el-radio-button>
          </el-radio-group>
          <el-select v-if="grain === 'week'" v-model="weekOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="w in weekOptions" :key="w.value" :label="w.label" :value="w.value" />
          </el-select>
          <el-select v-if="grain === 'month'" v-model="monthOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="m in monthOptions" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
          <el-select v-if="grain === 'quarter'" v-model="quarterOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="q in quarterOptions" :key="q.value" :label="q.label" :value="q.value" />
          </el-select>
          <el-select v-if="grain === 'year'" v-model="yearOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="y in yearOptions" :key="y.value" :label="y.label" :value="y.value" />
          </el-select>
          <el-select v-if="grain === 'month' || grain === 'quarter' || grain === 'year'" v-model="trendGrain" size="small" style="width: 120px; margin-right: 8px">
            <el-option label="按日聚合" value="day" />
            <el-option label="按周聚合" value="week" />
            <el-option label="按月聚合" value="month" />
          </el-select>
        </template>
      </FilterBar>
    </div>

    <!-- 模块1: 核心指标KPI -->
    <section class="section-card mb-20">
      <SectionHeader :title="'核心指标'" :sub="'对比' + compareName + '同时段 · ' + periodName" />
      <el-row :gutter="10">
        <el-col :xs="12" :sm="8" :md="4" v-for="c in kpiCards" :key="c.key">
          <KpiCard
            :label="c.label"
            :value="c.value"
            :unit="c.unit"
            :money="c.money"
            :icon="c.icon"
            :bg="c.bg"
            :color="c.color"
            :yoy-key="c.yoy_key || c.key"
            :yoy-pct="c.yoy_pct"
            :yoy-value="c.yoy_value"
            :yoy-label="compareName"
            :device-count="c.key === 'reception' ? c.device_count : null"
            :tip="c.tip"
            compact
          />
        </el-col>
      </el-row>
    </section>

    <!-- 模块2: 趋势分析 -->
    <section class="section-card mb-24">
      <SectionHeader title="趋势分析" :sub="`${periodName} · ${rhythmSub}`" />
      <ChartCard body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="360px" />
      </ChartCard>
    </section>

    <!-- 模块3: 接待与退款汇总 -->
    <el-row :gutter="12" class="mb-24">
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <SectionHeader title="接待分项汇总" :sub="periodName" />
          <ChartCard title="接待分类分布（累计）">
            <template #extra>
              <el-radio-group v-model="receptionPerspective" size="small">
                <el-radio-button value="count">接待量(次)</el-radio-button>
                <el-radio-button value="device">设备量(台)</el-radio-button>
              </el-radio-group>
            </template>
            <BaseChart :option="receptionPieOption" height="260px" />
          </ChartCard>
        </section>
      </el-col>
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <SectionHeader title="退款分项汇总" :sub="periodName" />
          <ChartCard title="退款原因Top10（累计）">
            <BaseChart :option="refundReasonOption" height="260px" />
          </ChartCard>
        </section>
      </el-col>
    </el-row>

    <!-- 模块4: 人均效率排名 -->
    <section class="section-card mb-24">
      <SectionHeader title="人均效率排名" sub="按工作量得分排序 · 支持部门/分组筛选">
        <template #extra>
          <el-space wrap size="small">
            <el-select v-model="filterDepartment" placeholder="全部部门" clearable size="small" style="width: 120px">
              <el-option v-for="d in departmentOptions" :key="d" :label="d" :value="d" />
            </el-select>
            <el-select v-model="filterGroupId" placeholder="全部分组" clearable size="small" style="width: 140px">
              <el-option v-for="g in groupsList" :key="g.id" :label="g.name" :value="g.name" />
            </el-select>
            <el-radio-group v-model="staffFilter.aggregateBy" size="small">
              <el-radio-button label="person">按人</el-radio-button>
              <el-radio-button label="group">按分组</el-radio-button>
              <el-radio-button label="all">全员</el-radio-button>
            </el-radio-group>
            <el-checkbox
              v-model="showAllStaff"
              size="small"
              :disabled="staffFilter.aggregateBy !== 'person'"
            >
              显示所有人(含非企微)
            </el-checkbox>
          </el-space>
        </template>
      </SectionHeader>
      <el-card shadow="never">
        <el-table :data="staffRows" border stripe size="small" max-height="500">
          <el-table-column label="排名" width="64" align="center">
            <template #default="{ $index }">
              <el-tag v-if="$index < 3 && staffFilter.aggregateBy !== 'all'" size="small" :type="['danger','warning','success'][$index]" effect="dark">
                Top{{ $index + 1 }}
              </el-tag>
              <span v-else class="text-muted">{{ $index + 1 }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="人员" width="140">
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openPersonAudit(row.name, row.person_id)">{{ row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column v-else-if="staffFilter.aggregateBy === 'group'" label="分组" width="140">
            <template #default="{ row }">
              <el-tag size="default" effect="dark" type="primary">{{ row.name || '未分组' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="部门" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.department === '未设置' || !row.department ? 'info' : 'success'">
                {{ row.department || '—' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reception_count" label="会话数(次)" width="110" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
          </el-table-column>
          <el-table-column prop="reception_device_count" label="接待数(个)" width="100" align="right" sortable>
            <template #default="{ row }">
              <span style="color:#409eff">{{ formatNum(row.reception_device_count ?? 0) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="工作量" width="90" align="right" sortable :sort-method="(a,b)=>Number(a.work_score||0)-Number(b.work_score||0)">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="scoreTagType(row.work_score,500,300,100)">
                <span style="font-weight:600">{{ formatNum(row.work_score) }}</span>
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="效率" width="90" align="right" sortable :sort-method="(a,b)=>Number(a.eff_score||0)-Number(b.eff_score||0)">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="scoreTagType(row.eff_score,350,200,80)">
                <span style="font-weight:600;color:#2563eb">{{ formatNum(row.eff_score) }}</span>
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </section>

    <!-- 模块5: 退款挽回专项分析 -->
    <section class="section-card mb-24" v-if="hasRecovery">
      <SectionHeader title="退款挽回专项分析" :sub="`${periodName} · 成功率统计`">
        <template #extra>
          <el-tag size="small" effect="dark" type="danger" style="margin-right:6px">{{ recoveryData.date }}~{{ recoveryData.date_end || recoveryData.date }}</el-tag>
          <el-button size="small" type="primary" plain @click="$router.push('/dashboards/refund')">
            查看退款看板详情 →
          </el-button>
        </template>
      </SectionHeader>

      <!-- KPI卡片行 -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="8">
          <div class="kpi-mini-card" style="background:#eff6ff">
            <div class="kpi-mini-label" style="color:#2563eb">总接待SN(分母)</div>
            <div class="kpi-mini-value" style="color:#2563eb">
              {{ formatNum(recoveryData.kpi.total_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="8">
          <div class="kpi-mini-card" :style="{background: Number(recoveryData.kpi.success_rate||0)>=38?'#f0fdf4':(Number(recoveryData.kpi.success_rate||0)>=30?'#fefce8':'#fef2f2')}">
            <div class="kpi-mini-label" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626')}">成功率</div>
            <div class="kpi-mini-value" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626')}">
              {{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="8">
          <div class="kpi-mini-card" style="background:#fef2f2">
            <div class="kpi-mini-label" style="color:#dc2626">挽留失败</div>
            <div class="kpi-mini-value" style="color:#dc2626">
              {{ formatNum(recoveryData.kpi.failed_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 人员挽回详情列表 -->
      <el-card shadow="never">
        <template #header>
          <div class="flex-between">
            <span style="font-weight:600">各人员挽回详情</span>
          </div>
        </template>
        <el-table :data="recoveryStaffList" size="small" border stripe>
          <el-table-column label="人员" width="140">
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openPersonAudit(row.name)">{{ row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="group" label="分组" width="120">
            <template #default="{ row }">{{ row.group || '—' }}</template>
          </el-table-column>
          <el-table-column prop="total_count" label="接待数" width="90" align="center" sortable />
          <el-table-column prop="success_count" label="成功数" width="90" align="center">
            <template #default="{ row }">
              <span style="color:#16a34a;font-weight:600">{{ row.success_count }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="failed_count" label="失败数" width="90" align="center">
            <template #default="{ row }">
              <span style="color:#dc2626;font-weight:600">{{ row.failed_count }}</span>
            </template>
          </el-table-column>
          <el-table-column label="成功率" width="100" align="center" sortable>
            <template #default="{ row }">
              <el-progress
                :percentage="Number((row.success_rate || 0) * 100).toFixed(1)"
                :color="row.success_rate >= 0.5 ? '#16a34a' : row.success_rate >= 0.3 ? '#ca8a04' : '#dc2626'"
                :stroke-width="8"
                style="width: 80px; display:inline-block"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { today } from '@/api/analytics'
import { listGroups } from '@/api/group'
import BaseChart from '@/components/BaseChart.vue'
import { useAudit } from '@/composables/useAudit'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import weekOfYear from 'dayjs/plugin/weekOfYear'
dayjs.extend(isoWeek)
dayjs.extend(quarterOfYear)
dayjs.extend(weekOfYear)

const loading = ref(false)

// ========== 时间选择 ==========
const grain = ref('week')
const weekOffset = ref(0)
const monthOffset = ref(0)
const quarterOffset = ref(0)
const yearOffset = ref(0)
const customDateRange = ref(null)
const trendGrain = ref('day')
const quickGrainOpt = ref('all')
const grainQuickOptions = [{ label: '全部', value: 'all' }]

// ========== 人员筛选 ==========
const filterDepartment = ref('')
const filterGroupId = ref(null)
const groupsList = ref([])

const staffFilter = reactive({
  aggregateBy: 'person',
})

// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
const showAllStaff = ref(false)

// 审计弹窗(点击人员唤起)
const { openPersonAudit } = useAudit()

// ========== 图表配置 ==========
const receptionPerspective = ref('count')

// ========== 数据存储 ==========
const kpiData = ref({})
const hourlyData = ref({})
const receptionData = ref({})
const refundData = ref({})
const staffData = ref({})
const recoveryData = ref({})
const hasRecovery = computed(() => recoveryData.value && recoveryData.value.kpi)

// ========== 选项 ==========
const weekOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const d = dayjs().subtract(i, 'week')
    const ws = d.startOf('week').add(1, 'day')
    const we = ws.add(6, 'day')
    const label = i === 0 ? '本周' : i === 1 ? '上周' : `${ws.format('MM-DD')}~${we.format('MM-DD')}`
    opts.push({ label, value: i })
  }
  return opts
})
const monthOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const d = dayjs().subtract(i, 'month')
    const label = i === 0 ? '本月' : i === 1 ? '上月' : d.format('YYYY年MM月')
    opts.push({ label, value: i })
  }
  return opts
})
const quarterOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 8; i++) {
    const d = dayjs().subtract(i, 'quarter')
    const label = i === 0 ? '本季度' : i === 1 ? '上季度' : `${d.year()}年Q${d.quarter()}`
    opts.push({ label, value: i })
  }
  return opts
})
const yearOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 3; i++) {
    const d = dayjs().subtract(i, 'year')
    const label = i === 0 ? '今年' : i === 1 ? '去年' : `${d.year()}年`
    opts.push({ label, value: i })
  }
  return opts
})

// ========== 标签 ==========
const grainLabel = computed(() => {
  const gMap = { week: '周视图', month: '月视图', quarter: '季度视图', year: '年视图' }
  const tMap = { day: '·按日', week: '·按周', month: '·按月' }
  return (gMap[grain.value] || '周视图') + (tMap[trendGrain.value] || '')
})
const grainTagType = computed(() => ({ week: 'primary', month: 'warning', quarter: 'danger', year: 'info' }[grain.value] || 'info'))

const periodName = computed(() => {
  if (customDateRange.value) return '自定义范围'
  switch (grain.value) {
    case 'week': {
      const i = weekOffset.value
      return i === 0 ? '本周' : i === 1 ? '上周' : `第${dayjs().subtract(i, 'week').isoWeek()}周`
    }
    case 'month': {
      const i = monthOffset.value
      return i === 0 ? '本月' : i === 1 ? '上月' : dayjs().subtract(i, 'month').format('YYYY年MM月')
    }
    case 'quarter': {
      const i = quarterOffset.value
      return i === 0 ? '本季度' : i === 1 ? '上季度' : `${dayjs().subtract(i, 'quarter').year()}年Q${dayjs().subtract(i, 'quarter').quarter()}`
    }
    case 'year': {
      const i = yearOffset.value
      return i === 0 ? '今年' : i === 1 ? '去年' : `${dayjs().subtract(i, 'year').year()}年`
    }
  }
  return ''
})
const compareName = computed(() => {
  if (customDateRange.value) return '对比期'
  switch (grain.value) {
    case 'week': {
      const i = weekOffset.value + 1
      return i === 1 ? '上周' : `前${i}周`
    }
    case 'month': {
      const i = monthOffset.value + 1
      return i === 1 ? '上月' : `前${i}月`
    }
    case 'quarter': {
      const i = quarterOffset.value + 1
      return i === 1 ? '上季度' : `前${i}季`
    }
    case 'year': {
      const i = yearOffset.value + 1
      return i === 1 ? '去年' : `前${i}年`
    }
  }
  return ''
})

const displayDate = computed(() => periodName.value)

const rhythmSub = computed(() => {
  if (grain.value === 'week') return '按日聚合'
  if (grain.value === 'month') return '按日聚合'
  return `按${trendGrain.value === 'day' ? '日' : trendGrain.value === 'week' ? '周' : '月'}聚合`
})

// ========== 日期范围计算 ==========
function getDateRange() {
  if (customDateRange.value) {
    return { start: customDateRange.value[0], end: customDateRange.value[1] }
  }
  const now = dayjs()
  let start, end
  switch (grain.value) {
    case 'week': {
      const offset = weekOffset.value
      const weekStart = now.subtract(offset, 'week').startOf('week').add(1, 'day')
      start = weekStart.format('YYYY-MM-DD')
      end = weekStart.add(6, 'day').format('YYYY-MM-DD')
      break
    }
    case 'month': {
      const offset = monthOffset.value
      const monthDate = now.subtract(offset, 'month')
      start = monthDate.startOf('month').format('YYYY-MM-DD')
      end = monthDate.endOf('month').format('YYYY-MM-DD')
      break
    }
    case 'quarter': {
      const offset = quarterOffset.value
      const quarterDate = now.subtract(offset, 'quarter')
      start = quarterDate.startOf('quarter').format('YYYY-MM-DD')
      end = quarterDate.endOf('quarter').format('YYYY-MM-DD')
      break
    }
    case 'year': {
      const offset = yearOffset.value
      const yearDate = now.subtract(offset, 'year')
      start = yearDate.startOf('year').format('YYYY-MM-DD')
      end = yearDate.endOf('year').format('YYYY-MM-DD')
      break
    }
  }
  return { start, end }
}

// ========== KPI卡 ==========
function _pctDelta(cur, prev) {
  if (prev == null || Number(prev) === 0) return null
  return Math.round(((Number(cur) - Number(prev)) / Number(prev)) * 1000) / 10
}
const kpiCards = computed(() => {
  const d = kpiData.value || {}
  const reception = d.reception || {}
  const refundCnt = d.refund_count || {}
  const refundAmt = d.refund_amount || {}
  const complaint = d.complaint || {}
  const recoveryRate = recoveryData.value?.kpi?.success_rate
  return [
    { key: 'reception', label: '接待数', value: reception.device_count, unit: '个', icon: 'Service', bg: '#eff6ff', color: '#2563eb', yoy_key: 'reception', yoy_pct: _pctDelta(reception.device_count, reception.device_count_yoy), yoy_value: reception.device_count_yoy, device_count: null, tip: '接待设备去重数' },
    { key: 'session', label: '会话数', value: reception.value, unit: '次', icon: 'ChatDotRound', bg: '#f0fdf4', color: '#16a34a', yoy_key: 'session', yoy_pct: reception.yoy_pct, yoy_value: reception.yoy_value, tip: '含客服接待+系统处理' },
    { key: 'refund_count', label: '退款笔数', value: refundCnt.value, unit: '笔', icon: 'Warning', bg: '#fef2f2', color: '#dc2626', yoy_key: 'refund_count', yoy_pct: refundCnt.yoy_pct, yoy_value: refundCnt.yoy_value, tip: '四路经营合计 A+B+C+D' },
    { key: 'refund_amount', label: '退款金额', value: refundAmt.value, money: true, icon: 'Money', bg: '#fff7ed', color: '#ea580c', yoy_key: 'refund_amount', yoy_pct: refundAmt.yoy_pct, yoy_value: refundAmt.yoy_value, tip: '四路经营合计退款金额' },
    { key: 'complaint', label: '投诉数', value: complaint.value, unit: '条', icon: 'Bell', bg: '#faf5ff', color: '#7c3aed', yoy_key: 'complaint', yoy_pct: complaint.yoy_pct, yoy_value: complaint.yoy_value, tip: '内部+外部投诉合计' },
    { key: 'recovery', label: '挽回率', value: Number(recoveryRate || 0), unit: '%', icon: 'SuccessFilled', bg: '#ecfdf5', color: '#059669', yoy_key: 'recovery', yoy_pct: null, yoy_value: null, tip: '退款挽回成功率' },
  ]
})

// ========== 图表配置 ==========
const hourlyOption = computed(() => {
  const data = hourlyData.value
  const labels = data?.slot_labels || data?.hour_labels || data?.hours || []
  const reception = data?.reception || []
  const refund = data?.refund_amount || []
  const complaint = data?.complaint || []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['接待', '退款金额', '投诉'], top: 0 },
    grid: { left: 50, right: 60, bottom: 30, top: 40 },
    xAxis: [{ type: 'category', data: labels, axisPointer: { type: 'shadow' } }],
    yAxis: [
      { type: 'value', name: '接待/投诉', position: 'left' },
      { type: 'value', name: '金额(元)', position: 'right', axisLine: { show: true, lineStyle: { color: '#ea580c' } } },
    ],
    series: [
      { name: '接待', type: 'bar', data: reception, itemStyle: { color: '#3b82f6' }, barMaxWidth: 30 },
      { name: '投诉', type: 'bar', data: complaint, itemStyle: { color: '#f59e0b' }, barMaxWidth: 20 },
      { name: '退款金额', type: 'line', yAxisIndex: 1, data: refund, smooth: true, itemStyle: { color: '#ea580c' }, areaStyle: { opacity: 0.1 } },
    ],
  }
})

const receptionPieOption = computed(() => {
  const data = receptionData.value
  const items = receptionPerspective.value === 'count'
    ? (data?.categories || [])
    : (data?.categories_device || [])
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 10, top: 'center' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      label: { show: false },
      data: items.map(d => ({ name: d.name, value: d.value || 0 })),
    }],
  }
})

const refundReasonOption = computed(() => {
  const data = refundData.value?.reasons || []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 120, right: 60, bottom: 30, top: 10 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: data.map(d => d.name).reverse() },
    series: [
      { name: '订单数', type: 'bar', data: data.map(d => d.count).reverse(), itemStyle: { color: '#3b82f6' }, barMaxWidth: 16 },
      { name: '金额(元)', type: 'bar', data: data.map(d => d.amount).reverse(), itemStyle: { color: '#ea580c' }, barMaxWidth: 16 },
    ],
  }
})

// ========== 人均效率表 ==========
const departmentOptions = computed(() => {
  const depts = new Set()
  ;(staffData.value?.data || []).forEach(r => {
    if (r.department && r.department !== '未分配') depts.add(r.department)
  })
  return Array.from(depts).sort()
})

const staffRows = computed(() => {
  let rows = staffData.value?.data || []
  // 按人视图下默认仅显示企微人员, 勾选后显示所有人
  if (staffFilter.aggregateBy === 'person' && !showAllStaff.value) {
    rows = rows.filter(r => (r.qw_user_id || '').trim())
  }
  if (filterDepartment.value) {
    rows = rows.filter(r => r.department === filterDepartment.value)
  }
  if (filterGroupId.value) {
    rows = rows.filter(r => r.group === filterGroupId.value)
  }
  return rows
})

// ========== 退款挽回 ==========
const recoveryStaffList = computed(() => {
  const details = recoveryData.value?.staff_details || recoveryData.value?.staff_detail || []
  return details.map(d => ({
    name: d.name,
    group: d.group,
    total_count: d.total_sn,
    success_count: d.success_sn,
    failed_count: d.failed_sn,
    success_rate: d.total_sn > 0 ? d.success_sn / d.total_sn : 0,
  }))
})

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

function scoreTagType(score, a, b, c) {
  if (score >= a) return 'success'
  if (score >= b) return 'warning'
  if (score >= c) return 'danger'
  return 'info'
}

// ========== 数据加载 ==========
async function loadAll() {
  loading.value = true
  const { start, end } = getDateRange()
  const params = { start, end, trend_grain: trendGrain.value }

  try {
    const [kpiRes, hourlyRes, receptionRes, refundRes, staffRes, recoveryRes] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend(params),
      today.reception(params),
      today.refund(params),
      today.staff({ ...params, aggregate_by: staffFilter.aggregateBy, topn: 200 }),
      today.refundRecovery(params),
    ])

    if (kpiRes.data) kpiData.value = kpiRes.data
    if (hourlyRes.data) hourlyData.value = hourlyRes.data
    if (receptionRes.data) receptionData.value = receptionRes.data
    if (refundRes.data) refundData.value = refundRes.data
    if (staffRes.data) staffData.value = staffRes.data
    if (recoveryRes.data) recoveryData.value = recoveryRes.data
  } catch (e) {
    console.error('加载数据失败', e)
  } finally {
    loading.value = false
  }
}

async function loadGroups() {
  try {
    const res = await listGroups()
    groupsList.value = res.data || []
  } catch {}
}

onMounted(async () => {
  await Promise.all([loadAll(), loadGroups()])
})

watch([grain, weekOffset, monthOffset, quarterOffset, yearOffset, customDateRange, trendGrain, filterDepartment, filterGroupId, staffFilter.aggregateBy], () => {
  loadAll()
})
</script>

<style scoped>
.operation-dashboard {
  padding-bottom: 24px;
}
.section-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb-16 { margin-bottom: 16px; }
.mb-20 { margin-bottom: 20px; }
.mb-24 { margin-bottom: 24px; }
.kpi-mini-card {
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}
.kpi-mini-label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.kpi-mini-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}
.kpi-mini-unit {
  font-size: 14px;
  font-weight: 400;
  color: #9ca3af;
  margin-left: 4px;
}
.text-muted { color: #9ca3af; }
</style>
