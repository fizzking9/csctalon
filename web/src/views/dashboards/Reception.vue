<template>
  <div class="page-container reception-dashboard">
    <!-- 顶部标题 + 时间筛选 -->
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">接待分析 <el-tag size="small" type="warning" effect="plain" style="margin-left: 8px">对齐周报</el-tag></div>
      <FilterBar
        v-model:model-value="rangeMode"
        v-model:range="customRange"
        :quick-options="receptionQuickOptions"
        date-picker-type="daterange"
        :show-custom="true"
        show-grain="false"
        :loading="loading"
        @change="onRangeChange"
        @range-change="onRangeChange"
        @refresh="loadAll"
      />
    </div>

    <!-- 模块1:总体接待量 -->
    <section class="section-card mb-24">
      <SectionHeader title="1. 总体接待量" />
      <!-- 汇总卡片:最新周 -->
      <el-row :gutter="16" class="mb-16">
        <el-col :xs="12" :sm="6" :md="6">
          <KpiCard
            label="本周接待量"
            :value="latestWeek.reception_total"
            unit="次"
            icon="ChatDotRound"
            bg="#ecf5ff"
            color="#409eff"
            :yoy-pct="weekDiffPct(latestWeek.reception_total, prevWeek.reception_total)"
            :yoy-value="prevWeek.reception_total"
            yoy-key="reception_total"
            tip="客服助手沟通事项次数,1条接待记录=1次会话.环比=本周(最新有数据周)vs上周"
          >
            <template #extra>
              <div class="kpi-sub-local">较上周 <span :class="weekDiffColor(latestWeek.reception_total, prevWeek.reception_total)">{{ weekDiffText(latestWeek.reception_total, prevWeek.reception_total) }}</span></div>
            </template>
          </KpiCard>
        </el-col>
        <el-col :xs="12" :sm="6" :md="6">
          <KpiCard
            label="日均接待量"
            :value="latestWeek.daily_avg"
            unit="次/天"
            icon="DataAnalysis"
            bg="#f0f9eb"
            color="#67c23a"
            tip="本周有数据天数内的日均接待量"
          >
            <template #extra>
              <div class="kpi-sub-local">本周有数据 {{ latestWeek.day_count || 0 }} 天</div>
            </template>
          </KpiCard>
        </el-col>
        <el-col :xs="12" :sm="6" :md="6">
          <KpiCard
            label="客服人数"
            :value="latestWeek.staff_count"
            unit="人"
            icon="UserFilled"
            bg="#fdf6ec"
            color="#e6a23c"
            tip="本周实际在岗接待的员工人数"
          >
            <template #extra><div class="kpi-sub-local">本周实际在岗接待</div></template>
          </KpiCard>
        </el-col>
        <el-col :xs="12" :sm="6" :md="6">
          <KpiCard
            label="人均接待(天)"
            :value="latestWeek.per_capita_daily"
            unit="次"
            icon="Histogram"
            bg="#f4f4f5"
            color="#909399"
            tip="按在岗天折算的人均日接待量 (接待总量 / (客服人数 × 有数据天数))"
          >
            <template #extra><div class="kpi-sub-local">本周人均(按在岗天)</div></template>
          </KpiCard>
        </el-col>
      </el-row>

      <!-- 总体接待量图:柱接待量 + 双Y人数和人均 -->
      <ChartCard title="总体接待量趋势" body-style="padding: 8px 10px" class="mb-16">
        <BaseChart :option="overviewTrendOption" height="300px" />
      </ChartCard>

      <!-- 第二张:客服人数及人均接待 -->
      <ChartCard title="客服人数及人均接待" body-style="padding: 8px 10px" class="mb-16">
        <BaseChart :option="staffPerCapitaOption" height="300px" />
      </ChartCard>

      <!-- 总体接待量表 -->
      <el-card shadow="never">
        <el-table :data="overviewRows" border stripe size="small">
          <el-table-column label="周数" width="80" align="center">
            <template #default="{ row }">
              <span class="week-cell">
                {{ row.week_no }}周
                <el-tooltip :content="row.week_start" placement="top">
                  <el-icon class="week-cal" :size="12" color="#909399"><Calendar /></el-icon>
                </el-tooltip>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="接待量" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row.reception_total) }}</template>
          </el-table-column>
          <el-table-column label="日均" width="120" align="right">
            <template #default="{ row }">{{ fmtNum(row.daily_avg) }}</template>
          </el-table-column>
          <el-table-column label="客服数" width="90" align="center">
            <template #default="{ row }">{{ row.staff_count }}</template>
          </el-table-column>
          <el-table-column label="人均(天)" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row.per_capita_daily) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="260" show-overflow-tooltip />
        </el-table>
      </el-card>
    </section>

    <!-- 模块2:接待分项汇总 -->
    <section class="section-card mb-24">
      <SectionHeader title="2. 接待分项汇总" />
      <el-card class="mb-16" shadow="never">
        <el-table :data="categoryRows" border stripe size="small">
          <el-table-column label="周数" width="80" align="center">
            <template #default="{ row }">{{ row.week_no }}周</template>
          </el-table-column>
          <el-table-column label="投诉" width="100" align="right">
            <template #default="{ row }"><span :class="highlightCell(row['投诉'], 'complaint')">{{ fmtNum(row['投诉']) }}</span></template>
          </el-table-column>
          <el-table-column label="退款" width="100" align="right">
            <template #default="{ row }">{{ fmtNum(row['退款']) }}</template>
          </el-table-column>
          <el-table-column label="其他问题" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row['其他问题']) }}</template>
          </el-table-column>
          <el-table-column label="套餐问题" width="110" align="right">
            <template #default="{ row }"><span :class="highlightCell(row['套餐问题'], 'package')">{{ fmtNum(row['套餐问题']) }}</span></template>
          </el-table-column>
          <el-table-column label="咨询" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row['咨询']) }}</template>
          </el-table-column>
          <el-table-column label="综合问题" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row['综合问题']) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注说明" min-width="300" show-overflow-tooltip />
        </el-table>
      </el-card>

      <el-row :gutter="16">
        <el-col :xs="24" :md="12">
          <ChartCard title="投诉趋势 + 线性拟合" body-style="padding: 8px 10px" class="mb-16">
            <BaseChart :option="complaintTrendOption" height="300px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="12">
          <ChartCard title="退款趋势" body-style="padding: 8px 10px" class="mb-16">
            <BaseChart :option="refundTrendOption" height="300px" />
          </ChartCard>
        </el-col>
      </el-row>
      <ChartCard title="其他问题汇总（套餐/咨询/其他/综合 堆叠面积）" body-style="padding: 8px 10px" class="mb-16">
        <BaseChart :option="othersStackOption" height="300px" />
      </ChartCard>
    </section>

    <!-- 模块3:接待分项人均工作 -->
    <section class="section-card mb-24">
      <SectionHeader title="3. 接待分项人均工作" />
      <el-card class="mb-16" shadow="never">
        <el-table :data="perCapitaRows" border stripe size="small">
          <el-table-column label="周数" width="80" align="center">
            <template #default="{ row }">{{ row.week_no }}周</template>
          </el-table-column>
          <el-table-column label="投诉" width="100" align="right">
            <template #default="{ row }"><span :class="highlightCell(row.complaint_pc, 'complaint')">{{ fmtNum(row.complaint_pc) }}</span></template>
          </el-table-column>
          <el-table-column label="投诉组" width="90" align="center">
            <template #default="{ row }">{{ row.complaint_staff }}人</template>
          </el-table-column>
          <el-table-column label="退款" width="100" align="right">
            <template #default="{ row }">{{ fmtNum(row.refund_pc) }}</template>
          </el-table-column>
          <el-table-column label="退款组" width="90" align="center">
            <template #default="{ row }">{{ row.refund_staff }}人</template>
          </el-table-column>
          <el-table-column label="套餐问题" width="110" align="right">
            <template #default="{ row }"><span :class="highlightCell(row.package_pc, 'package')">{{ fmtNum(row.package_pc) }}</span></template>
          </el-table-column>
          <el-table-column label="套餐组" width="90" align="center">
            <template #default="{ row }">{{ row.package_staff }}人</template>
          </el-table-column>
          <el-table-column label="咨询" width="110" align="right">
            <template #default="{ row }">{{ fmtNum(row.consult_pc) }}</template>
          </el-table-column>
          <el-table-column label="咨询组" width="90" align="center">
            <template #default="{ row }">{{ row.consult_staff }}人</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="260" show-overflow-tooltip />
        </el-table>
      </el-card>

      <el-row :gutter="16">
        <el-col :xs="24" :md="12">
          <ChartCard title="投诉组人数及人均接待" body-style="padding: 8px 10px" class="mb-16">
            <BaseChart :option="pcComplaintOption" height="300px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="12">
          <ChartCard title="退款组人数及人均接待" body-style="padding: 8px 10px" class="mb-16">
            <BaseChart :option="pcRefundOption" height="300px" />
          </ChartCard>
        </el-col>
      </el-row>
      <ChartCard title="其他组人数及人均接待(套餐组 + 咨询组)" body-style="padding: 8px 10px" class="mb-16">
        <BaseChart :option="pcOthersOption" height="320px" />
      </ChartCard>
    </section>

    <!-- 模块4:各组接待 -->
    <section class="section-card mb-24">
      <SectionHeader
        title="4. 各组接待"
        :sub="`所选周期 ${dateRangeText} 的人员日接待明细`"
      />
      <div v-if="groupDetail.groups && groupDetail.groups.length">
        <el-card
          v-for="g in groupDetail.groups"
          :key="g.group_name"
          class="group-card mb-16"
          shadow="never"
        >
          <template #header>
            <div class="flex-between" style="margin: -6px 0">
              <div class="group-header">
                <b>{{ g.group_name }}</b>
                <el-tag size="small" type="info" style="margin-left: 10px">组总 {{ fmtNum(g.group_total) }}</el-tag>
                <el-tag size="small" effect="plain" style="margin-left: 6px">组均 {{ fmtNum(g.group_avg) }}</el-tag>
              </div>
              <span class="text-muted">{{ g.people.length }} 人</span>
            </div>
          </template>
          <el-table :data="g.people" border stripe size="small" max-height="520">
            <el-table-column label="姓名" width="110" fixed="left">
              <template #default="{ row }"><b>{{ row.name }}</b></template>
            </el-table-column>
            <el-table-column v-for="(d, i) in groupDetail.date_labels" :key="'d'+i" :label="d" width="90" align="right">
              <template #default="{ row }">{{ fmtNum(row.daily_list[i]) }}</template>
            </el-table-column>
            <el-table-column label="平均接待量" width="110" align="right" fixed="right">
              <template #default="{ row }">
                <b>{{ fmtNum(row.avg) }}</b>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
      <el-alert
        v-if="groupDetail.ungrouped && groupDetail.ungrouped.length"
        :title="'未分组人员(' + groupDetail.ungrouped.length + '人,需在人员管理中配置分组)'"
        type="warning"
        :closable="false"
        class="mb-16"
        show-icon
      />
      <el-card
        v-if="groupDetail.ungrouped && groupDetail.ungrouped.length"
        class="group-card mb-16"
        shadow="never"
      >
        <template #header>
          <div class="group-header">
            <b>未分组</b>
            <el-tag size="small" type="warning" effect="plain" style="margin-left: 10px">
              合计 {{ fmtNum(groupDetail.ungrouped.reduce((s, p) => s + p.total, 0)) }}
            </el-tag>
          </div>
        </template>
        <el-table :data="groupDetail.ungrouped" border stripe size="small" max-height="480">
          <el-table-column label="姓名" width="110" fixed="left">
            <template #default="{ row }"><b>{{ row.name }}</b></template>
          </el-table-column>
          <el-table-column v-for="(d, i) in groupDetail.date_labels" :key="'ud'+i" :label="d" width="90" align="right">
            <template #default="{ row }">{{ fmtNum(row.daily_list[i]) }}</template>
          </el-table-column>
          <el-table-column label="平均接待量" width="110" align="right" fixed="right">
            <template #default="{ row }"><b>{{ fmtNum(row.avg) }}</b></template>
          </el-table-column>
        </el-table>
      </el-card>
      <el-empty v-if="!groupDetail.groups?.length && !groupDetail.ungrouped?.length" description="暂无接待明细数据" />
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh, Calendar } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import FilterBar from '@/components/dashboard/FilterBar.vue'
import SectionHeader from '@/components/dashboard/SectionHeader.vue'
import KpiCard from '@/components/dashboard/KpiCard.vue'
import ChartCard from '@/components/dashboard/ChartCard.vue'
import { reception } from '@/api/analytics'
import dayjs from 'dayjs'

