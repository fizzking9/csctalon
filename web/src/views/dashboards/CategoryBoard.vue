<!--
  ┌────────────────────────── 设计说明（Design Rationale · #254 B3-10） ──────────────────────────┐
  │ 目的（替谁回答什么问题）                                                                      │
  │   替运营主管在周/月复盘回答：「哪类设备 / 哪个套餐 / 哪个商户号是营收主力、                     │
  │   谁投诉风险最高？」—— 据此做资源倾斜（给主力品类加库存/客服）与风险管控                        │
  │   （高投诉率品类整改）决策。                                                                  │
  │                                                                                              │
  │ 逻辑（为什么这样组织）                                                                        │
  │   ① 指标统一为「规模 → 活跃 → 营收 → 风险」四级，对标正规商业运营看板：                       │
  │      规模=设备数；活跃=近90天有充值或完整月流量；营收=近90天充值金额；                        │
  │      风险=近30天退款笔数 / 投诉数 / 投诉率(每百台)。                                          │
  │   ② 三维切换（设备类型 / 套餐 / 商户号）共用一个接口与一套视图 —— 同一组问题                  │
  │      沿三个切面看，不为每维度堆一个页面。                                                     │
  │   ③ TOP10 双轴图（充值金额柱 + 投诉数线）：一眼看出「营收主力是否同时是投诉大户」，           │
  │      两指标错位（营收高投诉低 = 优质品类；双高 = 需整改）正是决策输入。                       │
  │                                                                                              │
  │ 口径（数据判据 + 文档指针）                                                                   │
  │   · 设备类型走 devices.device_type_code（B3-1 归一，无码设备落「未归一」桶）；                │
  │   · 套餐走 combo_dict 归一码（B3-2，别名未命中回落原始名）；                                  │
  │   · 商户号走 DeviceMerchantLink is_current 建链（B3-1，未建链设备不进商户维）；               │
  │   · 投诉判据走 complaint_scope 唯一出口（服务类别空白或'投诉'，禁 LIKE）；                    │
  │   · 充值金额 Numeric(12,2) 元，后端 Decimal 计算、出参 float（#222 方案 C′）；                │
  │   · 架构: docs/arch/A-20261007-002-客户域主数据与设备360设计.md（LOCKED v1.0）                │
  │                                                                                              │
  │ 取舍（不做什么及原因）                                                                        │
  │   ❌ 不做转化漏斗 —— 需埋点/订单状态机，当前数据面无；                                        │
  │   ❌ 不做留存分析 —— 需设备生命周期快照，属 B4 架构演进；                                     │
  │   ❌ 不做钻取到单设备 —— 设备360 已覆盖（避免重复入口，看板卡片规范 §3.4 声明）；             │
  │   ❌ 不在本组件叠事件时间轴标注 —— x 轴是品类不是时间，markPoint/markLine 无锚点；            │
  │      时间轴事件标注已在「运营总览」趋势图落地（#233/#234 B1.5）；                             │
  │   ❌ 默认 TOP10 截断 —— 防宽表；全量明细走数据查看/导出。                                     │
  │                                                                                              │
  │ 工单：#254 · 排期: docs/reports/20261009-下一步开发计划-排期规划.md §六 B3-10                  │
  └──────────────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="category-board">
    <!-- 头部: 标题 + ⓘ数据说明 + 维度切换 -->
    <div class="cb-head mb-12">
      <div class="cb-title">
        分品类运营
        <span class="cb-sub">规模 → 活跃 → 营收 → 风险 · 按充值金额 TOP{{ limit }}</span>
        <el-tooltip effect="dark" placement="top" raw-content :content="BOARD_TIP">
          <el-icon class="cb-tip-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
      <el-radio-group v-model="dim" size="small" @change="load">
        <el-radio-button value="device_type">设备类型</el-radio-button>
        <el-radio-button value="combo">套餐</el-radio-button>
        <el-radio-button value="merchant">商户号</el-radio-button>
      </el-radio-group>
    </div>

    <!-- KPI 汇总卡（TOP{{limit}} 合计，非全站总量 —— 卡面已标注防误读） -->
    <el-row :gutter="10" class="mb-12">
      <el-col v-for="c in kpiCards" :key="c.label" :xs="12" :sm="6">
        <div class="cb-kpi" :style="{ background: c.bg }">
          <div class="cb-kpi-label" :style="{ color: c.color }">{{ c.label }}</div>
          <div class="cb-kpi-value" :style="{ color: c.color }">{{ c.value }}</div>
          <div class="cb-kpi-sub">{{ c.sub }}</div>
        </div>
      </el-col>
    </el-row>

    <!-- TOP10 对比图: 充值金额(柱·左轴) + 投诉数(线·右轴) -->
    <div v-loading="loading">
      <BaseChart v-if="items.length" :option="topChartOption" height="320px" class="mb-12" />
      <el-empty
        v-else-if="!loading"
        description="该维度窗口内无设备/充值/退款/投诉记录"
        :image-size="60"
        class="mb-12"
      />
    </div>

    <!-- 维度明细表（投诉率>5% 红色高亮） -->
    <el-table :data="items" size="small" border stripe max-height="480">
      <el-table-column type="index" label="#" width="46" fixed />
      <el-table-column prop="name" label="品类名" min-width="150" show-overflow-tooltip fixed />
      <el-table-column prop="device_cnt" label="设备数" width="90" align="right" sortable />
      <el-table-column label="活跃数(90天)" width="110" align="right" sortable :sort-by="(r) => r.active_cnt">
        <template #default="{ row }">
          {{ row.active_cnt }}
          <span class="cb-dim">({{ activeRate(row) }}%)</span>
        </template>
      </el-table-column>
      <el-table-column prop="recharge_cnt" label="充值笔数" width="90" align="right" sortable />
      <el-table-column label="充值金额(90天·元)" width="140" align="right" sortable :sort-by="(r) => r.recharge_amount">
        <template #default="{ row }">{{ fmtMoney(row.recharge_amount) }}</template>
      </el-table-column>
      <el-table-column prop="refund_cnt" label="退款笔数(30天)" width="120" align="right" sortable />
      <el-table-column prop="complaint_cnt" label="投诉数(30天)" width="110" align="right" sortable />
      <el-table-column label="投诉率/百台" width="110" align="right" sortable :sort-by="(r) => r.complaint_rate">
        <template #default="{ row }">
          <span :class="{ 'cb-rate-danger': row.complaint_rate > 5 }">{{ row.complaint_rate }}%</span>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="totalDimCount > limit" class="cb-more">
      共 {{ totalDimCount }} 个品类，仅显示充值金额 TOP{{ limit }}；全量请走数据查看/导出。
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import { categoryOps } from '@/api/analytics'
import BaseChart from '@/components/BaseChart.vue'

