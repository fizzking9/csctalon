<template>
  <div class="timeliness">
    <!-- 筛选: 日期范围 + 趋势粒度 -->
    <section class="block-card mb-16">
      <div class="filter-bar">
        <span class="block-title" style="margin:0">提现时效</span>
        <el-date-picker
          v-model="filters.range"
          type="daterange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          value-format="YYYY-MM-DD"
          :shortcuts="dateShortcuts"
          size="small"
          @change="loadAll"
        />
        <el-radio-group v-model="grain" size="small" @change="renderTrend">
          <el-radio-button label="day">按日</el-radio-button>
          <el-radio-button label="week">按周</el-radio-button>
          <el-radio-button label="month">按月</el-radio-button>
        </el-radio-group>
        <el-button type="primary" size="small" :icon="Search" :loading="loading" @click="loadAll">分析</el-button>
      </div>
    </section>

    <!-- KPI 条 -->
    <section class="block-card mb-16">
      <div class="block-head"><span class="block-title">钱包沉淀与退款时效</span><span class="block-hint">三路有时效的退款: 直接线上退款 · 钱包提现 · 手动商户号操作 · 沉淀高=用户不提现(资金滞留风险)</span></div>
      <el-row :gutter="12">
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#fef2f2">
            <div class="kpi-label" style="color:#dc2626">钱包沉淀</div>
            <div class="kpi-value">{{ sleep.count }}<span class="unit">笔</span></div>
            <div class="kpi-sub">占转入 {{ sleep.pct }}%</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#fef2f2">
            <div class="kpi-label" style="color:#dc2626">沉淀金额</div>
            <div class="kpi-value">¥{{ formatMoney(sleep.amount) }}</div>
            <div class="kpi-sub">钱仍躺在提现钱包</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#fff7ed">
            <div class="kpi-label" style="color:#ea580c">钱包提现时效(均)</div>
            <div class="kpi-value">{{ fmtD(timeliness.avg) }}<span class="unit">天</span></div>
            <div class="kpi-sub">入钱包→提现, 中位 {{ fmtD(timeliness.median) }} 天</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#eff6ff">
            <div class="kpi-label" style="color:#2563eb">直接退款时效(均)</div>
            <div class="kpi-value">{{ fmtD(timeliness.directAvg) }}<span class="unit">天</span></div>
            <div class="kpi-sub">线上直退, 发起→实际到账</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#f5f3ff">
            <div class="kpi-label" style="color:#7c3aed">手动退款时效(均)</div>
            <div class="kpi-value">{{ fmtD(timeliness.manualAvg) }}<span class="unit">天</span></div>
            <div class="kpi-sub">商户号手动操作, 登记→实际退款</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#f0fdf4">
            <div class="kpi-label" style="color:#16a34a">≤1天提现占比</div>
            <div class="kpi-value">{{ timeliness.pct1 }}<span class="unit">%</span></div>
            <div class="kpi-sub">入钱包后当天提现</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#f8fafc">
            <div class="kpi-label" style="color:#475569">区间出款笔数</div>
            <div class="kpi-value">{{ timeliness.count }}<span class="unit">笔</span></div>
            <div class="kpi-sub">三路合计 ¥{{ formatMoney(timeliness.amount) }}</div>
          </div>
        </el-col>
      </el-row>
    </section>

    <!-- 图1: 三路出款趋势 -->
    <section class="block-card mb-16">
      <div class="block-head">
        <span class="block-title">三路实际出款趋势</span>
        <span class="block-hint">柱=每日实际出款笔数(直退/钱包提现/手动退款堆叠) · 线=钱包平均拖延天数(入钱包→用户提现)</span>
      </div>
      <BaseChart v-if="trendOption.series?.length" :option="trendOption" height="300px" />
      <el-empty v-else description="所选区间无出款数据" :image-size="60" />
    </section>

    <!-- 图2: 沉淀与清理 -->
    <section class="block-card mb-16">
      <div class="block-head">
        <span class="block-title">钱包资金流入 vs 提现流出</span>
        <span class="block-hint">柱=转入(订单退费转入)/提现 · 线=当日净水位(转入−转出)</span>
      </div>
      <BaseChart v-if="waterOption.series?.length" :option="waterOption" height="300px" />
      <el-empty v-else description="暂无钱包水位数据" :image-size="60" />
    </section>

    <!-- 沉淀清单 -->
    <section class="block-card">
      <div class="block-head">
        <span class="block-title">沉淀清单（转入钱包未提现）</span>
        <span class="block-hint">共 {{ sleepTotal }} 条 · 点击 SN 可查看设备审计 · 拖延越久越需催提</span>
      </div>
      <el-table :data="sleepList" border stripe size="small" max-height="420">
        <el-table-column prop="refund_no" label="退款单号" width="160" fixed show-overflow-tooltip />
        <el-table-column prop="device_sn" label="设备SN" width="140" fixed>
          <template #default="{ row }">
            <span v-if="row.device_sn" class="sn-chip" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</span>
            <span v-else style="color:#9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="refund_amount" label="金额(¥)" width="110" align="right" sortable>
          <template #default="{ row }">{{ formatMoney(row.refund_amount) }}</template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" width="100" show-overflow-tooltip />
        <el-table-column prop="d1_refund_created" label="转入时间" width="160" show-overflow-tooltip />
        <el-table-column prop="waiting_days" label="已滞留(天)" width="100" align="right" sortable>
          <template #default="{ row }">
            <el-tag size="small" :type="row.waiting_days > 7 ? 'danger' : row.waiting_days > 3 ? 'warning' : 'info'">
              {{ row.waiting_days ?? '—' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div class="pag-wrap">
        <el-pagination
          v-model:current-page="sleepPage"
          v-model:page-size="sleepPageSize"
          :total="sleepTotal"
          :page-sizes="[30, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          small
          @current-change="loadSleeping"
          @size-change="sleepPage = 1; loadSleeping()"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import BaseChart from '@/components/BaseChart.vue'
import { refundWithdrawal as rwApi, cash } from '@/api/analytics'
import { useAudit } from '@/composables/useAudit'
import { formatMoney } from '@/utils/format'

const { openDeviceAudit } = useAudit()

const today = new Date()
const last30 = new Date(today.getTime() - 29 * 86400000)
const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`

const filters = reactive({ range: [fmt(last30), fmt(today)] })
const grain = ref('day')
const loading = ref(false)

const dateShortcuts = [
  { text: '最近7天', value: () => { const e = new Date(); const s = new Date(e - 6*864e5); return [s, e] } },
  { text: '最近30天', value: () => { const e = new Date(); const s = new Date(e - 29*864e5); return [s, e] } },
  { text: '最近90天', value: () => { const e = new Date(); const s = new Date(e - 89*864e5); return [s, e] } },
]

const overview = ref({ wallet_sleep: {}, paths: {} })
const dailySplit = ref([])
const walletWater = ref([])
const sleepList = ref([])
const sleepTotal = ref(0)
const sleepPage = ref(1)
const sleepPageSize = ref(50)

function fmtD(v) {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toFixed(1)
}

const sleep = computed(() => {
  const ws = overview.value?.wallet_sleep || {}
  return { count: ws.still_in_wallet_count || 0, pct: ws.still_in_wallet_pct ?? 0, amount: ws.still_in_wallet_amount || 0 }
})

const timeliness = computed(() => {
  const a = overview.value?.paths?.A_wallet || {}
  const b = overview.value?.paths?.B_direct || {}
  const m = overview.value?.cashout_split?.manual_refund || {}
  const d24 = dailySplit.value || []
  const totalCount = d24.reduce((s, r) => s + (r.total_count || 0), 0)
  const totalAmount = d24.reduce((s, r) => s + (r.total_amount || 0), 0)
  // ≤1天提现占比: 从日粒度数据按 wallet_count 加权估算
  let le1 = 0
  const weights = []
  d24.forEach(r => {
    if (r.wallet_avg_days_2_4 != null && r.wallet_count) {
      weights.push({ w: r.wallet_count, v: r.wallet_avg_days_2_4 })
    }
  })
  if (weights.length) {
    const totalW = weights.reduce((s, x) => s + x.w, 0)
    const avg = weights.reduce((s, x) => s + x.v * x.w, 0) / totalW
    // avg=0 也视为当天提现(占比100%), 避免 1/0 → Infinity 显示异常
    le1 = avg > 1 ? Math.max(0, Math.round(100 / avg)) : 100
  }
  return {
    avg: a.avg_days_2_4 ?? 0, median: a.median_days_2_4 ?? 0,
    directAvg: b.avg_days ?? 0, manualAvg: m.avg_days ?? 0,
    pct1: le1,
    count: totalCount, amount: totalAmount,
  }
})

// ---- 图1: 三路实际出款趋势 (按日/周/月聚合) ----
const trendOption = computed(() => {
  const agg = aggregateTrend()
  if (!agg.labels.length) return {}
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['直退笔数', '钱包提现笔数', '手动退款笔数', '平均拖延(天)'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: agg.labels, axisLabel: { fontSize: 11, rotate: agg.labels.length > 12 ? 30 : 0 } },
    yAxis: [
      { type: 'value', name: '笔数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '天数', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      { name: '直退笔数', type: 'bar', stack: 'out', data: agg.directCounts, barMaxWidth: 22, itemStyle: { color: '#93c5fd', borderRadius: [2, 2, 0, 0] } },
      { name: '钱包提现笔数', type: 'bar', stack: 'out', data: agg.walletCounts, barMaxWidth: 22, itemStyle: { color: '#fbbf24' } },
      { name: '手动退款笔数', type: 'bar', stack: 'out', data: agg.manualCounts, barMaxWidth: 22, itemStyle: { color: '#c4b5fd', borderRadius: [4, 4, 0, 0] } },
      { name: '平均拖延(天)', type: 'line', yAxisIndex: 1, data: agg.avgDays, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#f59e0b' }, lineStyle: { width: 2.5, color: '#f59e0b' } },
    ],
  }
})

function aggregateTrend() {
  const rows = dailySplit.value || []
  if (!rows.length) return { labels: [], directCounts: [], walletCounts: [], manualCounts: [], avgDays: [] }
  if (grain.value === 'day') {
    return {
      labels: rows.map(r => r.date.slice(5)),
      directCounts: rows.map(r => r.direct_count || 0),
      walletCounts: rows.map(r => r.wallet_count || 0),
      manualCounts: rows.map(r => r.manual_count || 0),
      avgDays: rows.map(r => (r.wallet_avg_days_2_4 == null ? null : Number(r.wallet_avg_days_2_4).toFixed(1))),
    }
  }
  // 按周/月聚合: 加权平均拖延天数
  const groups = new Map()
  rows.forEach(r => {
    const d = new Date(r.date)
    let key, label
    if (grain.value === 'week') {
      const iso = getISOWeek(d)
      key = `${d.getFullYear()}-W${iso}`
      label = `${d.getMonth()+1}/${d.getDate()}周`
    } else {
      key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`
      label = `${d.getFullYear()}-${d.getMonth()+1}`
    }
    if (!groups.has(key)) { groups.set(key, { sum: 0, n: 0, dc: 0, wc: 0, mc: 0, label }) }
    const g = groups.get(key)
    g.dc += r.direct_count || 0
    g.wc += r.wallet_count || 0
    g.mc += r.manual_count || 0
    if (r.wallet_avg_days_2_4 != null) { g.sum += r.wallet_avg_days_2_4 * (r.wallet_count || 0); g.n += (r.wallet_count || 0) }
  })
  const sorted = [...groups.entries()].sort((a, b) => a[0] < b[0] ? -1 : 1)
  return {
    labels: sorted.map(([, g]) => g.label),
    directCounts: sorted.map(([, g]) => g.dc),
    walletCounts: sorted.map(([, g]) => g.wc),
    manualCounts: sorted.map(([, g]) => g.mc),
    avgDays: sorted.map(([, g]) => (g.n ? (g.sum / g.n).toFixed(1) : null)),
  }
}

function getISOWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
  const dayNum = d.getUTCDay() || 7
  d.setUTCDate(d.getUTCDate() + 4 - dayNum)
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1))
  return Math.ceil((((d - yearStart) / 86400000) + 1) / 7)
}

// ---- 图2: 沉淀与清理 ----
const waterOption = computed(() => {
  const rows = walletWater.value || []
  if (!rows.length) return {}
  const dates = rows.map(r => String(r.date).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['转入', '提现', '净水位'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11, rotate: dates.length > 14 ? 30 : 0 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '净水位', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      { name: '转入', type: 'bar', data: rows.map(r => r.wallet_in), itemStyle: { color: '#10b981' }, barMaxWidth: 26 },
      { name: '提现', type: 'bar', data: rows.map(r => r.withdraw), itemStyle: { color: '#f87171' }, barMaxWidth: 26 },
      { name: '净水位', type: 'line', yAxisIndex: 1, data: rows.map(r => (r.wallet_in - r.withdraw)), smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
    ],
  }
})

function renderTrend() {
  // grain 变化直接由 trendOption computed 重新计算
}

function rangeParams() {
  const params = {}
  if (filters.range && filters.range.length === 2) {
    params.start = filters.range[0] + 'T00:00:00'
    params.end = filters.range[1] + 'T23:59:59'
  }
  return params
}

async function loadSleeping() {
  const params = rangeParams()
  params.page = sleepPage.value
  params.page_size = sleepPageSize.value
  try {
    const res = await rwApi.sleeping(params)
    sleepTotal.value = res?.data?.total || 0
    sleepList.value = res?.data?.list || []
  } catch (e) {
    sleepList.value = []
  }
}

async function loadAll() {
  loading.value = true
  try {
    const params = rangeParams()
    const [o, ds, wv] = await Promise.all([
      rwApi.overview(params),
      rwApi.dailySplit(params),
      cash.walletVsWithdraw(params),
    ])
    overview.value = o?.data || {}
    dailySplit.value = ds?.data || []
    walletWater.value = wv?.data || []
    sleepPage.value = 1
    await loadSleeping()
  } catch (e) {
    ElMessage.error('加载失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.timeliness { width: 100%; }
.block-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.block-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 600; color: #111827; }
.block-hint { font-size: 12px; color: #6b7280; }
.mb-16 { margin-bottom: 16px; }
.filter-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.kpi-card { border-radius: 10px; padding: 12px 14px 10px; border: 1px solid rgba(148,163,184,0.15); display: flex; flex-direction: column; gap: 3px; min-height: 84px; }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { margin-top: 2px; font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; }
.unit { font-size: 12px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
.sn-chip { color: #2563eb; cursor: pointer; font-weight: 600; text-decoration: underline; text-decoration-style: dotted; }
.sn-chip:hover { color: #1d4ed8; }
.pag-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