// ===== 筛选状态 =====
const rangeMode = ref('90')
const customRange = ref([])
const loading = ref(false)

// FilterBar 快捷选项 (对齐周报 默认90天周视图)
const receptionQuickOptions = [
  { value: '90', label: '近90天(周)' },
  { value: '180', label: '近180天' },
  { value: '365', label: '近一年' },
]

function weekDiffPct(cur, prev) {
  if (!prev) return null
  const pct = Math.round((cur - prev) / prev * 1000) / 10
  return pct === 0 ? 0 : (Number.isFinite(pct) ? pct : null)
}

const effectiveStart = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  return dayjs().format('YYYY-MM-DD')
})

const dateRangeText = computed(() => `${effectiveStart.value || '-'} 至 ${effectiveEnd.value || '-'}`)

function onRangeChange() { loadAll() }

// ===== 数据容器 =====
const overview = ref([])         // 模块1总览
const categorySummary = ref([])  // 模块2分项
const perCapita = ref([])        // 模块3人均
const groupDetail = ref({ groups: [], ungrouped: [], date_keys: [], date_labels: [] }) // 模块4分组

// ===== 表格行(可注入 remark) =====
const overviewRows = computed(() => overview.value)
const categoryRows = computed(() => categorySummary.value)
const perCapitaRows = computed(() => perCapita.value)