// ⓘ 数据说明（看板卡片双强制 §1：来源/口径/时点 + "空"的含义；§2：不下钻须声明理由）
const BOARD_TIP = `
<b>数据说明</b><br/>
· 来源：devices / dim_device_types / combo_dict / device_merchant_link(is_current) / device_combos / device_flow_usage / refunds / service_tickets<br/>
· 口径：活跃 = 近90天有充值或完整月流量；营收 = 近90天充值金额(元)；风险 = 近30天退款笔数 / 投诉数；投诉率 = 投诉数 ÷ 设备数 × 100（每百台）；投诉判据走全站唯一出口（服务类别空白或"投诉"）<br/>
· 时点：T+0 实时查询；汇总卡为 TOP10 合计（非全站总量）<br/>
· "空"的含义：该维度窗口内无任何设备/充值/退款/投诉记录<br/>
· 本模块<b>不下钻</b>：单设备明细由「设备360」覆盖（工单#254 取舍），避免重复入口<br/>
<br/>
<b>取舍</b><br/>
· 不做转化漏斗（无埋点/订单状态机）· 不做留存分析（属 B4）<br/>
· 不叠事件时间轴标注（x 轴是品类非时间；事件标注见「运营总览」趋势图）<br/>
· TOP10 截断防宽表，全量走导出
`

