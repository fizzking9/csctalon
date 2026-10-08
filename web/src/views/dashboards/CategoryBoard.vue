<!--
  ┌────────────────────────── 设计说明（Design Rationale · #272） ──────────────────────────┐
  │ 目的（替谁回答什么问题）                                                                  │
  │   替运营主管回答：「整体运营大盘是否健康？」—— 规模/活跃/营收/风险四级总览，              │
  │   一眼判断整体趋势，不做维度归因（归因交给退款/投诉分析）。                                 │
  │                                                                                          │
  │ 逻辑（为什么这样组织）                                                                    │
  │   ① 总览只保留 5 张 KPI 卡片（总设备/活跃/充值/退款/投诉），无维度切换。                  │
  │   ② 维度切分（设备类型/套餐/商户号/激活时间）下沉到退款分析和投诉分析，                   │
  │      作为筛选和分组维度——运营看板回答「好不好」，分析页面回答「哪里不好」。                │
  │                                                                                          │
  │ 口径（数据判据 + 文档指针）                                                               │
  │   · 总设备数 = devices 全量；活跃 = 近90天有充值或完整月流量；                              │
  │   · 营收 = 近90天充值金额(元)；风险 = 近30天退款笔数 / 投诉数；                             │
  │   · 投诉判据走 complaint_scope 唯一出口；金额 Numeric(12,2) 元，出参 float（#222）；       │
  │   · 维度切分归退款/投诉分析（#272 取舍）。                                                │
  │                                                                                          │
  │ 取舍（不做什么及原因）                                                                    │
  │   ❌ 不做维度切换/明细表 —— 维度归因与退款/投诉分析重复，已下沉（#272）；                  │
  │   ❌ 不做钻取 —— 单设备明细由「设备360」覆盖，品类明细由退款/投诉分析覆盖；                │
  │   ❌ 不做趋势图 —— 总览趋势由「今日运营」看板覆盖（#225）。                                │
  │                                                                                          │
  │ 工单：#272 · 前身 #254（维度切分版）                                                      │
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
import { QuestionFilled } from '@element-plus/icons-vue'
import { categoryOps } from '@/api/analytics'

const BOARD_TIP = `
<b>数据说明</b><br/>
· 来源：devices / device_combos / device_flow_usage / refunds / service_tickets<br/>
· 口径：总设备数 = devices 全量；活跃 = 近90天有充值或完整月流量；营收 = 近90天充值金额(元)；退款/投诉 = 近30天；投诉判据走全站唯一出口（服务类别空白或"投诉"）<br/>
· 时点：T+0 实时查询<br/>
· "空"的含义：窗口内无记录则显示 0<br/>
· 本模块<b>不下钻</b>：维度归因见退款/投诉分析；单设备明细见「设备360」<br/>
<br/>
<b>取舍</b><br/>
· 不做维度切换/明细表（已下沉到退款/投诉分析，#272）<br/>
· 不做趋势图（见「今日运营」看板）<br/>
`

const kpiCards = ref([
  { label: '总设备数', value: '-', sub: '规模 · 台', bg: '#eff6ff', color: '#2563eb' },
  { label: '活跃设备', value: '-', sub: '近90天 · 台', bg: '#f0fdf4', color: '#16a34a' },
  { label: '充值金额', value: '-', sub: '近90天 · 元', bg: '#fff7ed', color: '#ea580c' },
  { label: '退款笔数', value: '-', sub: '近30天 · 笔', bg: '#fef2f2', color: '#dc2626' },
  { label: '投诉数', value: '-', sub: '近30天 · 件', bg: '#fdf4ff', color: '#9333ea' },
])

const fmtMoney = (v) =>
  v >= 10000 ? (v / 10000).toFixed(1) + '万' : Math.round(v).toLocaleString('zh-CN')

async function load() {
  try {
    // device_type 桶数少(<20)，limit=50 可覆盖全部桶；各桶互斥，求和=总量
    const res = await categoryOps.opsOverview({ dim: 'device_type', limit: 50 })
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