// ===== 最新周/上周 (用于卡片环比) =====
const latestWeek = computed(() => overview.value[overview.value.length - 1] ||
  { reception_total: 0, daily_avg: 0, staff_count: 0, per_capita_daily: 0, day_count: 0 })
const prevWeek = computed(() => overview.value[overview.value.length - 2] || { reception_total: 0 })

// ===== 工具函数 =====
function fmtNum(v) {
  if (v == null || v === '') return '-'
  const n = Number(v)
  if (Number.isNaN(n)) return v
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
function weekDiffText(cur, prev) {
  if (!prev) return '-'
  const pct = (cur - prev) / prev
  const arrow = pct >= 0 ? '↑' : '↓'
  return `${arrow} ${(Math.abs(pct) * 100).toFixed(1)}%`
}
function weekDiffColor(cur, prev) {
  if (!prev) return ''
  const pct = (cur - prev) / prev
  return pct > 0 ? 'diff-up' : pct < 0 ? 'diff-down' : ''
}
function highlightCell(v, type) {
  if (v == null) return ''
  // 周报中用高亮背景色标注重点数值,这里用类名做轻量强调
  const THRESHOLDS = { complaint: 500, package: 3000 }
  if (v >= THRESHOLDS[type]) return 'cell-highlight'
  return ''
}
// 给 X 轴取周标签
const weekLabels = computed(() => overview.value.map(r => (r.week_no ? r.week_no + '周' : r.week_start?.slice(5) || '')))

// ===== 图表配置 =====
// 模块1:总体接待量趋势(柱接待量 + 双线人数&人均)
const overviewTrendOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
  legend: { data: ['接待量', '客服人数', '人均接待(天)'], top: 0 },
  grid: { left: 60, right: 60, bottom: 30, top: 36 },
  xAxis: { type: 'category', data: weekLabels.value, axisLabel: { fontSize: 10 } },
  yAxis: [
    { type: 'value', name: '接待量', axisLabel: { formatter: v => v >= 10000 ? (v / 10000).toFixed(1) + '万' : v } },
    { type: 'value', name: '人数 / 人均', min: 0 },
  ],
  series: [
    {
      name: '接待量',
      type: 'bar',
      data: overview.value.map(r => r.reception_total),
      itemStyle: { color: '#6aa7ff' },
      barMaxWidth: 28,
      label: { show: true, position: 'top', fontSize: 10, formatter: p => p.value >= 10000 ? (p.value / 10000).toFixed(1) + '万' : p.value },
    },
    {
      name: '客服人数',
      type: 'line',
      yAxisIndex: 1,
      data: overview.value.map(r => r.staff_count),
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      itemStyle: { color: '#f5a623' },
      lineStyle: { color: '#f5a623' },
      label: { show: true, fontSize: 10, color: '#f5a623' },
    },
    {
      name: '人均接待(天)',
      type: 'line',
      yAxisIndex: 1,
      data: overview.value.map(r => r.per_capita_daily),
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      itemStyle: { color: '#67c23a' },
      lineStyle: { color: '#67c23a' },
      label: { show: true, fontSize: 10, color: '#67c23a' },
    },
  ],
}))