const limit = 10
const dim = ref('device_type')
const items = ref([])
const totalDimCount = ref(0)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await categoryOps.opsOverview({ dim: dim.value, limit })
    const d = res.data || {}
    items.value = d.items || []
    totalDimCount.value = d.total_dim_count || 0
  } catch {
    // 失败静默为空态（el-empty 提示"空"的含义），不阻断看板其它模块
    items.value = []
    totalDimCount.value = 0
  } finally {
    loading.value = false
  }
}
onMounted(load)

const _sum = (k) => items.value.reduce((a, b) => a + (Number(b[k]) || 0), 0)
const fmtMoney = (v) =>
  v >= 10000 ? (v / 10000).toFixed(1) + '万' : Math.round(v).toLocaleString('zh-CN')
const activeRate = (row) =>
  row.device_cnt ? ((row.active_cnt / row.device_cnt) * 100).toFixed(1) : '0.0'

// 汇总卡：TOPN 合计（卡面 sub 已标注口径，防误读为全站总量）
const kpiCards = computed(() => [
  { label: '品类数', value: totalDimCount.value, sub: `当前维度全部桶数`, bg: '#eff6ff', color: '#2563eb' },
  { label: `设备数 TOP${limit}合计`, value: _sum('device_cnt').toLocaleString('zh-CN'), sub: '规模 · 台', bg: '#f0fdf4', color: '#16a34a' },
  { label: `充值金额 TOP${limit}合计`, value: fmtMoney(_sum('recharge_amount')), sub: '营收 · 近90天(元)', bg: '#fff7ed', color: '#ea580c' },
  { label: `投诉数 TOP${limit}合计`, value: _sum('complaint_cnt'), sub: '风险 · 近30天(件)', bg: '#fef2f2', color: '#dc2626' },
])

// TOP10 对比图：充值金额(柱·左轴) + 投诉数(线·右轴)
// 双指标错位即决策信号：营收高+投诉低=优质品类；双高=需整改
const topChartOption = computed(() => {
  const names = items.value.map((i) => i.name)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: [`充值金额 TOP${limit}(90天·元)`, '投诉数(30天)'], top: 0 },
    grid: { left: 70, right: 56, top: 36, bottom: 64 },
    xAxis: {
      type: 'category',
      data: names,
      axisLabel: { rotate: names.some((n) => (n || '').length > 5) ? 28 : 0, interval: 0, fontSize: 11 },
    },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { formatter: (v) => (v >= 10000 ? v / 10000 + '万' : v) } },
      { type: 'value', name: '投诉(件)', minInterval: 1 },
    ],
    series: [
      {
        name: `充值金额 TOP${limit}(90天·元)`,
        type: 'bar',
        barMaxWidth: 36,
        itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] },
        data: items.value.map((i) => i.recharge_amount),
      },
      {
        name: '投诉数(30天)',
        type: 'line',
        yAxisIndex: 1,
        symbolSize: 7,
        lineStyle: { width: 2, color: '#dc2626' },
        itemStyle: { color: '#dc2626' },
        data: items.value.map((i) => i.complaint_cnt),
      },
    ],
  }
})
</script>

<style scoped>
.category-board {
  padding-top: 4px;
}
.cb-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.cb-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.cb-sub {
  font-size: 12px;
  font-weight: 400;
  color: #909399;
  margin-left: 8px;
}
.cb-tip-icon {
  color: #b0b4bb;
  cursor: help;
  font-size: 14px;
  margin-left: 6px;
  vertical-align: -2px;
}
.mb-12 {
  margin-bottom: 12px;
}
.cb-kpi {
  border-radius: 8px;
  padding: 10px 12px;
}
.cb-kpi-label {
  font-size: 12px;
}
.cb-kpi-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.3;
}
.cb-kpi-sub {
  font-size: 11px;
  color: #909399;
}
.cb-dim {
  color: #909399;
  font-size: 11px;
}
.cb-rate-danger {
  color: #dc2626;
  font-weight: 700;
}
.cb-more {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
  text-align: right;
}
@media (max-width: 768px) {
  .cb-kpi-value {
    font-size: 18px;
  }
}
</style>
