<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">现金流分析</div>
      <el-radio-group v-model="range" size="small" @change="loadAll">
        <el-radio-button label="7">近7天</el-radio-button>
        <el-radio-button label="30">近30天</el-radio-button>
        <el-radio-button label="90">近90天</el-radio-button>
      </el-radio-group>
    </div>

    <el-card class="mb-16" header="真实现金流出趋势(商户号退款)">
      <BaseChart :option="cashTrendOption" height="320px" />
    </el-card>

    <el-card class="mb-16" header="退回钱包 vs 实际提现(暴露提现缺口)">
      <BaseChart :option="compareOption" height="320px" />
    </el-card>

    <el-row :gutter="16">
      <el-col :span="14">
        <el-card header="商户号对账(按商户号)">
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
import dayjs from 'dayjs'

const range = ref('30')
const cashTrend = ref([])
const compare = ref([])
const merchants = ref([])
const wallets = ref([])

function dateRange() {
  const end = dayjs()
  const start = end.subtract(Number(range.value), 'day')
  return { start: start.format('YYYY-MM-DD'), end: end.format('YYYY-MM-DD') }
}
function fmt(v) { return v == null ? '-' : Number(v).toFixed(2) }

const cashTrendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['现金流出', '笔数'] },
  grid: { left: 50, right: 50, bottom: 30, top: 40 },
  xAxis: { type: 'category', data: cashTrend.value.map((d) => d.date) },
  yAxis: [{ type: 'value', name: '金额(元)' }, { type: 'value', name: '笔数' }],
  series: [
    { name: '现金流出', type: 'line', smooth: true, areaStyle: { color: '#f56c6c33' }, itemStyle: { color: '#f56c6c' }, data: cashTrend.value.map((d) => d.value) },
    { name: '笔数', type: 'bar', yAxisIndex: 1, data: cashTrend.value.map((d) => d.count) },
  ],
}))

const compareOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['退回钱包', '实际提现'] },
  grid: { left: 50, right: 30, bottom: 30, top: 40 },
  xAxis: { type: 'category', data: compare.value.map((d) => d.date) },
  yAxis: { type: 'value', name: '金额(元)' },
  series: [
    { name: '退回钱包', type: 'bar', data: compare.value.map((d) => d.wallet_in) },
    { name: '实际提现', type: 'bar', data: compare.value.map((d) => d.withdraw) },
  ],
}))

async function loadAll() {
  const r = dateRange()
  try {
    const [t, c, m, w] = await Promise.all([
      cash.trend(r),
      cash.walletVsWithdraw(r),
      cash.merchant(),
      cash.walletBalance({ limit: 20 }),
    ])
    cashTrend.value = t.data
    compare.value = c.data
    merchants.value = m.data
    wallets.value = w.data
  } catch (e) {}
}

onMounted(loadAll)
</script>