// 模块1:客服人数及人均接待(柱人均接待 + 线人数)
const staffPerCapitaOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
  legend: { data: ['人均接待', '客服人数'], top: 0 },
  grid: { left: 60, right: 60, bottom: 30, top: 36 },
  xAxis: { type: 'category', data: weekLabels.value, axisLabel: { fontSize: 10 } },
  yAxis: [
    { type: 'value', name: '人均接待' },
    { type: 'value', name: '人数', min: 0 },
  ],
  series: [
    {
      name: '人均接待',
      type: 'bar',
      data: overview.value.map(r => r.per_capita_weekly),
      itemStyle: { color: '#67c23a', opacity: 0.65 },
      barMaxWidth: 28,
      label: { show: true, position: 'top', fontSize: 10 },
    },
    {
      name: '客服人数',
      type: 'line',
      yAxisIndex: 1,
      data: overview.value.map(r => r.staff_count),
      smooth: true,
      symbol: 'circle',
      symbolSize: 7,
      itemStyle: { color: '#f5a623' },
      lineStyle: { color: '#f5a623', width: 2 },
      label: { show: true, fontSize: 10, color: '#f5a623', formatter: p => p.value + '人' },
    },
  ],
}))

// 模块2:投诉趋势 + 线性拟合
const complaintTrendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['投诉', '线性(投诉)'], top: 0 },
  grid: { left: 50, right: 20, bottom: 30, top: 36 },
  xAxis: { type: 'category', data: weekLabels.value },
  yAxis: { type: 'value' },
  series: [
    {
      name: '投诉',
      type: 'line',
      data: categorySummary.value.map(r => r['投诉'] || 0),
      smooth: false,
      symbol: 'circle',
      symbolSize: 7,
      itemStyle: { color: '#f56c6c' },
      lineStyle: { color: '#f56c6c', width: 2 },
      label: { show: true, fontSize: 10, color: '#f56c6c' },
    },
    ...linearTrendLine(
      '线性(投诉)',
      categorySummary.value.map(r => r['投诉'] || 0),
      { color: '#f56c6c', type: 'dashed' },
    ),
  ],
}))

const refundTrendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['退款'], top: 0 },
  grid: { left: 50, right: 20, bottom: 30, top: 36 },
  xAxis: { type: 'category', data: weekLabels.value },
  yAxis: { type: 'value', axisLabel: { formatter: v => v >= 1000 ? v / 1000 + 'k' : v } },
  series: [
    {
      name: '退款',
      type: 'line',
      data: categorySummary.value.map(r => r['退款'] || 0),
      smooth: true,
      symbol: 'circle',
      symbolSize: 7,
      itemStyle: { color: '#409eff' },
      lineStyle: { color: '#409eff', width: 2 },
      areaStyle: { opacity: 0.1 },
      label: { show: true, fontSize: 10, color: '#409eff' },
    },
  ],
}))

const othersStackOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  legend: { data: ['套餐问题', '咨询', '其他问题', '综合问题'], top: 0 },
  grid: { left: 60, right: 20, bottom: 30, top: 36 },
  xAxis: { type: 'category', data: weekLabels.value },
  yAxis: {
    type: 'value',
    axisLabel: { formatter: v => v >= 10000 ? (v / 10000).toFixed(1) + '万' : v },
  },
  series: [
    stackArea('套餐问题', categorySummary.value.map(r => r['套餐问题'] || 0), '#909399'),
    stackArea('咨询', categorySummary.value.map(r => r['咨询'] || 0), '#13c2c2'),
    stackArea('其他问题', categorySummary.value.map(r => r['其他问题'] || 0), '#e6a23c'),
    stackArea('综合问题', categorySummary.value.map(r => r['综合问题'] || 0), '#b37feb'),
  ],
}))

