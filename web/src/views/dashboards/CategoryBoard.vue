<!--
  ┌────────────────────────── 设计说明（Design Rationale · #272 / #293 改） ─────────────────────────┐
  │ 目的（替谁回答什么问题）                                                                  │
  │   替运营主管回答：「整体运营大盘是否健康？」—— 规模/活跃/营收/风险四级总览，              │
  │   一眼判断整体趋势，不做维度归因（归因交给退款/投诉分析）。                                 │
  │                                                                                          │
  │ 逻辑（为什么这样组织）                                                                    │
  │   ① 总览只保留 5 张 KPI 卡片（总设备/活跃/充值/退款/投诉），无维度切换。                  │
  │   ② 维度切分（设备类型/套餐/商户号/激活时间）下沉到退款分析和投诉分析，                   │
  │      作为筛选和分组维度——运营看板回答「好不好」，分析页面回答「哪里不好」。                │
  │   ③ #293: 窗口前置——日/周/月/季 grain 切换（与「运营总览」tab 同一窗口标准），            │
  │      全部活动量列跟随所选窗口；修复旧版「90/90/90/30/30」硬编码拼盘（同名指标             │
  │      与总览 tab 数字对不上）。                                                            │
  │                                                                                          │
  │ 口径（数据判据 + 文档指针）                                                               │
  │   · 总设备数 = devices 全量（存量语义，不随窗口变）；                                     │
  │   · 活跃 = 窗口内有充值或完整月流量的设备；充值 = 窗口内充值金额(元)；                     │
  │   · 退款 = 窗口内「实际退款」笔数（口径A: 排除 无需退款/驳回/申请中，                       │
  │     与退款分析 4442e89 对齐——修复前含申请中夸约40%）；                                     │
  │   · 投诉 = 窗口内投诉工单（complaint_scope 唯一出口）；                                    │
  │   · 投诉率(接口内)分母=窗口内活跃设备（同窗可比）；                                        │
  │   · 金额 Numeric(12,2) 元，出参 float（#222）。                                           │
  │                                                                                          │
  │ 取舍（不做什么及原因）                                                                    │
  │   ❌ 不做维度切换/明细表 —— 维度归因与退款/投诉分析重复，已下沉（#272）；                  │
  │   ❌ 不做钻取 —— 单设备明细由「设备360」覆盖，品类明细由退款/投诉分析覆盖；                │
  │   ❌ 不做趋势图 —— 总览趋势由「今日运营」看板覆盖（#225）。                                │
  │                                                                                          │
  │ 工单：#272 · #293（grain 窗口 + 口径A + device_type key 修复）· 前身 #254                 │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="category-board">
    <div class="cb-head mb-12">
      <div class="cb-title">
        运营总览
        <span class="cb-sub">规模 · 活跃 · 营收 · 风险</span>
        <el-tooltip effect="dark" placement="top" raw-content :content="BOARD_TIP">
          <el-icon class="cb-tip-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
      <!-- #293: 窗口前置(日/周/月/季), 与运营总览 tab 同一窗口标准 -->
      <el-radio-group v-model="grain" size="small" @change="load">
        <el-radio-button value="day">今日</el-radio-button>
        <el-radio-button value="week">本周</el-radio-button>
        <el-radio-button value="month">本月</el-radio-button>
        <el-radio-button value="quarter">本季度</el-radio-button>
      </el-radio-group>
    </div>

    <el-row :gutter="10">
      <el-col v-for="c in kpiCards" :key="c.label" :xs="12" :sm="4">
        <div class="cb-kpi" :style="{ background: c.bg }">
          <div class="cb-kpi-label" :style="{ color: c.color }">{{ c.label }}</div>
          <div class="cb-kpi-value" :style="{ color: c.color }">{{ c.value }}</div>
          <div class="cb-kpi-sub">{{ c.sub }}</div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import dayjs from 'dayjs'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import { QuestionFilled } from '@element-plus/icons-vue'
import { categoryOps } from '@/api/analytics'
import { getWeekRangeStr } from '@/utils/weekRange'

dayjs.extend(quarterOfYear)

const GRAIN_LABEL = { day: '今日', week: '本周', month: '本月', quarter: '本季度' }

