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
  │   ④ #293: 表现方式对齐「运营总览」tab —— 复用 KpiCard 组件（图标/单位/万元格式/           │
  │      同比标签），活动指标环比上一窗口（昨日/上周/上月/上季度同期）。                        │
  │                                                                                          │
  │ 口径（数据判据 + 文档指针）                                                               │
  │   · 总设备数 = devices 全量（存量语义，不随窗口变，无环比）；                              │
  │   · 活跃 = 窗口内有充值(非质检)或完整月流量的设备；                                       │
  │   · 充值 = 窗口内充值金额(元)，排除质检套餐(出厂质检流水非营收,#293)；                     │
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
  │   ❌ 总设备数不做环比 —— 存量指标无「同期」概念。                                          │
  │                                                                                          │
  │ 工单：#272 · #293（grain 窗口+口径A+设备类型key+质检套餐排除）· 前身 #254                  │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="category-board">
    <section class="section-card mb-20">
      <SectionHeader :title="'核心指标'" :sub="'对比' + compareName + '同时段 · ' + GRAIN_LABEL[grain]">
        <template #extra>
          <div class="cb-extra">
            <!-- #293: 窗口前置(日/周/月/季), 与运营总览 tab 同一窗口标准 -->
            <el-radio-group v-model="grain" size="small" @change="load">
              <el-radio-button value="day">今日</el-radio-button>
              <el-radio-button value="week">本周</el-radio-button>
              <el-radio-button value="month">本月</el-radio-button>
              <el-radio-button value="quarter">本季度</el-radio-button>
            </el-radio-group>
            <el-tooltip effect="dark" placement="top" raw-content :content="BOARD_TIP">
              <el-icon class="cb-tip-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </template>
      </SectionHeader>
      <el-row :gutter="10">
        <el-col v-for="c in kpiCards" :key="c.key" :xs="12" :sm="8" :md="4">
          <KpiCard
            :label="c.label"
            :value="c.value"
            :unit="c.unit"
            :money="c.money"
            :bg="c.bg"
            :color="c.color"
            :yoy-key="c.yoyKey"
            :yoy-pct="c.yoyPct"
            :yoy-value="c.yoyValue"
            :yoy-label="compareName"
            :tip="c.tip"
            compact
          />
        </el-col>
      </el-row>
    </section>
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
const COMPARE_NAME = { day: '昨日', week: '上周', month: '上月', quarter: '上季度' }

// #293: grain → [当前窗口, 上一窗口] (北京时间 YYYY-MM-DD; 周一起始见 weekRange.js)
// 上一窗口取完整同期: 周=上一整周, 月/季=上一完整月/季的相同天数跨度
function grainRange(g) {
  const now = dayjs()
  const day = (d) => d.format('YYYY-MM-DD')
  if (g === 'day') {
    return [
      { start: day(now), end: day(now) },
      { start: day(now.subtract(1, 'day')), end: day(now.subtract(1, 'day')) },
    ]
  }
  if (g === 'week') {
    return [
      getWeekRangeStr(undefined, 0, true),
      getWeekRangeStr(undefined, 1, false),
    ]
  }
  if (g === 'month') {
    const len = now.date() // 本月已过天数(含今日), 对齐上月同期
    return [
      { start: day(now.startOf('month')), end: day(now) },
      { start: day(now.subtract(1, 'month').startOf('month')), end: day(now.subtract(1, 'month').startOf('month').add(len - 1, 'day')) },
    ]
  }
  const len = now.diff(now.startOf('quarter'), 'day') + 1
  return [
    { start: day(now.startOf('quarter')), end: day(now) },
    { start: day(now.startOf('quarter').subtract(1, 'quarter')), end: day(now.startOf('quarter').subtract(1, 'quarter').add(len - 1, 'day')) },
  ]
}

const grain = ref('month')
const compareName = ref('上月')

const BOARD_TIP = `
<b>数据说明</b><br/>
· 来源：devices / device_combos / device_flow_usage / refunds / service_tickets<br/>
· 口径：总设备数 = devices 全量(存量)；活跃 = 窗口内有充值(非质检)或完整月流量；
  营收 = 窗口内充值金额(元)，<b>已排除质检套餐</b>(出厂质检流水非营收,#293)；
  退款 = 窗口内<b>实际退款</b>笔数（口径A: 排除 无需退款/驳回/申请中，与退款分析对齐）；
  投诉 = 窗口内投诉工单（complaint_scope 唯一出口）<br/>
· 窗口：跟随右上方 日/周/月/季 切换；环比 = 上一同期(昨日/上周/上月/上季度同期)<br/>
· 时点：T+0 实时查询<br/>
· "空"的含义：窗口内无记录则显示 0<br/>
· 本模块<b>不下钻</b>：维度归因见退款/投诉分析；单设备明细见「设备360」<br/>
<br/>
<b>取舍</b><br/>
· 不做维度切换/明细表（已下沉到退款/投诉分析，#272）<br/>
· 不做趋势图（见「今日运营」看板）<br/>
· 总设备数不做环比（存量指标无同期概念）<br/>
`

const CARD_META = [
  { key: 'device', label: '总设备数', unit: '台', bg: '#eff6ff', color: '#2563eb', yoyKey: '' },
  { key: 'active', label: '活跃设备', unit: '台', bg: '#f0fdf4', color: '#16a34a', yoyKey: '' },
  { key: 'recharge', label: '充值金额', unit: '', bg: '#fff7ed', color: '#ea580c', money: true, yoyKey: '' },
  { key: 'refund', label: '退款笔数', unit: '笔', bg: '#fef2f2', color: '#dc2626', yoyKey: 'refund_count' },
  { key: 'complaint', label: '投诉数', unit: '件', bg: '#fdf4ff', color: '#9333ea', yoyKey: 'complaint' },
]

const kpiCards = ref(
  CARD_META.map((c) => ({ ...c, value: '-', yoyPct: null, yoyValue: 0 })),
)

async function fetchSum(win) {
  // #293: KPI 求和必须走后端 totals(全桶聚合)——回填后 device_type 桶数 >50,
  // 前端 sum(items) 会被 limit 截断少算长尾(实测少算 ~75%, 环比显示异常 -60%+)
  const res = await categoryOps.opsOverview({
    dim: 'device_type', limit: 50,
    start: win.start, end: win.end + ' 23:59:59',
  })
  const t = res.data?.totals || {}
  return {
    device: Number(t.device_cnt) || 0,
    active: Number(t.active_cnt) || 0,
    recharge: Number(t.recharge_amount) || 0,
    refund: Number(t.refund_cnt) || 0,
    complaint: Number(t.complaint_cnt) || 0,
  }
}

async function load() {
  try {
    const [curWin, prevWin] = grainRange(grain.value)
    compareName.value = COMPARE_NAME[grain.value]
    const [cur, prev] = await Promise.all([fetchSum(curWin), fetchSum(prevWin)])
    kpiCards.value = CARD_META.map((c) => {
      const v = cur[c.key]
      const p = prev[c.key] || 0
      // 总设备数=存量, 无环比; 其余活动量指标环比上一同期
      const yoyPct = c.yoyKey === '' && c.key === 'device'
        ? null
        : (p ? Math.round((v - p) / p * 1000) / 10 : null)
      return { ...c, value: v, yoyPct, yoyValue: p }
    })
  } catch {
    // 失败保持 "-"，不阻断看板其它模块
  }
}
onMounted(load)
</script>

<style scoped>
.category-board { padding-top: 4px; }
.cb-extra { display: flex; align-items: center; gap: 8px; }
.cb-tip-icon { color: #b0b4bb; cursor: help; font-size: 14px; }
.mb-20 { margin-bottom: 20px; }
@media (max-width: 768px) { .cb-extra { flex-wrap: wrap; } }
</style>