// 模块3:人均图
const pcComplaintOption = computed(() => dualAxisPerCapitaOption(
  weekLabels.value,
  perCapita.value.map(r => r.complaint_pc || 0),
  '投诉人均',
  '#f56c6c',
  perCapita.value.map(r => r.complaint_staff || 0),
  '投诉组',
))
const pcRefundOption = computed(() => dualAxisPerCapitaOption(
  weekLabels.value,
  perCapita.value.map(r => r.refund_pc || 0),
  '退款人均',
  '#409eff',
  perCapita.value.map(r => r.refund_staff || 0),
  '退款组',
))
const pcOthersOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
  legend: { data: ['套餐人均', '咨询人均', '套餐组', '咨询组'], top: 0 },
  grid: { left: 56, right: 56, bottom: 30, top: 36 },
  xAxis: { type: 'category', data: weekLabels.value },
  yAxis: [
    { type: 'value', name: '人均接待' },
    { type: 'value', name: '人数' },
  ],
  series: [
    {
      name: '套餐人均', type: 'bar', data: perCapita.value.map(r => r.package_pc || 0),
      itemStyle: { color: '#909399' }, barMaxWidth: 22,
    },
    {
      name: '咨询人均', type: 'bar', data: perCapita.value.map(r => r.consult_pc || 0),
      itemStyle: { color: '#13c2c2', opacity: 0.85 }, barMaxWidth: 22,
    },
    {
      name: '套餐组', type: 'line', yAxisIndex: 1,
      data: perCapita.value.map(r => r.package_staff || 0),
      symbol: 'circle', symbolSize: 7, itemStyle: { color: '#909399' },
      lineStyle: { color: '#909399', type: 'dashed', width: 2 },
      label: { show: true, fontSize: 10, color: '#909399', formatter: p => p.value + '人' },
    },
    {
      name: '咨询组', type: 'line', yAxisIndex: 1,
      data: perCapita.value.map(r => r.consult_staff || 0),
      symbol: 'circle', symbolSize: 7, itemStyle: { color: '#13c2c2' },
      lineStyle: { color: '#13c2c2', type: 'dashed', width: 2 },
      label: { show: true, fontSize: 10, color: '#13c2c2', formatter: p => p.value + '人' },
    },
  ],
}))

