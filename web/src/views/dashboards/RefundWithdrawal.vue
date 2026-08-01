<template>
  <div class="refund-withdrawal-board page-container">
    <el-card class="filter-card" shadow="never">
      <el-form :inline="true" :model="filters" size="default" label-width="auto">
        <el-form-item label="实际出款日期">
          <el-date-picker
            v-model="filters.range"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期(出款)"
            end-placeholder="结束日期(出款)"
            value-format="YYYY-MM-DD"
            :shortcuts="dateShortcuts"
            @change="loadAll"
          />
        </el-form-item>
        <el-form-item label="路径筛选">
          <el-select v-model="filters.path" @change="loadAll" style="width: 160px;">
            <el-option label="全部路径" value="ALL" />
            <el-option label="A + B 已出款" value="AB" />
            <el-option label="A 转入提现账户" value="A" />
            <el-option label="B 直退 (微信/支付宝等)" value="B" />
          </el-select>
        </el-form-item>
        <el-form-item label="明细慢单>3天">
          <el-switch v-model="filters.slowOnly" @change="loadDetail" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="loadAll">重新加载</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- KPI 卡片区：总体 + 钱包沉淀 -->
    <el-row :gutter="16" class="kpi-row">
      <el-col :xs="12" :sm="8" :md="6" :lg="4" v-for="k in topKpis" :key="k.label">
        <el-card shadow="hover" class="kpi-card" :class="k.type">
          <div class="kpi-label">{{ k.label }}</div>
          <div class="kpi-value">{{ k.value }}</div>
          <div class="kpi-sub">{{ k.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 本窗口内实际出款结构（核心关注项）：合计 + 直接线上退款 + 钱包历史沉淀今日提现 -->
    <el-row :gutter="16" class="kpi-row split-kpi-row">
      <el-col :xs="24" :sm="8" v-for="(k, i) in cashoutSplitCards" :key="i">
        <el-card shadow="hover" class="kpi-card" :class="k.type">
          <div class="kpi-label">{{ k.label }}</div>
          <div class="kpi-value">{{ k.value }}</div>
          <div class="kpi-sub" v-html="k.sub"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 钱包路径出款时效分析卡片：只有 A 路径需要分析耗时，B 直退均为秒级/当天 → 耗时分布与 A 对比无意义 -->
    <el-row :gutter="16" class="metric-row">
      <el-col :lg="24" v-for="p in pathMetrics" :key="p.key">
        <el-card shadow="never" class="metric-card">
          <template #header>
            <div class="card-head">
              <span class="path-tag" :class="p.key">{{ p.title }}</span>
              <span class="path-desc">{{ p.desc }}</span>
            </div>
          </template>
          <div v-if="p.data.count" class="metric-grid">
            <div class="metric-item"><div class="l">退款单数</div><div class="v">{{ p.data.count.toLocaleString() }} 笔</div></div>
            <div class="metric-item"><div class="l">退款金额</div><div class="v">¥ {{ p.data.amount.toLocaleString() }}</div></div>
            <div class="metric-item"><div class="l">平均耗时</div><div class="v days">{{ fmtD(p.data.avg_days) }} 天</div></div>
            <div class="metric-item"><div class="l">中位数</div><div class="v days">{{ fmtD(p.data.median_days) }} 天</div></div>
            <div class="metric-item"><div class="l">P75</div><div class="v days">{{ fmtD(p.data.p75_days) }} 天</div></div>
            <div class="metric-item"><div class="l">P95</div><div class="v days warn">{{ fmtD(p.data.p95_days) }} 天</div></div>
            <div class="metric-item"><div class="l">≤1天到账率</div><div class="v pct ok">{{ p.data.pct_within_1d }}%</div></div>
            <div class="metric-item"><div class="l">≤3天到账率</div><div class="v pct ok">{{ p.data.pct_within_3d }}%</div></div>
            <div class="metric-item"><div class="l">≤7天到账率</div><div class="v pct ok">{{ p.data.pct_within_7d }}%</div></div>
            <div class="metric-item"><div class="l">＞15天占比</div><div class="v pct danger">{{ p.data.pct_over_15d }}%</div></div>
            <div class="metric-item" title="退款工单录入 → 订单退费转入钱包的平均耗时：体现客服/财务入钱包的速度">
              <div class="l">工单→入钱包(均)</div>
              <div class="v days">{{ fmtD(p.data.avg_days_1_2) }} 天</div>
            </div>
            <div class="metric-item" title="钱躺在钱包里直到用户主动提现的平均耗时：体现用户提现意愿 / 拖延">
              <div class="l">入钱包→用户提现(均)</div>
              <div class="v days warn">{{ fmtD(p.data.avg_days_2_4) }} 天</div>
            </div>
          </div>
          <el-empty v-else description="暂无已出款数据" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 两个 ECharts 图表：钱包路径耗时分布 + 钱包路径到账率（B 直退均为秒级，对比无意义，已移除） -->
    <el-row :gutter="16" class="chart-row">
      <el-col :lg="14">
        <el-card shadow="never">
          <template #header><div class="card-head-title">钱包路径出款耗时分布（A）</div></template>
          <div ref="barChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :lg="10">
        <el-card shadow="never">
          <template #header><div class="card-head-title">钱包路径到账率（A：≤1 / ≤3 / ≤7 天 与 ＞15天占比）</div></template>
          <div ref="radarChartRef" class="chart"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 每日实际出款两条流堆叠图：当天出款总量 = 当天直接线上退款 + 当天钱包提现(历史工单进钱包后今天才出款) -->
    <el-row :gutter="16" class="chart-row">
      <el-col :lg="24">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <div class="card-head-title">每日实际出款两条流（直退 vs 钱包提现）</div>
              <span class="muted">柱子总高度=当天实际出款总笔数；绿色=当天线上直退、橙色=历史入钱包后今天才被用户提现拿走的笔数</span>
            </div>
          </template>
          <div ref="dailySplitChartRef" class="chart" style="height:360px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 三个维度聚合表 Tab：处理人 / 原因 / 方式 -->
    <el-card shadow="never" class="dim-card">
      <template #header>
        <div class="card-head-title">Top N 维度聚合表（按 count 降序）</div>
      </template>
      <el-tabs v-model="dimTab">
        <el-tab-pane label="按处理人 Top" name="operator">
          <el-table :data="dimData.by_operator" stripe size="small" max-height="420">
            <el-table-column prop="name" label="处理人" min-width="160" fixed />
            <el-table-column prop="count" label="单数" width="90" align="right" sortable />
            <el-table-column prop="count_ratio" label="占比" width="80" align="right" :formatter="fmtPct" />
            <el-table-column prop="amount" label="金额(¥)" width="130" align="right" :formatter="fmtMoney" sortable />
            <el-table-column prop="avg_amount" label="均单(¥)" width="120" align="right" :formatter="fmtMoney" />
            <el-table-column prop="avg_days" label="均耗时(天)" width="120" align="right" sortable :formatter="fmtDcol" />
            <el-table-column prop="median_days" label="中位(天)" width="110" align="right" :formatter="fmtDcol" />
            <el-table-column prop="p95_days" label="P95(天)" width="110" align="right" :formatter="fmtDcol" class-name="col-warn" />
            <el-table-column prop="max_days" label="最慢(天)" width="110" align="right" class-name="col-danger" sortable :formatter="fmtDcol" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="按退款原因 Top" name="reason">
          <el-table :data="dimData.by_reason" stripe size="small" max-height="420">
            <el-table-column prop="name" label="退款原因" min-width="220" fixed show-overflow-tooltip />
            <el-table-column prop="count" label="单数" width="90" align="right" sortable />
            <el-table-column prop="count_ratio" label="占比" width="80" align="right" :formatter="fmtPct" />
            <el-table-column prop="amount" label="金额(¥)" width="130" align="right" :formatter="fmtMoney" sortable />
            <el-table-column prop="avg_days" label="均耗时(天)" width="120" align="right" sortable :formatter="fmtDcol" />
            <el-table-column prop="median_days" label="中位(天)" width="110" align="right" :formatter="fmtDcol" />
            <el-table-column prop="p95_days" label="P95(天)" width="110" align="right" :formatter="fmtDcol" class-name="col-warn" />
            <el-table-column prop="max_days" label="最慢(天)" width="110" align="right" class-name="col-danger" sortable :formatter="fmtDcol" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="按退款方式 Top" name="method">
          <el-table :data="dimData.by_method" stripe size="small" max-height="420">
            <el-table-column prop="name" label="退款方式" min-width="220" fixed show-overflow-tooltip />
            <el-table-column prop="count" label="单数" width="90" align="right" sortable />
            <el-table-column prop="count_ratio" label="占比" width="80" align="right" :formatter="fmtPct" />
            <el-table-column prop="amount" label="金额(¥)" width="130" align="right" :formatter="fmtMoney" sortable />
            <el-table-column prop="avg_days" label="均耗时(天)" width="120" align="right" sortable :formatter="fmtDcol" />
            <el-table-column prop="median_days" label="中位(天)" width="110" align="right" :formatter="fmtDcol" />
            <el-table-column prop="p95_days" label="P95(天)" width="110" align="right" :formatter="fmtDcol" class-name="col-warn" />
            <el-table-column prop="max_days" label="最慢(天)" width="110" align="right" class-name="col-danger" sortable :formatter="fmtDcol" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 明细表：支持排序下拉 + 分页 -->
    <el-card shadow="never" class="detail-card">
      <template #header>
        <div class="card-head">
          <div class="card-head-title">出款明细（默认最慢单在最前，方便快速发现卡壳问题）</div>
          <el-form :inline="true" size="small" label-width="auto">
            <el-form-item label="明细排序">
              <el-select v-model="detailSort" @change="loadDetail" style="width:180px">
                <el-option label="耗时 降序（最慢先）" value="days_desc" />
                <el-option label="耗时 升序（最快先）" value="days_asc" />
                <el-option label="金额 降序" value="amount_desc" />
                <el-option label="发起时间 降序" value="d1_desc" />
              </el-select>
            </el-form-item>
            <el-form-item label="共">
              <span class="muted">{{ detailTotal.toLocaleString() }} 条</span>
            </el-form-item>
          </el-form>
        </div>
      </template>
      <el-table :data="detailList" stripe size="small" border max-height="640">
        <el-table-column label="路径" width="70" align="center" fixed>
          <template #default="{ row }">
            <el-tag v-if="row.path==='A'" type="warning" effect="plain" size="small">A钱包</el-tag>
            <el-tag v-else-if="row.path==='B'" type="success" effect="plain" size="small">B直退</el-tag>
            <el-tag v-else type="info" effect="plain" size="small">X未知</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="refund_no" label="退款单号" width="160" fixed show-overflow-tooltip />
        <el-table-column prop="device_sn" label="设备SN" width="140" show-overflow-tooltip />
        <el-table-column prop="refund_method" label="退款方式" min-width="140" show-overflow-tooltip />
        <el-table-column prop="refund_reason" label="退款原因" min-width="150" show-overflow-tooltip />
        <el-table-column prop="operator" label="处理人" width="120" show-overflow-tooltip />
        <el-table-column prop="refund_amount" label="金额(¥)" width="110" align="right" :formatter="fmtMoney" />
        <el-table-column
          prop="days_1_4"
          label="发起→到账(天)"
          width="130" align="right" sortable
          :cell-class-name="daysCellCls"
          :formatter="fmtDcol"
        />
        <el-table-column prop="days_1_2" label="发起→入钱包" width="120" align="right" :formatter="fmtDcol" />
        <el-table-column prop="days_2_4" label="入钱包→提现" width="130" align="right" :formatter="fmtDcol" />
        <el-table-column prop="d1_refund_created" label="发起时间(D1)" width="160" show-overflow-tooltip />
        <el-table-column prop="d2_wallet_in" label="入钱包(D2)" width="160" show-overflow-tooltip />
        <el-table-column prop="d4_actual_cashout" label="实际出款(D4)" width="160" show-overflow-tooltip />
        <el-table-column prop="platform_order_no" label="平台单号" width="160" show-overflow-tooltip />
        <el-table-column prop="withdraw_id" label="提现ID" width="100" show-overflow-tooltip />
      </el-table>
      <div class="pag-wrap">
        <el-pagination
          v-model:current-page="detailPage"
          v-model:page-size="detailPageSize"
          :total="detailTotal"
          :page-sizes="[30, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="loadDetail"
          @size-change="detailPage=1;loadDetail()"
        />
      </div>
    </el-card>

    <div class="tips">
      <el-alert
        type="info" :closable="false" show-icon
        title="口径说明"
        description="A路径（转入提现账户）= 发起退款（D1）→ 订单退费转入钱包（D2，可能为空）→ 用户提现实际出款（D4，真正现金到账）；B路径（微信/支付宝/银行卡直退）= 发起退款（D1）→ 退费单更新完成（D4，以接口回调/审核完成时刻为现金真实到账近似）。到账率指对应分档内的退款单占该路径总数百分比。钱包沉淀 = 已发起转入提现账户、但仍未匹配到用户提现操作的退款单，金额仍躺在提现钱包中。"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { refundWithdrawal as rwApi } from '@/api/analytics'

const today = new Date()
const last30 = new Date(today.getTime() - 30 * 86400000)
const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`

const filters = reactive({
  range: [fmt(last30), fmt(today)],
  path: 'ALL',
  slowOnly: false,
})

const dimTab = ref('operator')
const detailSort = ref('days_desc')
const detailPage = ref(1)
const detailPageSize = ref(50)
const detailTotal = ref(0)
const detailList = ref([])

const dateShortcuts = [
  { text: '最近7天', value: () => { const e = new Date(); const s = new Date(e - 6*864e5); return [s, e] } },
  { text: '最近30天', value: () => { const e = new Date(); const s = new Date(e - 29*864e5); return [s, e] } },
  { text: '最近90天', value: () => { const e = new Date(); const s = new Date(e - 89*864e5); return [s, e] } },
  { text: '今年至今', value: () => { const e = new Date(); const s = new Date(e.getFullYear(), 0, 1); return [s, e] } },
]

const overview = ref({ scope: {}, paths: {}, wallet_sleep: {}, cashout_split: {} })
const distData = ref([])
const dailySplit = ref([])
const dimData = ref({ by_operator: [], by_reason: [], by_method: [] })

const topKpis = computed(() => {
  const o = overview.value?.paths?.AB_merged || {}
  const ws = overview.value?.wallet_sleep || {}
  const a = overview.value?.paths?.A_wallet || {}
  const b = overview.value?.paths?.B_direct || {}
  return [
    { label: '出款总单量', value: (o.count || 0).toLocaleString(), sub: '笔 (A+B 已完成出款)', type: 'primary' },
    { label: '出款总金额', value: `¥ ${(o.amount || 0).toLocaleString()}`, sub: '已完成现金出款 (元)', type: 'primary' },
    { label: '全局平均耗时', value: fmtD(o.avg_days), sub: `中位 ${fmtD(o.median_days)} / P95 ${fmtD(o.p95_days)} 天`, type: 'primary' },
    { label: '≤3天到账率', value: `${o.pct_within_3d ?? 0}%`, sub: `≤1天 ${o.pct_within_1d ?? 0}% / ≤7天 ${o.pct_within_7d ?? 0}%`, type: 'ok' },
    { label: 'A路径(钱包)单量', value: (a.count || 0).toLocaleString(), sub: `均耗时 ${fmtD(a.avg_days)} 天`, type: 'warning' },
    { label: 'B路径(直退)单量', value: (b.count || 0).toLocaleString(), sub: `均耗时 ${fmtD(b.avg_days)} 天`, type: 'success' },
    { label: '钱包沉淀(未提现)', value: (ws.still_in_wallet_count || 0).toLocaleString() + ' 笔', sub: `占转入 ${ws.still_in_wallet_pct ?? 0}%`, type: 'danger' },
    { label: '钱包沉淀金额', value: `¥ ${(ws.still_in_wallet_amount || 0).toLocaleString()}`, sub: '仍躺在提现钱包中', type: 'danger' },
  ]
})

const cashoutSplitCards = computed(() => {
  const sp = overview.value?.cashout_split || {}
  const dr = sp.direct_refund || {}      // B流: 本窗口内直接线上退款 (今天直退的)
  const ww = sp.wallet_withdrawal || {}  // A流: 之前在钱包里, 本窗口内才被用户提现拿走的
  const tc = sp.total_count || 0
  const ta = sp.total_amount || 0
  return [
    {
      label: '本窗口实际出款合计',
      value: tc.toLocaleString() + ' 笔',
      sub: `总金额 ¥ ${Number(ta).toLocaleString()}<br>（筛选日期=实际出款D4，不再是退款发起D1）`,
      type: 'primary',
    },
    {
      label: 'B 流：直接线上退款出款',
      value: `${(dr.count || 0).toLocaleString()} 笔`,
      sub: `占当天出款 <b>${dr.count_ratio ?? 0}%</b><br>金额 ¥ ${Number(dr.amount || 0).toLocaleString()}<br>（今天线上接口直接退出去的单）`,
      type: 'ok',
    },
    {
      label: 'A 流：钱包历史沉淀·今日提现出款',
      value: `${(ww.count || 0).toLocaleString()} 笔`,
      sub: `占当天出款 <b>${ww.count_ratio ?? 0}%</b><br>金额 ¥ ${Number(ww.amount || 0).toLocaleString()}<br>（之前就进了钱包，<b>今天才被用户提现拿走</b>的单）<br>入钱包均耗 ${fmtD(ww.avg_days_1_2)} · 用户提现拖延 ${fmtD(ww.avg_days_2_4)} 天`,
      type: 'warning',
    },
  ]
})

const pathMetrics = computed(() => [
  { key: 'A', title: 'A 路径：转入提现账户 → 用户提现', desc: '真实现金出款时刻 = 用户提现 op_time（实际到账给用户的时刻），含两个子阶段：入钱包速度 + 用户提现拖延', data: overview.value?.paths?.A_wallet || {} },
])

function fmtD(v) { return v == null || isNaN(v) ? '-' : (v < 1 ? (Number(v*24).toFixed(1)) + 'h' : Number(v).toFixed(2)) }
function fmtDcol(_r,_c,v){ return fmtD(v) }
function fmtMoney(_r,_c,v){ return v == null ? '-' : '¥ ' + Number(v).toLocaleString() }
function fmtPct(_r,_c,v){ return v == null ? '-' : v + '%' }
function daysCellCls({ row }) {
  const d = row?.days_1_4
  if (d == null) return ''
  if (d > 15) return 'cell-danger'
  if (d > 7) return 'cell-warn'
  if (d <= 1) return 'cell-ok'
  return ''
}

const barChartRef = ref(null)
const radarChartRef = ref(null)
const dailySplitChartRef = ref(null)
let barChart = null
let radarChart = null
let dailySplitChart = null

function renderBar() {
  if (!barChartRef.value) return
  barChart = barChart || echarts.init(barChartRef.value)
  const bins = distData.value.map(r => r.bin)
  barChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => v.toLocaleString() },
    legend: { data: ['A-钱包出款(笔)'] },
    grid: { left: 40, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'category', data: bins, axisLabel: { interval: 0, rotate: 0 } },
    yAxis: { type: 'value' },
    series: [
      { name: 'A-钱包出款(笔)', type: 'bar', data: distData.value.map(r => r.wallet_count || 0), itemStyle: { color: '#E6A23C' },
        label: { show: true, position: 'top' } },
    ]
  })
}

function renderRadar() {
  if (!radarChartRef.value) return
  radarChart = radarChart || echarts.init(radarChartRef.value)
  const a = overview.value?.paths?.A_wallet || {}
  radarChart.setOption({
    tooltip: { valueFormatter: v => (v == null ? '-' : v + '%') },
    legend: { data: ['A-钱包到账率'], bottom: 0 },
    grid: { left: 40, right: 20, top: 30, bottom: 60 },
    xAxis: {
      type: 'category',
      data: ['≤1天到账率', '≤3天到账率', '≤7天到账率', '>15天占比(反向)'],
      axisLabel: { interval: 0 },
    },
    yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    series: [
      { name: 'A-钱包到账率', type: 'bar', data: [
        a.pct_within_1d ?? 0, a.pct_within_3d ?? 0, a.pct_within_7d ?? 0, a.pct_over_15d ?? 0,
      ], itemStyle: { color: '#E6A23C' }, barWidth: 40,
        label: { show: true, position: 'top', formatter: '{c}%' } },
    ]
  })
}

function renderDailySplit() {
  if (!dailySplitChartRef.value) return
  dailySplitChart = dailySplitChart || echarts.init(dailySplitChartRef.value)
  const rows = dailySplit.value || []
  const dates = rows.map(r => r.date)
  dailySplitChart.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const row = rows[params[0].dataIndex] || {}
        const lines = [`<b>${row.date}</b> 出款合计 <b>${row.total_count || 0} 笔</b> / ¥ ${Number(row.total_amount || 0).toLocaleString()}`]
        params.forEach(p => {
          lines.push(`${p.marker}${p.seriesName}: ${Number(p.value || 0).toLocaleString()} 笔`)
        })
        if (row.wallet_avg_days_1_2 != null || row.wallet_avg_days_2_4 != null) {
          lines.push(`<span style="color:#909399;font-size:12px;">钱包入款平均: ${row.wallet_avg_days_1_2 ?? '-'} 天 · 用户提现拖延平均: ${row.wallet_avg_days_2_4 ?? '-'} 天</span>`)
        }
        return lines.join('<br>')
      }
    },
    legend: { data: ['B-直接线上退款', 'A-钱包沉淀今日提现'] },
    grid: { left: 50, right: 20, top: 50, bottom: 60 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { interval: 0, rotate: dates.length > 20 ? 45 : 0, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      axisLabel: { formatter: '{value} 笔' },
    },
    series: [
      {
        name: 'B-直接线上退款',
        type: 'bar',
        stack: 'cashout',
        data: rows.map(r => r.direct_count || 0),
        itemStyle: { color: '#67C23A' },
        emphasis: { focus: 'series' },
        label: { show: dates.length <= 31, position: 'inside', color: '#fff', formatter: p => p.value || '' },
      },
      {
        name: 'A-钱包沉淀今日提现',
        type: 'bar',
        stack: 'cashout',
        data: rows.map(r => r.wallet_count || 0),
        itemStyle: { color: '#E6A23C' },
        emphasis: { focus: 'series' },
        label: { show: dates.length <= 31, position: 'inside', color: '#fff', formatter: p => p.value || '' },
      },
    ]
  })
}

const resizeHandler = () => {
  barChart && barChart.resize()
  radarChart && radarChart.resize()
  dailySplitChart && dailySplitChart.resize()
}

async function loadAll() {
  const params = {}
  if (filters.range && filters.range.length === 2) {
    params.start = filters.range[0] + 'T00:00:00'
    params.end = filters.range[1] + 'T23:59:59'
  }
  try {
    const [o, d, ds, dim] = await Promise.all([
      rwApi.overview(params),
      rwApi.timeDistribution(params),
      rwApi.dailySplit(params),
      rwApi.dimensions({ ...params, topn: 30 }),
    ])
    overview.value = o?.data || {}
    distData.value = d?.data || []
    dailySplit.value = ds?.data || []
    dimData.value = dim?.data || { by_operator: [], by_reason: [], by_method: [] }
    await nextTick()
    renderBar()
    renderRadar()
    renderDailySplit()
    detailPage.value = 1
    loadDetail()
  } catch (e) {
    ElMessage.error('看板数据加载失败: ' + (e?.message || e))
  }
}

async function loadDetail() {
  const params = {}
  if (filters.range && filters.range.length === 2) {
    params.start = filters.range[0] + 'T00:00:00'
    params.end = filters.range[1] + 'T23:59:59'
  }
  params.sort = detailSort.value
  params.page = detailPage.value
  params.page_size = detailPageSize.value
  params.slow_only = filters.slowOnly
  params.path = filters.path
  try {
    const res = await rwApi.detail(params)
    detailTotal.value = res?.data?.total || 0
    detailList.value = res?.data?.list || []
  } catch (e) {
    ElMessage.error('明细加载失败: ' + (e?.message || e))
  }
}

onMounted(() => {
  window.addEventListener('resize', resizeHandler)
  loadAll()
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeHandler)
  barChart && barChart.dispose()
  radarChart && radarChart.dispose()
  dailySplitChart && dailySplitChart.dispose()
})
</script>

<style scoped>
.refund-withdrawal-board { padding: 12px 16px; background: #f5f7fa; min-height: 100%; }
.filter-card { margin-bottom: 14px; }
.kpi-row, .metric-row, .chart-row { margin-bottom: 16px; }
.kpi-card { border-radius: 8px; }
.kpi-card .kpi-label { font-size: 13px; color: #909399; }
.kpi-card .kpi-value { font-size: 24px; font-weight: 600; margin: 8px 0 4px; letter-spacing: .3px; }
.kpi-card .kpi-sub { font-size: 12px; color: #909399; line-height: 1.5; }
.kpi-card.primary .kpi-value { color: #409EFF; }
.kpi-card.ok .kpi-value { color: #67C23A; }
.kpi-card.warning .kpi-value { color: #E6A23C; }
.kpi-card.danger .kpi-value { color: #F56C6C; }
.metric-card .card-head { display: flex; align-items: center; gap: 10px; }
.metric-card .card-head .path-desc { font-size: 12px; color: #909399; }
.metric-card .path-tag { padding: 3px 8px; border-radius: 4px; font-weight: 600; color: #fff; }
.metric-card .path-tag.A { background: #E6A23C; }
.metric-card .path-tag.B { background: #67C23A; }
.metric-card .metric-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px 16px; }
.metric-card .metric-grid .metric-item { padding: 8px 10px; background: #fafbfc; border-radius: 6px; }
.metric-card .metric-grid .l { font-size: 12px; color: #909399; }
.metric-card .metric-grid .v { font-size: 16px; font-weight: 600; color: #303133; margin-top: 4px; }
.metric-card .metric-grid .v.days.warn { color: #E6A23C; }
.metric-card .metric-grid .v.pct.ok { color: #67C23A; }
.metric-card .metric-grid .v.pct.danger { color: #F56C6C; }
.chart { width: 100%; height: 340px; }
.card-head-title { font-weight: 600; font-size: 15px; }
.card-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.dim-card, .detail-card { margin-bottom: 16px; }
.pag-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }
.muted { color: #909399; }
.tips { margin-top: 18px; }
:deep(.cell-danger) { color: #F56C6C; font-weight: 600; }
:deep(.cell-warn) { color: #E6A23C; }
:deep(.cell-ok) { color: #67C23A; }
:deep(.col-warn .cell) { color: #E6A23C !important; }
:deep(.col-danger .cell) { color: #F56C6C !important; }
</style>
