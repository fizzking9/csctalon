<template>
  <div class="personnel-analysis">
    <!-- ============ 筛选栏 ============ -->
    <section class="section-card">
      <div class="filter-bar">
        <span class="block-title" style="margin: 0">人员分析</span>
        <el-select v-model="department" placeholder="部门" size="small" style="width: 140px" @change="loadPersonnel">
          <el-option label="全部部门" value="" />
          <el-option label="企微" value="企微" />
          <el-option label="店铺" value="店铺" />
          <el-option label="研发" value="研发" />
        </el-select>
        <el-select v-model="staffType" placeholder="人员类型" size="small" style="width: 160px" @change="loadPersonnel">
          <el-option label="全部类型" value="" />
          <el-option label="专职挽回" value="专职挽回" />
          <el-option label="退款操作" value="退款操作" />
          <el-option label="其他人员挽回" value="其他人员挽回" />
        </el-select>
        <el-button :icon="Refresh" size="small" type="primary" :loading="tableLoading" @click="loadAll">刷新</el-button>
      </div>
    </section>

    <!-- ============ 1. 人员对比表 ============ -->
    <section class="section-card">
      <div class="block-title">人员对比表</div>
      <div v-if="summary" class="summary-line">
        共 {{ formatNum(summary.total_sn) }} 个接待SN ·
        成功 <span class="ok">{{ formatNum(summary.success_sn) }}</span> ·
        失败 <span class="ng">{{ formatNum(summary.failed_sn) }}</span> ·
        整体成功率 <span :style="{ color: rateColor(summary.success_rate), fontWeight: 600 }">{{ rateText(summary.success_rate) }}</span>
      </div>
      <el-table
        v-loading="tableLoading"
        :data="staffDetail"
        border
        stripe
        size="small"
        max-height="520"
        style="width: 100%"
      >
        <el-table-column label="姓名" width="100" fixed>
          <template #default="{ row }">
            <span style="font-weight: 600">{{ row.name || row.operator }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="department" label="部门" width="80" />
        <el-table-column prop="group" label="分组" width="100">
          <template #default="{ row }">
            <span>{{ row.group || '未分组' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="staff_type" label="人员类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="staffTypeTagType(row.staff_type)" effect="plain">
              {{ row.staff_type || '其他' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="total_sn" label="接待SN" width="90" align="right" sortable
          :sort-method="(a, b) => numCmp(a.total_sn, b.total_sn)">
          <template #default="{ row }">{{ formatNum(row.total_sn) }}</template>
        </el-table-column>
        <el-table-column prop="success_sn" label="成功" width="70" align="right">
          <template #default="{ row }"><span style="color: #16a34a">{{ formatNum(row.success_sn) }}</span></template>
        </el-table-column>
        <el-table-column prop="failed_sn" label="失败" width="70" align="right">
          <template #default="{ row }"><span style="color: #dc2626">{{ formatNum(row.failed_sn) }}</span></template>
        </el-table-column>
        <el-table-column prop="success_rate" label="成功率" width="100" align="center" sortable
          :sort-method="(a, b) => numCmp(a.success_rate, b.success_rate)">
          <template #default="{ row }">
            <el-tag size="small" :type="rateTagType(row.success_rate)" :effect="rateTagEffect(row.success_rate)">
              {{ rateText(row.success_rate) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="refund_op_amount" label="退款操作金额" width="130" align="right" sortable
          :sort-method="(a, b) => numCmp(a.refund_op_amount, b.refund_op_amount)">
          <template #default="{ row }">
            <span :style="{ color: num(row.refund_op_amount) > 0 ? '#b91c1c' : '#9ca3af', fontWeight: num(row.refund_op_amount) > 0 ? 600 : 400 }">
              ¥{{ formatMoney(row.refund_op_amount) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="estimated_save_amount" label="挽回估算金额" width="130" align="right">
          <template #default="{ row }">
            <span :style="{ color: num(row.estimated_save_amount) > 0 ? '#16a34a' : '#9ca3af' }">
              ¥{{ formatMoney(row.estimated_save_amount) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" size="small" @click="openPersonAudit(row.person_id, row.name)">时序</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- ============ 2. 人员成功率趋势 ============ -->
    <section class="section-card">
      <div class="chart-header">
        <span class="block-title" style="margin: 0">人员成功率趋势</span>
        <div class="chart-toolbar">
          <span class="trend-hint">默认: 成功率 Top3 + Bottom3</span>
          <el-radio-group v-model="trendGrain" size="small" @change="loadTrend">
            <el-radio-button value="day">按日</el-radio-button>
            <el-radio-button value="week">按周</el-radio-button>
          </el-radio-group>
        </div>
      </div>
      <BaseChart v-if="trendHasData" :option="trendOption" height="340px" />
      <el-empty v-else description="暂无趋势数据" />
    </section>

    <!-- ============ 3. 人员 × 原因 热力图 ============ -->
    <section class="section-card">
      <div class="chart-header">
        <span class="block-title" style="margin: 0">人员 × 原因 热力图</span>
        <span class="trend-hint">值=成功率(%) · 绿高红低</span>
      </div>
      <BaseChart v-if="heatmapHasData" :option="heatmapOption" height="420px" />
      <el-empty v-else description="暂无热力图数据" />
    </section>

    <!-- ============ 人员详情抽屉 ============ -->
    <el-drawer
      v-model="drawerVisible"
      :size="'60%'"
      direction="rtl"
      :title="drawerTitle"
      destroy-on-close
    >
      <div v-loading="drawerLoading" class="drawer-body">
        <!-- 人员概要 -->
        <el-card v-if="currentPerson" shadow="never" class="mb-12">
          <el-descriptions :column="4" border size="small">
            <el-descriptions-item label="姓名">{{ currentPerson.name || currentPerson.operator }}</el-descriptions-item>
            <el-descriptions-item label="部门">{{ currentPerson.department || '-' }}</el-descriptions-item>
            <el-descriptions-item label="分组">{{ currentPerson.group || '未分组' }}</el-descriptions-item>
            <el-descriptions-item label="类型">{{ currentPerson.staff_type || '其他' }}</el-descriptions-item>
            <el-descriptions-item label="接待SN">{{ formatNum(currentPerson.total_sn) }} 个</el-descriptions-item>
            <el-descriptions-item label="成功">{{ formatNum(currentPerson.success_sn) }} 个</el-descriptions-item>
            <el-descriptions-item label="成功率" :span="2">
              <el-tag size="small" :type="rateTagType(currentPerson.success_rate)" :effect="rateTagEffect(currentPerson.success_rate)">
                {{ rateText(currentPerson.success_rate) }}
              </el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <!-- 设备清单 Tab 切换 -->
        <div class="device-toolbar">
          <span class="block-title" style="margin: 0">设备清单</span>
          <el-radio-group v-model="deviceTab" size="small" @change="loadPersonDevices">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="success">成功</el-radio-button>
            <el-radio-button value="failed">失败</el-radio-button>
          </el-radio-group>
        </div>
        <el-table :data="personDevices.devices || []" border stripe size="small" max-height="520" style="width: 100%">
          <el-table-column prop="device_sn" label="SN" width="160" fixed />
          <el-table-column prop="first_reception_time" label="首次接待" width="150" />
          <el-table-column prop="last_reception_time" label="末次接待" width="150" />
          <el-table-column prop="reception_count" label="接待次数" width="90" align="right" sortable />
          <el-table-column prop="result" label="结果" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.result === 'success' ? 'success' : 'danger'" effect="plain">
                {{ row.result === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="fail_reason" label="失败原因" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.fail_reason || '-' }}</template>
          </el-table-column>
          <el-table-column prop="refund_amount" label="退款金额" width="120" align="right">
            <template #default="{ row }">
              <span :style="{ color: num(row.refund_amount) > 0 ? '#b91c1c' : '#9ca3af' }">¥{{ formatMoney(row.refund_amount) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="refund_time" label="退款时间" width="150">
            <template #default="{ row }">{{ row.refund_time || '-' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDeviceAudit(row.device_sn)">时序</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!(personDevices.devices && personDevices.devices.length)" description="暂无设备记录" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { refundDeep } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
})

// ---- 筛选条件 ----
const department = ref('')
const staffType = ref('')

// ---- 人员对比表 ----
const tableLoading = ref(false)
const staffDetail = ref([])
const summary = ref(null)

// ---- 人员详情抽屉 ----
const drawerVisible = ref(false)
const drawerLoading = ref(false)
const currentPerson = ref(null)
const personDevices = ref({ operator: '', name: '', devices: [], summary: {} })
const deviceTab = ref('all')

// ---- 趋势图 ----
const trendGrain = ref('day')
const trendLoading = ref(false)
const trendData = ref({ dates: [], persons: [] })

// ---- 热力图 ----
const heatmapLoading = ref(false)
const heatmapData = ref({ reasons: [], persons: [] })

const drawerTitle = computed(() => {
  const p = currentPerson.value
  return p ? `人员详情 · ${p.name || p.operator}` : '人员详情'
})

// ============ 工具函数 ============
function num(v) {
  const n = Number(v)
  return Number.isNaN(n) ? 0 : n
}
function numCmp(a, b) {
  return num(a) - num(b)
}
function formatNum(v) {
  const n = Number(v)
  if (v == null || v === '' || Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}
function rateText(v) {
  if (v == null || v === '') return '-'
  return Number(v).toFixed(1) + '%'
}
// 成功率颜色: ≥38% 绿 / 30-38% 黄 / <30% 红
function rateColor(v) {
  const n = Number(v || 0)
  if (n >= 38) return '#16a34a'
  if (n >= 30) return '#ca8a04'
  return '#dc2626'
}
function rateTagType(v) {
  const n = Number(v || 0)
  if (n >= 38) return 'success'
  if (n >= 30) return 'warning'
  return 'danger'
}
function rateTagEffect(v) {
  return Number(v || 0) >= 38 ? 'dark' : 'plain'
}
function staffTypeTagType(st) {
  if (st === '专职挽回') return 'primary'
  if (st === '退款操作') return 'warning'
  return 'info'
}

// 新窗口打开审计时序页
function openPersonAudit(personId, name) {
  if (!personId) return
  const url = `/audit/person?person_id=${personId}&name=${encodeURIComponent(name || '')}&start=${props.start || ''}&end=${props.end || ''}`
  window.open(url, '_blank')
}
function openDeviceAudit(deviceSn) {
  if (!deviceSn) return
  const url = `/audit/device?sn=${encodeURIComponent(deviceSn)}&start=${props.start || ''}&end=${props.end || ''}`
  window.open(url, '_blank')
}

// 计算默认趋势人员: 成功率 Top3 + Bottom3 (仅取 total_sn>0)
function computeDefaultTrendOps() {
  const list = (staffDetail.value || []).filter((r) => num(r.total_sn) > 0)
  const top3 = [...list].sort((a, b) => num(b.success_rate) - num(a.success_rate)).slice(0, 3)
  const bottom3 = [...list].sort((a, b) => num(a.success_rate) - num(b.success_rate)).slice(0, 3)
  const seen = new Set()
  const merged = []
  for (const r of [...top3, ...bottom3]) {
    if (r.operator != null && !seen.has(r.operator)) {
      seen.add(r.operator)
      merged.push(r)
    }
  }
  return merged
}

// ============ 数据加载 ============
async function loadPersonnel() {
  tableLoading.value = true
  try {
    const params = { start: props.start, end: props.end }
    if (department.value) params.department = department.value
    if (staffType.value) params.staff_type = staffType.value
    const res = await refundDeep.personnelAnalysis(params)
    const data = res.data || {}
    staffDetail.value = data.staff_detail || []
    summary.value = data.summary || null
    // 人员表加载完成后, 计算默认 Top3+Bottom3 并拉取趋势
    await loadTrend()
  } catch (e) {
    staffDetail.value = []
    summary.value = null
  } finally {
    tableLoading.value = false
  }
}

async function loadTrend() {
  const ops = computeDefaultTrendOps()
  if (!ops.length) {
    trendData.value = { dates: [], persons: [] }
    return
  }
  trendLoading.value = true
  try {
    const params = {
      start: props.start,
      end: props.end,
      operators: ops.map((r) => r.operator).join(','),
      grain: trendGrain.value,
    }
    const res = await refundDeep.personnelTrend(params)
    trendData.value = res.data || { dates: [], persons: [] }
  } catch (e) {
    trendData.value = { dates: [], persons: [] }
  } finally {
    trendLoading.value = false
  }
}

async function loadHeatmap() {
  heatmapLoading.value = true
  try {
    const res = await refundDeep.personReasonMatrix({ start: props.start, end: props.end, top: 15 })
    heatmapData.value = res.data || { reasons: [], persons: [] }
  } catch (e) {
    heatmapData.value = { reasons: [], persons: [] }
  } finally {
    heatmapLoading.value = false
  }
}

async function loadPersonDevices() {
  if (!currentPerson.value) return
  drawerLoading.value = true
  try {
    const params = {
      operator: currentPerson.value.operator,
      start: props.start,
      end: props.end,
    }
    // result: success / failed / 全部(留空)
    if (deviceTab.value === 'success') params.result = 'success'
    else if (deviceTab.value === 'failed') params.result = 'failed'
    const res = await refundDeep.personDevices(params)
    personDevices.value = res.data || { operator: currentPerson.value.operator, name: currentPerson.value.name, devices: [], summary: {} }
  } catch (e) {
    personDevices.value = { operator: currentPerson.value.operator, name: currentPerson.value.name, devices: [], summary: {} }
  } finally {
    drawerLoading.value = false
  }
}

function openDetail(row) {
  currentPerson.value = row
  deviceTab.value = 'all'
  drawerVisible.value = true
  loadPersonDevices()
}

async function loadAll() {
  // 人员表 + 热力图 并行; 趋势由 loadPersonnel 内部触发
  await Promise.all([loadPersonnel(), loadHeatmap()])
}

// ============ 图表配置 ============
const trendHasData = computed(() => {
  return trendData.value.dates && trendData.value.dates.length && trendData.value.persons && trendData.value.persons.length
})

const trendOption = computed(() => {
  const dates = trendData.value.dates || []
  const persons = trendData.value.persons || []
  // X 轴日期简化为 MM-DD
  const xData = dates.map((d) => (d && d.length >= 10 ? d.slice(5) : d))
  const palette = ['#2563eb', '#16a34a', '#dc2626', '#7c3aed', '#db2777', '#0891b2', '#ea580c', '#65a30d']
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const date = dates[params[0].dataIndex] || params[0].axisValue
        const lines = params.map((p) => {
          const person = persons[p.seriesIndex] || {}
          const trendItem = (person.trend || [])[p.dataIndex] || {}
          const sn = num(trendItem.total_sn)
          const succ = num(trendItem.success_sn)
          return `${p.marker} ${p.seriesName}: <b>${p.value}%</b> (接待 ${sn} / 成功 ${succ})`
        })
        return `<b>${date}</b><br/>` + lines.join('<br/>')
      },
    },
    legend: { data: persons.map((p) => p.name || p.operator), top: 0, type: 'scroll' },
    grid: { left: 48, right: 24, bottom: 56, top: 40 },
    xAxis: { type: 'category', data: xData, boundaryGap: true, axisLabel: { fontSize: 11, rotate: dates.length > 14 ? 30 : 0 } },
    yAxis: { type: 'value', name: '成功率%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { lineStyle: { type: 'dashed' } } },
    series: persons.map((p, idx) => ({
      name: p.name || p.operator,
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      data: (p.trend || []).map((t) => num(t.success_rate)),
      itemStyle: { color: palette[idx % palette.length] },
      lineStyle: { width: 2, color: palette[idx % palette.length] },
    })),
  }
})

const heatmapHasData = computed(() => {
  return heatmapData.value.reasons && heatmapData.value.reasons.length && heatmapData.value.persons && heatmapData.value.persons.length
})

const heatmapOption = computed(() => {
  const reasons = heatmapData.value.reasons || []
  const persons = heatmapData.value.persons || []
  const xLabels = reasons
  const yLabels = persons.map((p) => p.name || p.operator)
  const data = []
  persons.forEach((p, yIdx) => {
    const cellMap = {}
    for (const c of (p.cells || [])) cellMap[c.reason] = c
    reasons.forEach((reason, xIdx) => {
      const cell = cellMap[reason]
      const rate = cell ? num(cell.success_rate) : null
      data.push([xIdx, yIdx, rate, cell])
    })
  })
  return {
    tooltip: {
      formatter: (p) => {
        const cell = p.data && p.data[3]
        const person = persons[p.data[1]] || {}
        if (!cell) {
          return `<b>${person.name || person.operator}</b> - ${reasons[p.data[0]]}<br/>无数据`
        }
        return `<b>${person.name || person.operator}</b> - ${reasons[p.data[0]]}<br/>` +
          `接待: <b>${num(cell.total_sn)}</b> 个<br/>` +
          `成功: <b>${num(cell.success_sn)}</b> 个<br/>` +
          `失败: <b>${num(cell.failed_sn)}</b> 个<br/>` +
          `成功率: <b>${num(cell.success_rate).toFixed(1)}%</b>`
      },
    },
    grid: { left: 110, right: 24, bottom: 70, top: 20 },
    xAxis: {
      type: 'category',
      data: xLabels,
      splitArea: { show: true },
      axisLabel: { fontSize: 11, rotate: 18, interval: 0 },
    },
    yAxis: {
      type: 'category',
      data: yLabels,
      splitArea: { show: true },
      axisLabel: { fontSize: 11 },
    },
    visualMap: {
      min: 0,
      max: 100,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: { color: ['#dc2626', '#f59e0b', '#eab308', '#84cc16', '#16a34a'] },
      text: ['高', '低'],
    },
    series: [
      {
        name: '成功率',
        type: 'heatmap',
        data,
        label: {
          show: true,
          formatter: (p) => {
            const v = p.data[2]
            return v == null ? '-' : v.toFixed(0)
          },
          fontSize: 10,
        },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.3)' } },
      },
    ],
  }
})

// ============ 生命周期 ============
watch(
  () => [props.start, props.end],
  () => {
    loadAll()
  }
)

onMounted(loadAll)
</script>

<style scoped>
.personnel-analysis {
  width: 100%;
}
.section-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.block-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
}
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.summary-line {
  font-size: 12.5px;
  color: #4b5563;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #f9fafb;
  border-radius: 6px;
}
.summary-line .ok {
  color: #16a34a;
  font-weight: 600;
}
.summary-line .ng {
  color: #dc2626;
  font-weight: 600;
}
.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.chart-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.trend-hint {
  font-size: 12px;
  color: #6b7280;
}
.mb-12 {
  margin-bottom: 12px;
}
.drawer-body {
  padding: 0 4px;
}
.device-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .filter-bar { gap: 6px; }
  .block-title { font-size: 14px; }
  .summary-line { font-size: 11px; flex-wrap: wrap; }
  .trend-hint { font-size: 11px; }
  .section-card { padding: 12px; }
  :deep(.el-table) { min-width: 620px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
  :deep(.el-card) { overflow: hidden; }
  .chart-toolbar, .device-toolbar { flex-wrap: wrap; gap: 6px; }
}
</style>
