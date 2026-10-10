<!--
  ┌─────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：替退款条线管理者（主管/组长）回答三个决策问题：                                          │
  │       ① 钱退出去多少、账对不对得上（决策概览）；                                               │
  │       ② 退到钱包的钱沉淀了多少、三路退款各自多慢（提现时效）；                                  │
  │       ③ 每笔商户号真实退款能否与运营登记一一对上（对账复核）。                                  │
  │ 逻辑：三 Tab 按「看结论 → 查拖延 → 核逐笔」递进，先总量后明细；                                 │
  │       决策概览是全站唯一做退款自动结论的出口，本壳页自身不陈列任何数字。                         │
  │ 口径：概览 = 四路经营(A线上/B钱包/C闪猫/D手动) + E 对账基准（E 不与四路累加）；                 │
  │       提现 = 三路有时效退款（A钱包/B直退/D手动），窗口按 D4 实际出款时刻；                      │
  │       对账 = E 商户号真实退款流水为财务基准，用 E 调校 ABCD。                                  │
  │       详见 docs/arch/A-20260906-002-退款五路手动对账复核功能.md、                               │
  │             backend/app/services/analytics/refund_chain.py。                                  │
  │ 取舍：① 进页不自动加载数据，点「分析」才请求 —— 避免进入页面即打出一串重查询；                  │
  │       ② 三个 Tab 不共用顶部时间条 —— 概览按 D4 出款日、提现/对账各有独立窗口语义，              │
  │          强行统一会让人误以为三处数字同窗；                                                    │
  │       ③ 壳页不放 KPI 卡 —— 卡片只在各 Tab 子组件内，避免同一指标双真身；                        │
  │       ④ Tab 内筛选不写 URL，仅下钻链接携带 tab/start/end（保证下钻可还原窗口，见 #205）。       │
  │ 工单：#205 · 卡片级 ⓘ 与下钻见各 Tab 子组件（refund/DecisionOverview、RefundWithdrawal、       │
  │       refund/ReconcileCheck、refund/ReconcileLines）                                           │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">
        退款分析
        <!-- ★ #205 页面层可见设计说明（界面设计说明规范 §二·方案A）：含「取舍」段 -->
        <el-tooltip effect="dark" placement="bottom-start" raw-content
          content="<b>这个页面为什么这样设计</b><br/><b>给谁</b>：退款条线主管/组长（按周/按需查阅）<br/><b>回答什么</b>：① 退出去多少、账对不对得上（决策概览）② 退到钱包的钱沉淀了多少、三路退款各自多慢（提现时效）③ 每笔商户号真实退款能否与运营登记对上（对账复核）<br/><b>组织逻辑</b>：看结论 → 查拖延 → 核逐笔，先总量后明细；决策概览是唯一自动结论出口<br/><b>口径</b>：概览 = 四路经营 + E 对账基准（E 不与四路累加）；提现 = 三路有时效退款按实际出款时刻（D4）；对账 = E 商户号真实退款流水为财务基准<br/>详见 docs/arch/A-20260906-002-退款五路手动对账复核功能.md<br/><br/><b>⚠️ 取舍（不做什么及原因）</b><br/>① 进页不自动加载，点「分析」才请求 —— 避免进页即打出一串重查询<br/>② 三 Tab 不共用时间条 —— 概览按 D4 出款日、提现/对账窗口语义不同，强行统一会误以为同窗<br/>③ 壳页不陈列 KPI —— 卡片只在各 Tab 内，避免同指标双真身">
          <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-2px;font-size:14px;margin-left:4px"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
      <div class="filter-bar">
        <el-radio-group v-model="tabMode" size="small" @change="onTabChange">
          <el-radio-button value="overview">决策概览</el-radio-button>
          <el-radio-button value="withdrawal">提现时效</el-radio-button>
          <el-radio-button value="reconcile">对账复核</el-radio-button>
        </el-radio-group>
        <el-radio-group v-if="tabMode === 'overview'" v-model="rangeMode" size="small">
          <el-radio-button value="day">单日·{{ dailyLabel }}</el-radio-button>
          <el-radio-button value="7">近7天</el-radio-button>
          <el-radio-button value="30">近30天</el-radio-button>
          <el-radio-button value="90">近90天</el-radio-button>
          <el-radio-button value="custom">自定义</el-radio-button>
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
          <el-radio-button value="day">按日</el-radio-button>
          <el-radio-button value="week">按周</el-radio-button>
        </el-radio-group>
        <el-button v-if="tabMode !== 'reconcile'" type="primary" :icon="Search" :loading="analyzing" @click="runAnalysis">分析</el-button>
        <el-button v-if="tabMode === 'overview' && analyzed" :icon="Download" @click="handleExport">导出</el-button>
      </div>
    </div>

    <!-- 未分析时的空态提示(决策概览/提现时效专属; 对账复核自带窗口选择, 不依赖此空态) -->
    <section v-if="!analyzed && tabMode !== 'reconcile'" class="empty-hint">
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
      v-else-if="tabMode === 'overview' && analyzed"
      :start="effectiveStart"
      :end="effectiveEnd"
      :grain="grainMode"
      :reload-key="reloadKey"
    />

    <!-- ============ TAB2: 退款-提现全链路 ============ -->
    <RefundWithdrawal v-else-if="analyzed && tabMode === 'withdrawal'" />

    <!-- ============ TAB3: 五路对账复核 (A-20260906-002 / #22) ============ -->
    <!-- 独立渲染: 不依赖 analyzed(对账复核自带时间窗选择与生成预览按钮) -->
    <ReconcileCheck
      v-if="tabMode === 'reconcile'"
      :start="effectiveStart"
      :end="effectiveEnd"
      :reload-key="reloadKey"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { Search, Download, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import DecisionOverview from './refund/DecisionOverview.vue'
import RefundWithdrawal from './RefundWithdrawal.vue'
import ReconcileCheck from './refund/ReconcileCheck.vue'
import { refundExport } from '@/api/analytics'

const tabMode = ref('overview')
const rangeMode = ref('day')
const customRange = ref([])
const grainMode = ref('day')
const analyzed = ref(false)
const analyzing = ref(false)
const reloadKey = ref(0)

const route = useRoute()
const router = useRouter()

// ★ #205 下钻承接：提现时效页 KPI 卡下钻到决策概览明细时经 URL 携带 tab/start/end。
// 仅在挂载时读取一次还原窗口；带窗下钻直接装载（免再点「分析」，反馈同 runAnalysis）。
onMounted(() => {
  const q = route.query
  if (q.start && q.end) {
    tabMode.value = 'overview'
    rangeMode.value = 'custom'
    customRange.value = [String(q.start), String(q.end)]
    analyzed.value = true
    analyzing.value = true
    reloadKey.value += 1
    setTimeout(() => { analyzing.value = false }, 300)
  } else if (q.tab === 'overview' || q.tab === 'withdrawal' || q.tab === 'reconcile') {
    tabMode.value = q.tab
  }
})

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
  // ★ #205: 手动切 Tab 时把 tab 回写 URL 并丢弃窗口参数（start/end 只是下钻入口的一次性上下文，
  //   各 Tab 的筛选器窗口语义不同，不随 URL 流转；程序化设 tabMode 不触发本回调，不会与下钻冲突）
  router.replace({ query: { tab: tabMode.value } })
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
