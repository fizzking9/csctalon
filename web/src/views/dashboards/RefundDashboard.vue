<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">退款分析</div>
      <div class="filter-bar">
        <el-radio-group v-model="tabMode" size="small" @change="onTabChange">
          <el-radio-button label="overview">决策概览</el-radio-button>
          <el-radio-button label="withdrawal">提现时效</el-radio-button>
        </el-radio-group>
        <el-radio-group v-if="tabMode === 'overview'" v-model="rangeMode" size="small">
          <el-radio-button label="day">单日·{{ dailyLabel }}</el-radio-button>
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
          <el-radio-button label="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="tabMode === 'overview' && rangeMode === 'custom'"
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width: 240px"
        />
        <el-radio-group v-if="tabMode === 'overview'" v-model="grainMode" size="small">
          <el-radio-button label="day">按日</el-radio-button>
          <el-radio-button label="week">按周</el-radio-button>
        </el-radio-group>
        <el-button type="primary" :icon="Search" :loading="analyzing" @click="runAnalysis">分析</el-button>
        <el-button v-if="tabMode === 'overview' && analyzed" :icon="Download" @click="handleExport">导出</el-button>
      </div>
    </div>

    <!-- 未分析时的空态提示 -->
    <section v-if="!analyzed" class="empty-hint">
      <el-empty :image-size="96" description="">
        <template #description>
          <div class="empty-tip">
            <div class="empty-main">设置筛选区间后，点击右上角 <el-button size="small" type="primary" :icon="Search" @click="runAnalysis">分析</el-button> 加载数据</div>
            <div class="empty-sub">按需加载 · 避免进入页面即产生大量请求</div>
          </div>
        </template>
      </el-empty>
    </section>

    <!-- ============ TAB1: 决策概览 (0~5 区块) ============ -->
    <DecisionOverview
      v-else-if="tabMode === 'overview'"
      :start="effectiveStart"
      :end="effectiveEnd"
      :grain="grainMode"
      :reload-key="reloadKey"
    />

    <!-- ============ TAB2: 退款-提现全链路 ============ -->
    <RefundWithdrawal v-else-if="analyzed && tabMode === 'withdrawal'" />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import dayjs from 'dayjs'
import { Search, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import DecisionOverview from './refund/DecisionOverview.vue'
import RefundWithdrawal from './RefundWithdrawal.vue'
import { refundExport } from '@/api/analytics'

const tabMode = ref('overview')
const rangeMode = ref('day')
const customRange = ref([])
const grainMode = ref('day')
const analyzed = ref(false)
const analyzing = ref(false)
const reloadKey = ref(0)

// 单日默认值: 上午(12点前) → 昨日, 下午/晚上 → 当日; 点「分析」时重新计算,保证跨午点击取到最新默认
const dailyLabel = ref('昨日')
const dailyDay = ref('')
function refreshDailyDefault() {
  const isMorning = dayjs().hour() < 12
  dailyLabel.value = isMorning ? '昨日' : '今日'
  dailyDay.value = dayjs().subtract(isMorning ? 1 : 0, 'day').format('YYYY-MM-DD')
}
refreshDailyDefault()

const effectiveStart = computed(() => {
  if (rangeMode.value === 'day') return dailyDay.value
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'day') return dailyDay.value
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  return dayjs().format('YYYY-MM-DD')
})

function onTabChange() {
  // Tab 切换不自动加载, 保持"点分析才装载"约定
  analyzed.value = false
}

function runAnalysis() {
  refreshDailyDefault()  // 点分析时按当前时间重算单日默认(上午→昨日, 下午→今日)
  analyzed.value = true
  analyzing.value = true
  reloadKey.value += 1
  setTimeout(() => { analyzing.value = false }, 300)
}

async function handleExport() {
  try {
    const params = {}
    if (effectiveStart.value) params.start = effectiveStart.value
    if (effectiveEnd.value) params.end = effectiveEnd.value
    const res = await refundExport(params)
    // 响应拦截器对 blob 成功响应返回 response.data(即 Blob);兼容返回完整 response 的情况
    const blobData = res instanceof Blob ? res : res?.data
    const blob = new Blob([blobData], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    let fname = `退款明细_${effectiveStart.value || '全量'}_${effectiveEnd.value || '至今'}.xlsx`
    const cd = res.headers?.['content-disposition'] || ''
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd)
    if (m) fname = decodeURIComponent(m[1])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fname
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    ElMessage.success('已导出')
  } catch (e) {
    ElMessage.error('导出失败: ' + (e?.message || e))
  }
}
</script>

<style scoped>
.filter-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.mb-16 { margin-bottom: 16px; }
.empty-hint { background: #fff; border-radius: 8px; padding: 48px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.empty-tip { text-align: center; }
.empty-main { font-size: 15px; color: #374151; margin-bottom: 8px; display: flex; align-items: center; justify-content: center; gap: 8px; }
.empty-sub { font-size: 12px; color: #9ca3af; }
</style>
