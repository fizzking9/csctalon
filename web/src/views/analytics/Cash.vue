<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">现金流分析</div>
      <div class="filter-bar">
        <el-radio-group v-model="range" size="small" @change="loadAll">
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="grain" size="small" @change="loadAll">
          <el-radio-button label="day">按日</el-radio-button>
          <el-radio-button label="week">按周</el-radio-button>
          <el-radio-button label="month">按月</el-radio-button>
        </el-radio-group>
      </div>
    </div>

    <!-- 现金流对账卡片:商户号实际 vs 已登记估算 -->
    <el-row :gutter="16" class="mb-16">
      <el-col :span="6">
        <el-card shadow="hover" body-style="padding: 16px 20px">
          <div class="stat-label">商户号现金流(实际)</div>
          <div class="stat-value" style="color: #f56c6c">¥ {{ fmt(breakdown.merchant_cash) }}</div>
          <div class="stat-sub">微信侧真实扣款</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" body-style="padding: 16px 20px">
          <div class="stat-label">已登记估算现金流</div>
          <div class="stat-value" style="color: #909399">¥ {{ fmt(breakdown.total) }}</div>
          <div class="stat-sub">四路经营合计（A+B+C+D）</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" body-style="padding: 16px 20px">
          <div class="stat-label">直接退款(线上+人工)</div>
          <div class="stat-value" style="color: #e6a23c">¥ {{ fmt(breakdown.direct_refund) }}</div>
          <div class="stat-sub">客服在线操作,钱直接退回用户</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" body-style="padding: 16px 20px">
          <div class="stat-label">用户提现</div>
          <div class="stat-value" style="color: #409eff">¥ {{ fmt(breakdown.user_withdraw) }}</div>
          <div class="stat-sub">退到钱包后用户再提现</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 偏差提示:实际-估算=未登记商户侧退款 -->
    <el-alert
      class="mb-16"
      :type="diffType"
      :closable="false"
      show-icon
    >
      <template #title>
        偏差(实际 - 估算)= <b>¥ {{ fmt(breakdown.diff) }}</b>
        <span class="diff-hint"> — 商户号现金流不与经营数据累加,差额 = 商户侧已扣款但尚未登记到四路的退款(商户号流水同步后对账更完整)</span>
      </template>
    </el-alert>

    <el-card class="mb-16" header="现金流趋势(估算堆叠 vs 商户号实际)">
      <BaseChart :option="cashTrendOption" height="320px" />
    </el-card>

    <el-card class="mb-16" header="退回钱包 vs 实际提现(暴露提现缺口)">
      <BaseChart :option="compareOption" height="320px" />
    </el-card>

    <el-row :gutter="16">
      <el-col :span="14">
        <el-card header="商户号现金流对账(按商户号)">
          <el-table :data="merchants" border stripe size="small" max-height="320">
            <el-table-column prop="merchant_id" label="商户号" width="160" />
            <el-table-column label="退款金额" width="120">
              <template #default="{ row }">{{ fmt(row.refund_amount) }}</template>
            </el-table-column>
            <el-table-column prop="count" label="笔数" width="80" />
            <el-table-column label="手续费" width="100">
              <template #default="{ row }">{{ fmt(row.fee) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card header="钱包余额分布(按账户)">
          <el-table :data="wallets" border stripe size="small" max-height="320">
            <el-table-column prop="account_id" label="账户ID" show-overflow-tooltip />
            <el-table-column label="余额" width="100">
              <template #default="{ row }">{{ fmt(row.balance) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import BaseChart from '@/components/BaseChart.vue'
import { cash } from '@/api/analytics'
import { formatMoney, formatAxisWan } from '@/utils/format'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
dayjs.extend(isoWeek)

const range = ref('30')
const grain = ref('day') // 趋势粒度: 日/周/月; 前端对后端按日数据做汇聚
const cashTrend = ref([])
const compare = ref([])
const merchants = ref([])
const wallets = ref([])
const breakdown = ref({ direct_refund: 0, user_withdraw: 0, total: 0, merchant_cash: 0, diff: 0 })

function dateRange() {
  const end = dayjs()
  const start = end.subtract(Number(range.value), 'day')
  return { start: start.format('YYYY-MM-DD'), end: end.format('YYYY-MM-DD') }
}
// 金额格式化:≥1万 用"万"为单位,2位小数
function fmt(v) { return formatMoney(v) }

