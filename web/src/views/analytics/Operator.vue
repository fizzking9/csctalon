<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">人员操作分析</div>
      <el-radio-group v-model="range" size="small" @change="loadAll">
        <el-radio-button label="7">近7天</el-radio-button>
        <el-radio-button label="30">近30天</el-radio-button>
        <el-radio-button label="90">近90天</el-radio-button>
      </el-radio-group>
    </div>

    <el-card class="mb-16" header="工单处理量与退款金额排名(按处理人)">
      <BaseChart :option="ticketOption" height="340px" />
    </el-card>

    <el-row :gutter="16">
      <el-col :span="12">
        <el-card header="审核统计(按审核人)">
          <el-table :data="audit" border stripe size="small" max-height="320">
            <el-table-column prop="operator" label="审核人" />
            <el-table-column prop="audit_count" label="审核笔数" width="100" />
            <el-table-column label="审核金额" width="120">
              <template #default="{ row }">{{ fmt(row.audit_amount) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card header="退费操作统计(排除sys/system)">
          <el-table :data="refund" border stripe size="small" max-height="320">
            <el-table-column prop="operator" label="操作人" />
            <el-table-column prop="op_count" label="操作笔数" width="100" />
            <el-table-column label="退费金额" width="120">
              <template #default="{ row }">{{ fmt(row.refund_amount) }}</template>
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
import { operator } from '@/api/analytics'
import dayjs from 'dayjs'

const range = ref('30')
const ticket = ref([])
const audit = ref([])
const refund = ref([])

function dateRange() {
  const end = dayjs()
  const start = end.subtract(Number(range.value), 'day')
  return { start: start.format('YYYY-MM-DD'), end: end.format('YYYY-MM-DD') }
}
function fmt(v) { return v == null ? '-' : Number(v).toFixed(2) }

const ticketOption = computed(() => {
  const data = [...ticket.value].sort((a, b) => b.ticket_count - a.ticket_count).slice(0, 15)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['工单量', '退款金额'] },
    grid: { left: 120, right: 60, bottom: 30, top: 40 },
    xAxis: [
      { type: 'value', name: '工单量' },
      { type: 'value', name: '金额(元)' },
    ],
    yAxis: { type: 'category', data: data.map((d) => d.operator).reverse() },
    series: [
      { name: '工单量', type: 'bar', data: data.map((d) => d.ticket_count).reverse(), itemStyle: { color: '#409eff' } },
      { name: '退款金额', type: 'bar', xAxisIndex: 1, data: data.map((d) => d.refund_amount).reverse(), itemStyle: { color: '#e6a23c' } },
    ],
  }
})

async function loadAll() {
  const r = dateRange()
  try {
    const [t, a, rf] = await Promise.all([
      operator.ticket(r),
      operator.audit(r),
      operator.refund(r),
    ])
    ticket.value = t.data
    audit.value = a.data
    refund.value = rf.data
  } catch (e) {}
}

onMounted(loadAll)
</script>