// ===== 图工厂 =====
function dualAxisPerCapitaOption(labels, barData, barName, barColor, lineData, lineName) {
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: [barName, lineName], top: 0 },
    grid: { left: 56, right: 56, bottom: 30, top: 36 },
    xAxis: { type: 'category', data: labels },
    yAxis: [
      { type: 'value', name: '人均接待' },
      { type: 'value', name: '人数', min: 0 },
    ],
    series: [
      {
        name: barName, type: 'bar', data: barData,
        itemStyle: { color: barColor }, barMaxWidth: 26,
        label: { show: true, position: 'top', fontSize: 10 },
      },
      {
        name: lineName, type: 'line', yAxisIndex: 1, data: lineData,
        symbol: 'circle', symbolSize: 8, itemStyle: { color: barColor },
        lineStyle: { color: barColor, width: 2 },
        label: { show: true, fontSize: 10, color: barColor, formatter: p => p.value + '人' },
      },
    ],
  }
}
function stackArea(name, data, color) {
  return {
    name, type: 'line', stack: 'stack_others', data,
    symbol: 'none', smooth: false,
    areaStyle: { opacity: 0.55, color },
    lineStyle: { width: 1, color },
    itemStyle: { color },
  }
}
// 简单线性回归(最小二乘) — 用于"投诉"折线的拟合虚线
function linearTrendLine(name, yArr, style) {
  const n = yArr.length
  if (n < 2) return []
  let sx = 0, sy = 0, sxy = 0, sx2 = 0
  for (let i = 0; i < n; i++) { sx += i; sy += yArr[i]; sxy += i * yArr[i]; sx2 += i * i }
  const denom = n * sx2 - sx * sx || 1
  const slope = (n * sxy - sx * sy) / denom
  const intercept = (sy - slope * sx) / n
  const fit = []
  for (let i = 0; i < n; i++) fit.push(slope * i + intercept)
  return [{
    name, type: 'line', data: fit, showSymbol: false,
    lineStyle: { color: style.color, type: style.type || 'dashed', opacity: 0.7, width: 1.2 },
    itemStyle: { color: style.color },
    tooltip: { show: false },
  }]
}

// ===== 数据加载 =====
async function loadAll() {
  loading.value = true
  try {
    const params = {}
    if (effectiveStart.value) params.start = effectiveStart.value
    if (effectiveEnd.value) params.end = effectiveEnd.value
    const [o, c, p, g] = await Promise.all([
      reception.overview(params),
      reception.categorySummary(params),
      reception.perCapita(params),
      // group-detail 用最近7天,而非整个大范围(对齐周报一组人一周日接待的视图)
      reception.groupDetail({
        start: dayjs().subtract(6, 'day').format('YYYY-MM-DD'),
        end: dayjs().format('YYYY-MM-DD'),
      }),
    ])
    overview.value = o.data || []
    categorySummary.value = c.data || []
    perCapita.value = p.data || []
    groupDetail.value = g.data || { groups: [], ungrouped: [], date_keys: [], date_labels: [] }
  } catch (e) {
    // 错误静默(接口报错前端不崩)
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.reception-dashboard { padding-bottom: 20px; }
.section-card {
  background: #fff;
  border-radius: 6px;
  padding: 20px;
  border: 1px solid #ebeef5;
}
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.text-muted { color: #909399; font-size: 13px; }

/* KpiCard #extra 插槽的本地样式 */
.kpi-sub-local { font-size: 12px; color: #909399; margin-top: 6px; line-height: 1.4; }
.diff-up { color: #f56c6c; font-weight: 600; }
.diff-down { color: #67c23a; font-weight: 600; }

.week-cell {
  display: inline-flex; align-items: center; gap: 4px;
  font-weight: 500;
}
.week-cal { cursor: pointer; }
.cell-highlight {
  color: #f56c6c;
  font-weight: 600;
}
.group-header {
  display: flex; align-items: center;
}
/*
.filter-bar / .section-title / .section-subtitle / .stat-* 已迁移到 components/dashboard/* 组件
.chart-title 已迁移到 ChartCard.vue
*/
</style>