// #293: grain → 窗口(北京时间 YYYY-MM-DD, 与今日运营看板同标准: 周一起始见 weekRange.js)
function grainRange(g) {
  const now = dayjs()
  if (g === 'day') return { start: now.format('YYYY-MM-DD'), end: now.format('YYYY-MM-DD') }
  if (g === 'week') return getWeekRangeStr(undefined, 0, true)
  if (g === 'month') return { start: now.startOf('month').format('YYYY-MM-DD'), end: now.format('YYYY-MM-DD') }
  return { start: now.startOf('quarter').format('YYYY-MM-DD'), end: now.format('YYYY-MM-DD') }
}

const grain = ref('month')

const BOARD_TIP = `
<b>数据说明</b><br/>
· 来源：devices / device_combos / device_flow_usage / refunds / service_tickets<br/>
· 口径：总设备数 = devices 全量(存量)；活跃 = 窗口内有充值或完整月流量；营收 = 窗口内充值金额(元)；
  退款 = 窗口内<b>实际退款</b>笔数（口径A: 排除 无需退款/驳回/申请中，与退款分析对齐）；
  投诉 = 窗口内投诉工单（complaint_scope 唯一出口）<br/>
· 窗口：跟随上方 日/周/月/季 切换（与运营总览 tab 同一标准）<br/>
· 时点：T+0 实时查询<br/>
· "空"的含义：窗口内无记录则显示 0<br/>
· 本模块<b>不下钻</b>：维度归因见退款/投诉分析；单设备明细见「设备360」<br/>
<br/>
<b>取舍</b><br/>
· 不做维度切换/明细表（已下沉到退款/投诉分析，#272）<br/>
· 不做趋势图（见「今日运营」看板）<br/>
`

const kpiCards = ref([
  { label: '总设备数', value: '-', sub: '存量 · 台', bg: '#eff6ff', color: '#2563eb' },
  { label: '活跃设备', value: '-', sub: '本月 · 台', bg: '#f0fdf4', color: '#16a34a' },
  { label: '充值金额', value: '-', sub: '本月 · 元', bg: '#fff7ed', color: '#ea580c' },
  { label: '退款笔数', value: '-', sub: '本月 · 笔', bg: '#fef2f2', color: '#dc2626' },
  { label: '投诉数', value: '-', sub: '本月 · 件', bg: '#fdf4ff', color: '#9333ea' },
])

const fmtMoney = (v) =>
  v >= 10000 ? (v / 10000).toFixed(1) + '万' : Math.round(v).toLocaleString('zh-CN')

async function load() {
  try {
    const { start, end } = grainRange(grain.value)
    const label = GRAIN_LABEL[grain.value]
    // 副标题随 grain 更新(总设备数=存量, 不随窗口)
    kpiCards.value[1].sub = `${label} · 台`
    kpiCards.value[2].sub = `${label} · 元`
    kpiCards.value[3].sub = `${label} · 笔`
    kpiCards.value[4].sub = `${label} · 件`
    // device_type 桶数少(<20)，limit=50 可覆盖全部桶；各桶互斥，求和=总量
    const res = await categoryOps.opsOverview({ dim: 'device_type', limit: 50, start, end })
    const items = res.data?.items || []
    const sum = (k) => items.reduce((a, b) => a + (Number(b[k]) || 0), 0)
    kpiCards.value[0].value = sum('device_cnt').toLocaleString('zh-CN')
    kpiCards.value[1].value = sum('active_cnt').toLocaleString('zh-CN')
    kpiCards.value[2].value = fmtMoney(sum('recharge_amount'))
    kpiCards.value[3].value = sum('refund_cnt').toLocaleString('zh-CN')
    kpiCards.value[4].value = sum('complaint_cnt').toLocaleString('zh-CN')
  } catch {
    // 失败保持 "-"，不阻断看板其它模块
  }
}
onMounted(load)
</script>

<style scoped>
.category-board { padding-top: 4px; }
.cb-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.cb-title { font-size: 15px; font-weight: 600; color: #303133; }
.cb-sub { font-size: 12px; font-weight: 400; color: #909399; margin-left: 8px; }
.cb-tip-icon { color: #b0b4bb; cursor: help; font-size: 14px; margin-left: 6px; vertical-align: -2px; }
.mb-12 { margin-bottom: 12px; }
.cb-kpi { border-radius: 8px; padding: 10px 12px; }
.cb-kpi-label { font-size: 12px; }
.cb-kpi-value { font-size: 22px; font-weight: 700; line-height: 1.3; }
.cb-kpi-sub { font-size: 11px; color: #909399; }
@media (max-width: 768px) { .cb-kpi-value { font-size: 18px; } }
</style>