// 把按日序列按周/月聚合成带标签的序列(前端纯计算, 后端无改动)
// rows: 形如 {date, ...数值字段} 的数组; 返回 { labels, keys, rows: [{label, key, ...聚合后数值字段}] }
function aggregateByGrain(rows, g) {
  if (g === 'day') {
    return {
      labels: rows.map(r => r.date.slice(5)),
      keys: rows.map(r => r.date),
      rows: rows.map(r => ({ label: r.date.slice(5), key: r.date, ...r })),
    }
  }
  const groups = new Map()
  rows.forEach(r => {
    const dt = dayjs(r.date)
    if (!dt.isValid()) return
    let key, label
    if (g === 'week') {
      const ws = dt.startOf('isoWeek')
      const we = ws.add(6, 'day')
      key = ws.format('YYYY-MM-DD')
      label = `${ws.format('MM-DD')}~${we.format('MM-DD')}`
    } else {
      key = dt.format('YYYY-MM')
      label = dt.format('YYYY-MM')
    }
    const g0 = groups.get(key) || { label, key, count: 0 }
    // 数值字段(除 date/label/key/count 外)做求和; count 字段也求和(若无则跳过)
    Object.entries(r).forEach(([k, v]) => {
      if (k === 'date' || k === 'label' || k === 'key') return
      if (typeof v !== 'number') return
      g0[k] = (g0[k] || 0) + v
    })
    groups.set(key, g0)
  })
  const sorted = [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
  return {
    labels: sorted.map(([, g0]) => g0.label),
    keys: sorted.map(([k]) => k),
    rows: sorted.map(([, g0]) => g0),
  }
}

// 偏差类型:正偏差(实际>估算,有未登记退款)用 warning;负偏差(估算>实际)用 error
const diffType = computed(() => {
  const d = Number(breakdown.value.diff || 0)
  if (d > 0) return 'warning'
  if (d < 0) return 'error'
  return 'success'
})

const cashTrendOption = computed(() => {
  const agg = aggregateByGrain(cashTrend.value, grain.value)
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const date = params[0]?.axisValue || ''
        const lines = [date]
        params.forEach((p) => {
          lines.push(`${p.marker} ${p.seriesName}: ¥${formatMoney(p.value)}`)
        })
        return lines.join('<br/>')
      },
    },
    legend: { data: ['直接退款', '用户提现', '商户号现金流(实际)'] },
    grid: { left: 60, right: 30, bottom: 30, top: 40 },
    xAxis: { type: 'category', data: agg.labels, axisLabel: { rotate: agg.labels.length > 12 ? 30 : 0 } },
    yAxis: { type: 'value', name: '金额(万元)', axisLabel: { formatter: formatAxisWan } },
    series: [
      {
        name: '直接退款',
        type: 'bar',
        stack: '估算',
        itemStyle: { color: '#e6a23c' },
        data: agg.rows.map((d) => d.direct || 0),
      },
      {
        name: '用户提现',
        type: 'bar',
        stack: '估算',
        itemStyle: { color: '#409eff' },
        data: agg.rows.map((d) => d.withdraw || 0),
      },
      {
        name: '商户号现金流(实际)',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        itemStyle: { color: '#f56c6c' },
        lineStyle: { width: 2.5 },
        data: agg.rows.map((d) => d.merchant || 0),
      },
    ],
  }
})

const compareOption = computed(() => {
  const agg = aggregateByGrain(compare.value, grain.value)
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const date = params[0]?.axisValue || ''
        const lines = [date]
        params.forEach((p) => {
          lines.push(`${p.marker} ${p.seriesName}: ¥${formatMoney(p.value)}`)
        })
        return lines.join('<br/>')
      },
    },
    legend: { data: ['退回钱包', '实际提现'] },
    grid: { left: 60, right: 30, bottom: 30, top: 40 },
    xAxis: { type: 'category', data: agg.labels, axisLabel: { rotate: agg.labels.length > 12 ? 30 : 0 } },
    yAxis: { type: 'value', name: '金额(万元)', axisLabel: { formatter: formatAxisWan } },
    series: [
      { name: '退回钱包', type: 'bar', data: agg.rows.map((d) => d.wallet_in || 0) },
      { name: '实际提现', type: 'bar', data: agg.rows.map((d) => d.withdraw || 0) },
    ],
  }
})

async function loadAll() {
  const r = dateRange()
  try {
    const [t, b, c, m, w] = await Promise.all([
      cash.trend(r),
      cash.breakdown(),
      cash.walletVsWithdraw(r),
      cash.merchant(),
      cash.walletBalance({ limit: 20 }),
    ])
    cashTrend.value = t.data
    breakdown.value = b.data
    compare.value = c.data
    merchants.value = m.data
    wallets.value = w.data
  } catch (e) {}
}

onMounted(loadAll)
</script>

<style scoped>
.stat-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 4px;
}
.stat-value {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.4;
}
.stat-sub {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 4px;
}
.diff-hint {
  font-size: 12px;
  color: #909399;
}
</style>
