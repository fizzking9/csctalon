<template>
  <div class="device-analysis">
    <!-- 1. 筛选栏 -->
    <section class="section-card">
      <div class="block-title">设备风险分析 · 筛选</div>
      <div class="filter-row">
        <el-select v-model="filters.reason" placeholder="失败原因" size="small" style="width: 220px" @change="onFilterChange">
          <el-option label="全部原因" value="" />
          <el-option v-for="r in REASON_OPTIONS" :key="r" :label="r" :value="r" />
        </el-select>
        <el-select v-model="filters.amountRange" placeholder="金额区间" size="small" style="width: 160px" @change="onFilterChange">
          <el-option label="全部金额" value="" />
          <el-option label="<100元" value="lt100" />
          <el-option label="100-500元" value="100-500" />
          <el-option label="500-1000元" value="500-1000" />
          <el-option label="1000+元" value="gt1000" />
        </el-select>
        <el-select v-model="filters.receptionMin" placeholder="接待次数" size="small" style="width: 140px" @change="onFilterChange">
          <el-option label="全部次数" value="" />
          <el-option label="≥2次" :value="2" />
          <el-option label="≥3次" :value="3" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" size="small" type="primary" @click="loadData">刷新</el-button>
      </div>
    </section>

    <!-- 2. 设备风险分布 KPI -->
    <section class="section-card" v-loading="loading">
      <div class="block-title">设备风险分布</div>
      <el-row :gutter="12">
        <el-col :xs="12" :sm="12" :md="6">
          <div class="risk-card risk-red">
            <div class="risk-label">复退款设备数</div>
            <div class="risk-sub">周期内接待≥2次</div>
            <div class="risk-value">{{ riskSummary.repeat_refund_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <div class="risk-card risk-orange">
            <div class="risk-label">跨人员接待设备数</div>
            <div class="risk-sub">≥2名客服接待</div>
            <div class="risk-value">{{ riskSummary.cross_person_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <div class="risk-card risk-purple">
            <div class="risk-label">高金额设备数</div>
            <div class="risk-sub">退款 &gt; 1000元</div>
            <div class="risk-value">{{ riskSummary.high_amount_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <div class="risk-card risk-gray">
            <div class="risk-label">总失败设备数</div>
            <div class="risk-sub">T+7 窗口内失败</div>
            <div class="risk-value">{{ riskSummary.total_failed_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
      </el-row>
    </section>

    <!-- 3. Top20 高风险设备表 -->
    <section class="section-card" v-loading="loading">
      <div class="flex-between mb-12">
        <div class="block-title">Top20 高风险设备</div>
        <el-tag size="small" type="info" effect="plain">默认按退款金额倒序</el-tag>
      </div>
      <el-table
        :data="topDevices"
        border
        stripe
        size="small"
        max-height="520"
        :default-sort="{ prop: 'refund_amount', order: 'descending' }"
      >
        <el-table-column prop="device_sn" label="设备SN" min-width="140" fixed>
          <template #default="{ row }">
            <span style="font-weight: 600">{{ row.device_sn }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="reception_count" label="接待次数" width="90" align="right" sortable />
        <el-table-column prop="receptionist_count" label="接待人员数" width="100" align="right" sortable />
        <el-table-column label="接待人员" min-width="160">
          <template #default="{ row }">
            <span v-if="row.receptionists && row.receptionists.length">{{ row.receptionists.join(', ') }}</span>
            <span v-else style="color: #9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="fail_reason" label="失败原因" width="160">
          <template #default="{ row }">
            <el-tag v-if="row.fail_reason" size="small" :type="reasonTagType(row.fail_reason)" effect="light">{{ row.fail_reason }}</el-tag>
            <span v-else style="color: #9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="refund_amount" label="退款金额" width="120" align="right" sortable>
          <template #default="{ row }">
            <span :style="{ color: Number(row.refund_amount) > 1000 ? '#dc2626' : '#374151', fontWeight: 600 }">¥{{ formatMoney(row.refund_amount) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="refund_time" label="退款时间" width="150">
          <template #default="{ row }">{{ formatTime(row.refund_time) }}</template>
        </el-table-column>
        <el-table-column prop="final_status" label="最终状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.final_status" size="small" :type="statusTagType(row.final_status)" effect="plain">{{ row.final_status }}</el-tag>
            <span v-else style="color: #9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row.device_sn)">详情</el-button>
            <el-button link type="primary" size="small" @click="openAudit(row.device_sn)">时序</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !topDevices.length" description="暂无高风险设备数据" />
    </section>

    <!-- 4. 接待→流失时差分布 -->
    <section class="section-card">
      <div class="block-title">接待 → 流失时差分布</div>
      <BaseChart
        v-if="lossTimeOption && lossTimeOption.series && lossTimeOption.series.length"
        :option="lossTimeOption"
        height="320px"
      />
      <el-empty v-else description="暂无时差分布数据" />
    </section>

    <!-- 5. 设备接待次数分布 -->
    <section class="section-card">
      <div class="block-title">设备接待次数分布</div>
      <BaseChart
        v-if="receptionCountOption && receptionCountOption.series && receptionCountOption.series.length"
        :option="receptionCountOption"
        height="320px"
      />
      <el-empty v-else description="暂无接待次数分布数据" />
    </section>

    <!-- 6. 设备详情抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      size="60%"
      :title="`设备详情 · ${currentDeviceSn}`"
      direction="rtl"
      destroy-on-close
    >
      <div v-loading="drawerLoading" class="drawer-content">
        <template v-if="detail">
          <!-- 设备画像 -->
          <section class="section-card">
            <div class="block-title">设备画像</div>
            <el-row :gutter="12">
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">SN</span><span class="v">{{ detail.device_sn || '—' }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">型号</span><span class="v">{{ detail.device_type || '—' }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv">
                  <span class="k">最终结果</span>
                  <el-tag v-if="detail.result" size="small" :type="detail.result === 'failed' ? 'danger' : 'success'" effect="dark">
                    {{ detail.result === 'failed' ? '失败' : '成功' }}
                  </el-tag>
                  <span v-else style="color: #9ca3af">—</span>
                </div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">首次接待</span><span class="v">{{ formatTime(detail.first_reception) }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">末次接待</span><span class="v">{{ formatTime(detail.last_reception) }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">接待次数</span><span class="v">{{ detail.reception_count || 0 }} 次</span></div>
              </el-col>
              <el-col :xs="24" :md="12">
                <div class="kv">
                  <span class="k">接待人员</span>
                  <span class="v">{{ (detail.receptionists || []).join(', ') || '—' }}</span>
                </div>
              </el-col>
              <el-col :xs="12" :md="6">
                <div class="kv">
                  <span class="k">失败原因</span>
                  <el-tag v-if="detail.fail_reason" size="small" :type="reasonTagType(detail.fail_reason)" effect="light">{{ detail.fail_reason }}</el-tag>
                  <span v-else style="color: #9ca3af">—</span>
                </div>
              </el-col>
              <el-col :xs="12" :md="6">
                <div class="kv">
                  <span class="k">退款金额</span>
                  <span class="v" style="color: #dc2626; font-weight: 600">¥{{ formatMoney(detail.refund_amount) }}</span>
                </div>
              </el-col>
              <el-col :xs="24" :md="12">
                <div class="kv"><span class="k">退款单号</span><span class="v">{{ detail.refund_no || '—' }}</span></div>
              </el-col>
            </el-row>
          </section>

          <!-- 接待记录时间线 -->
          <section class="section-card">
            <div class="block-title">接待记录时间线</div>
            <el-table :data="sortedTimeline" border stripe size="small" max-height="280">
              <el-table-column prop="time" label="时间" width="160">
                <template #default="{ row }">{{ formatTime(row.time) }}</template>
              </el-table-column>
              <el-table-column prop="operator_name" label="接待人" width="100" />
              <el-table-column prop="problem_type" label="问题类型" width="120" />
              <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip />
              <el-table-column prop="execution_result" label="处理结果" min-width="160" show-overflow-tooltip />
              <el-table-column prop="consult_time" label="接待时长" width="100" align="right">
                <template #default="{ row }">{{ row.consult_time != null ? row.consult_time + 's' : '—' }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!sortedTimeline.length" description="暂无接待记录" />
          </section>

          <!-- 退款记录 -->
          <section class="section-card">
            <div class="block-title">退款记录</div>
            <el-table :data="detail.refund_records || []" border stripe size="small" max-height="280">
              <el-table-column prop="refund_no" label="退款单号" min-width="160" />
              <el-table-column prop="refund_amount" label="金额" width="110" align="right">
                <template #default="{ row }">¥{{ formatMoney(row.refund_amount) }}</template>
              </el-table-column>
              <el-table-column prop="refund_method" label="方式" width="100" />
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag v-if="row.status" size="small" :type="statusTagType(row.status)" effect="plain">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="operator" label="操作人" width="100" />
              <el-table-column prop="auditor" label="审核人" width="100">
                <template #default="{ row }">{{ row.auditor || '—' }}</template>
              </el-table-column>
              <el-table-column prop="created_at_raw" label="时间" width="160">
                <template #default="{ row }">{{ formatTime(row.created_at_raw) }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!detail.refund_records || !detail.refund_records.length" description="暂无退款记录" />
          </section>

          <!-- 关联工单 -->
          <section v-if="detail.tickets && detail.tickets.length" class="section-card">
            <div class="block-title">关联工单</div>
            <el-table :data="detail.tickets" border stripe size="small" max-height="280">
              <el-table-column prop="ticket_no" label="工单号" min-width="140" />
              <el-table-column prop="service_item" label="服务项" min-width="120" />
              <el-table-column prop="service_category" label="类别" width="100" />
              <el-table-column prop="register_time" label="登记时间" width="160">
                <template #default="{ row }">{{ formatTime(row.register_time) }}</template>
              </el-table-column>
              <el-table-column prop="finish_time" label="完成时间" width="160">
                <template #default="{ row }">{{ formatTime(row.finish_time) }}</template>
              </el-table-column>
              <el-table-column prop="handler" label="处理人" width="100" />
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag v-if="row.status" size="small" :type="statusTagType(row.status)" effect="plain">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </section>

          <!-- 钱包提现 -->
          <section v-if="detail.wallet_records && detail.wallet_records.length" class="section-card">
            <div class="block-title">钱包提现</div>
            <el-table :data="detail.wallet_records" border stripe size="small" max-height="280">
              <el-table-column prop="op_time" label="时间" width="160">
                <template #default="{ row }">{{ formatTime(row.op_time) }}</template>
              </el-table-column>
              <el-table-column prop="op_amount" label="金额" width="120" align="right">
                <template #default="{ row }">¥{{ formatMoney(row.op_amount) }}</template>
              </el-table-column>
              <el-table-column prop="op_type" label="操作类型" min-width="120" />
              <el-table-column prop="wallet_id" label="钱包ID" min-width="120" />
            </el-table>
          </section>
        </template>
        <el-empty v-else-if="!drawerLoading" description="暂无详情数据" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { refundDeep } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

// 父组件传入的时间范围
const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
})

// 失败原因 7 大类 (任务说明: 不想用了/充值错误/设备故障/网卡/卡无网络/套餐问题/卡商问题/其他)
const REASON_OPTIONS = [
  '不想用了',
  '充值错误',
  '设备故障',
  '网卡',
  '卡无网络',
  '套餐问题(流量虚、涨价、升级)',
  '卡商问题',
  '其他(填写备注)',
]

// 筛选条件
const filters = reactive({
  reason: '',
  amountRange: '',
  receptionMin: '',
})

// 加载状态
const loading = ref(false)

// 风险摘要 KPI
const riskSummary = ref({
  repeat_refund_devices: 0,
  cross_person_devices: 0,
  high_amount_devices: 0,
  total_failed_devices: 0,
})

// Top20 设备列表
const topDevices = ref([])
// 接待→流失时差分布
const lossTimeDist = ref([])
// 接待次数分布
const receptionCountDist = ref([])

// 金额区间 → { min, max } 映射
function resolveAmountRange(range) {
  switch (range) {
    case 'lt100': return { min: null, max: 100 }
    case '100-500': return { min: 100, max: 500 }
    case '500-1000': return { min: 500, max: 1000 }
    case 'gt1000': return { min: 1000, max: null }
    default: return { min: null, max: null }
  }
}

// 组装请求参数
function buildParams() {
  const { min, max } = resolveAmountRange(filters.amountRange)
  const params = {}
  if (props.start) params.start = props.start
  if (props.end) params.end = props.end
  if (filters.reason) params.reason = filters.reason
  if (min !== null) params.amount_min = min
  if (max !== null) params.amount_max = max
  if (filters.receptionMin !== '' && filters.receptionMin !== null) {
    params.reception_count_min = filters.receptionMin
  }
  return params
}

// 加载设备分析数据
async function loadData() {
  loading.value = true
  try {
    const params = buildParams()
    const res = await refundDeep.deviceAnalysis(params)
    const data = res.data || {}
    riskSummary.value = Object.assign({
      repeat_refund_devices: 0,
      cross_person_devices: 0,
      high_amount_devices: 0,
      total_failed_devices: 0,
    }, data.risk_summary || {})
    topDevices.value = data.top_devices || []
    lossTimeDist.value = data.loss_time_dist || []
    receptionCountDist.value = data.reception_count_dist || []
  } catch (e) {
    riskSummary.value = { repeat_refund_devices: 0, cross_person_devices: 0, high_amount_devices: 0, total_failed_devices: 0 }
    topDevices.value = []
    lossTimeDist.value = []
    receptionCountDist.value = []
  } finally {
    loading.value = false
  }
}

// 筛选条件变化时触发
function onFilterChange() {
  loadData()
}

// 监听 props.start / props.end 变化
watch(() => [props.start, props.end], () => {
  loadData()
})

onMounted(loadData)

// === 失败原因 tag 颜色映射 ===
const REASON_TAG_MAP = {
  '不想用了': 'info',
  '充值错误': 'warning',
  '设备故障': 'danger',
  '网卡': 'primary',
  '卡无网络': 'primary',
  '套餐问题(流量虚、涨价、升级)': 'success',
  '卡商问题': 'warning',
  '其他(填写备注)': 'info',
}
function reasonTagType(reason) {
  return REASON_TAG_MAP[reason] || 'info'
}

// === 退款/工单状态 tag 颜色 ===
function statusTagType(status) {
  if (!status) return 'info'
  if (/已退款|完成|成功|通过/.test(status)) return 'success'
  if (/驳回|失败|拒绝|终止/.test(status)) return 'danger'
  if (/审核|处理中|待|进行/.test(status)) return 'warning'
  return 'info'
}

// === 时间格式化 (ISO → YYYY-MM-DD HH:mm) ===
function formatTime(s) {
  if (!s) return '—'
  const str = String(s).replace('T', ' ').replace('Z', '')
  return str.slice(0, 16)
}

// === 设备详情抽屉 ===
const drawerVisible = ref(false)
const drawerLoading = ref(false)
const currentDeviceSn = ref('')
const detail = ref(null)

// 接待时间线按时间正序排列
const sortedTimeline = computed(() => {
  const list = (detail.value && detail.value.reception_timeline) ? [...detail.value.reception_timeline] : []
  list.sort((a, b) => {
    const ta = a.time ? new Date(a.time).getTime() : 0
    const tb = b.time ? new Date(b.time).getTime() : 0
    return ta - tb
  })
  return list
})

// 打开设备详情抽屉
async function openDetail(sn) {
  currentDeviceSn.value = sn
  drawerVisible.value = true
  drawerLoading.value = true
  detail.value = null
  try {
    const params = { device_sn: sn }
    if (props.start) params.start = props.start
    if (props.end) params.end = props.end
    const res = await refundDeep.deviceDetail(params)
    detail.value = res.data || null
  } catch (e) {
    detail.value = null
  } finally {
    drawerLoading.value = false
  }
}

// 新窗口打开时序审计页
function openAudit(sn) {
  const url = `/audit/device?sn=${encodeURIComponent(sn)}&start=${encodeURIComponent(props.start || '')}&end=${encodeURIComponent(props.end || '')}`
  window.open(url, '_blank')
}

// === 图表: 接待→流失时差分布 (柱状图) ===
const lossTimeOption = computed(() => {
  const list = lossTimeDist.value || []
  if (!list.length) return {}
  const x = list.map(r => r.bucket)
  const snCnt = list.map(r => Number(r.sn_cnt) || 0)
  const pal = ['#60a5fa', '#34d399', '#fbbf24', '#fb923c', '#f87171']
  const barData = snCnt.map((v, i) => ({
    value: v,
    itemStyle: { color: pal[i % pal.length], borderRadius: [4, 4, 0, 0] },
  }))
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = list[i] || {}
        return `<b>${it.bucket}</b><br/>失败SN数: <b>${Number(it.sn_cnt) || 0}</b> 个<br/>涉及金额: ¥${formatMoney(Number(it.amount) || 0)}`
      },
    },
    grid: { left: 56, right: 32, top: 24, bottom: 36 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 12 } },
    yAxis: { type: 'value', name: '失败SN数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
    series: [
      {
        name: '失败SN数',
        type: 'bar',
        data: barData,
        barMaxWidth: 48,
        label: { show: true, position: 'top', fontSize: 11, formatter: '{c}' },
      },
    ],
  }
})

// === 图表: 设备接待次数分布 (双Y轴 柱+线) ===
const receptionCountOption = computed(() => {
  const list = receptionCountDist.value || []
  if (!list.length) return {}
  const x = list.map(r => r.bucket)
  const deviceCnt = list.map(r => Number(r.device_cnt) || 0)
  const failRates = list.map(r => Number(r.fail_rate) || 0)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = list[i] || {}
        return `<b>${it.bucket}</b><br/>设备数: <b>${Number(it.device_cnt) || 0}</b> 个<br/>失败率: <b>${Number(it.fail_rate) || 0}%</b>`
      },
    },
    legend: { data: ['设备数', '失败率%'], top: 0, type: 'scroll' },
    grid: { left: 56, right: 64, top: 36, bottom: 36 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 12 } },
    yAxis: [
      { type: 'value', name: '设备数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '失败率%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '设备数',
        type: 'bar',
        data: deviceCnt,
        barMaxWidth: 40,
        itemStyle: { color: '#60a5fa', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', fontSize: 11, formatter: '{c}' },
      },
      {
        name: '失败率%',
        type: 'line',
        yAxisIndex: 1,
        data: failRates,
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        itemStyle: { color: '#7c3aed' },
        lineStyle: { width: 3, color: '#7c3aed' },
      },
    ],
  }
})
</script>

<style scoped>
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
  color: #111827;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb-12 {
  margin-bottom: 12px;
}

/* 风险 KPI 卡片 */
.risk-card {
  border-radius: 10px;
  padding: 14px 16px 12px;
  border: 1px solid rgba(148, 163, 184, 0.15);
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 96px;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.risk-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px -8px rgba(15, 23, 42, 0.18);
}
.risk-label {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.2px;
}
.risk-sub {
  font-size: 11px;
  color: #6b7280;
  opacity: 0.9;
}
.risk-value {
  margin-top: 4px;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.3px;
}
.risk-value .unit {
  font-size: 13px;
  font-weight: 500;
  margin-left: 2px;
  opacity: 0.85;
}
.risk-red {
  background: #fef2f2;
}
.risk-red .risk-label,
.risk-red .risk-value {
  color: #dc2626;
}
.risk-orange {
  background: #fff7ed;
}
.risk-orange .risk-label,
.risk-orange .risk-value {
  color: #ea580c;
}
.risk-purple {
  background: #faf5ff;
}
.risk-purple .risk-label,
.risk-purple .risk-value {
  color: #7c3aed;
}
.risk-gray {
  background: #f3f4f6;
}
.risk-gray .risk-label,
.risk-gray .risk-value {
  color: #6b7280;
}

/* 抽屉内画像 */
.drawer-content {
  padding: 0 4px;
}
.kv {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
}
.kv .k {
  color: #6b7280;
  min-width: 64px;
  flex-shrink: 0;
}
.kv .v {
  color: #111827;
  font-weight: 500;
  word-break: break-all;
}
</style>
