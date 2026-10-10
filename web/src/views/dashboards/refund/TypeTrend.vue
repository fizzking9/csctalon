<!--
  ┌─────────────────────────── 设计说明（Design Rationale） ───────────────────────────┐
  │ 目的：替**退款挽回主管**回答「哪类原因/哪类人在拖低挽回成功率、异常出现在哪几天」， │
  │       支撑「往哪个方向加人/改话术」的排班与拦截话术决策。                          │
  │ 逻辑：先原因维度定位问题类型 → 再人员类型定位是谁 → 热力图定位到具体日期×原因的    │
  │       异常点 → problem_type 保留原始文本防归一化掩盖新话术 → 组间对比做横向绩效。  │
  │ 口径：与「今日挽回」页签同源 —— 后端复用 _refund_recovery_core：                   │
  │       cs_records 退款类接待SN去重 + T+7 失败判定；窗口 = 父页筛选区间              │
  │       （近7/30/90天或自定义）；时间落桶 AT TIME ZONE 'Asia/Shanghai'。             │
  │       各卡口径详见卡片 ⓘ。                                                        │
  │ 取舍：① 不做按原因/按人员类型的SN级下钻 —— 后端无对应明细接口，强做只会给空明细页；│
  │       ② problem_type 不做归一化展示 —— 保留原始文本以便发现新退款话术；            │
  │       ③ 热力图只取近30天 —— 更早区间用趋势图看，避免图高过大不可读。               │
  │ 工单：#205                                                                        │
  └──────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="type-trend">
    <!-- ============ 筛选栏 ============ -->
    <section class="section-card">
      <div class="filter-bar">
        <span class="block-title" style="margin: 0">
          类型 × 趋势
          <!-- ★ #205 页面层可见设计说明（界面设计说明规范 §二.2.2：含「取舍」段） -->
          <el-tooltip raw-content effect="dark" placement="bottom"
            content="<b>目的</b>：回答「哪类原因/哪类人在拖低挽回成功率、异常出现在哪几天」<br/><b>逻辑</b>：原因维度定位问题 → 人员类型定位人 → 热力图定位日期×原因异常点 → problem_type 看原始细分 → 组间对比横向绩效<br/><b>口径</b>：与「今日挽回」同源（后端复用 _refund_recovery_core：退款类接待SN去重 + T+7 失败判定）；窗口=父页筛选区间（近7/30/90天或自定义）<br/><b>⚠️ 取舍（不做什么及原因）</b><br/>① 不做按原因/类型的SN级下钻 —— 后端无对应明细接口<br/>② problem_type 不归一化 —— 保留原始文本以便发现新退款话术<br/>③ 热力图只取近30天 —— 更早区间用趋势图看<br/><b>工单</b>：#205">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:14px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="trend-hint">粒度:</span>
        <el-radio-group v-model="grain" size="small" @change="loadTrend">
          <el-radio-button value="day">按日</el-radio-button>
          <el-radio-button value="week">按周</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" size="small" type="primary" :loading="loading" @click="loadAll">刷新</el-button>
      </div>
    </section>

    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <!-- ============ 1. 各原因类型 接待量&成功率 (双Y轴堆叠柱+多线) ============ -->
    <section v-loading="loading" class="section-card">
      <div class="chart-header">
        <span class="block-title" style="margin: 0">
          各原因类型 接待量 & 成功率
          <!-- ★ #205 数据说明 -->
          <el-tooltip raw-content effect="dark" placement="top"
            content="<b>来源</b>：GET /analytics/refund/type-trend?dimension=reason<br/><b>口径</b>：分母=退款类接待SN去重（同「今日挽回」口径），失败=T+7 判定；原因=cs_records.problem_type 归一化 7 大类，SN按接待日(Asia/Shanghai)落桶；堆叠柱=各原因接待SN，线=各原因成功率；粒度按日/按周（周以周一为始）<br/><b>时点</b>：窗口=父页筛选区间；接口缓存 120s<br/><b>「空」的含义</b>：该日该原因无接待SN则为 0；区间内无接待显示「暂无原因类型趋势数据」<br/><b>下钻</b>：不下钻 —— 原因聚合已是归因终点，后端无按原因的SN明细接口（规范 §3.4）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="trend-hint">左Y=SN数(7大原因堆叠) · 右Y=成功率%(每原因一条线)</span>
      </div>
      <BaseChart v-if="reasonHasData" :option="reasonOption" height="360px" />
      <el-empty v-else description="暂无原因类型趋势数据" />
    </section>

    <!-- ============ 2. 各人员类型 接待量&成功率 (双Y轴分组柱+多线) ============ -->
    <section v-loading="loading" class="section-card">
      <div class="chart-header">
        <span class="block-title" style="margin: 0">
          各人员类型 接待量 & 成功率
          <!-- ★ #205 数据说明 -->
          <el-tooltip raw-content effect="dark" placement="top"
            content="<b>来源</b>：GET /analytics/refund/type-trend?dimension=staff_type<br/><b>口径</b>：人员三类 —— 专职挽回(Person.specialty=退款挽回) / 其他人员挽回(非专职接待且未产生退款) / 退款操作(仅执行退款操作无接待)；分组柱=接待SN，虚线=各类型成功率；失败=T+7 判定<br/><b>时点</b>：窗口=父页筛选区间；接口缓存 120s<br/><b>「空」的含义</b>：该日该类型无接待SN则为 0<br/><b>下钻</b>：点右上「人员明细」→ 切到「人员」页签（带当前窗口）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="trend-hint">左Y=SN数(分组柱) · 右Y=成功率%(每类型一条线)</span>
        <!-- ★ #205 下钻（规范 §3.2①）：人员类型 → 人，切父页「人员」页签；窗口经 props 透传不丢 -->
        <el-link type="primary" :underline="false" size="small" @click="goPersonnel">人员明细 →</el-link>
      </div>
      <BaseChart v-if="staffTypeHasData" :option="staffTypeOption" height="360px" />
      <el-empty v-else description="暂无人员类型趋势数据" />
    </section>

    <!-- ============ 3. 日期×原因 矩阵热力图 ============ -->
    <section class="section-card">
      <div class="chart-header">
        <span class="block-title" style="margin: 0">
          日期 × 原因 矩阵热力图 (近30天)
          <!-- ★ #205 数据说明 -->
          <el-tooltip raw-content effect="dark" placement="top"
            content="<b>来源</b>：复用「各原因类型」接口数据（/analytics/refund/type-trend?dimension=reason）<br/><b>口径</b>：行=日期（近30天，最新在上）、列=7大归一化原因；单元格=失败数/接待数/成功率（右侧切换）；色阶：失败/接待=绿→红（越大越差），成功率=红→绿（越高越好）<br/><b>时点</b>：窗口=父页筛选区间（前端截取近30天）<br/><b>「空」的含义</b>：该格无接待SN，tooltip 显示「无数据」<br/><b>下钻</b>：不下钻 —— 单元格 tooltip 已含接待/成功/失败/成功率全量指标，无更深明细（规范 §3.4）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <el-radio-group v-model="matrixMode" size="small">
          <el-radio-button value="failed">失败数</el-radio-button>
          <el-radio-button value="total">接待数</el-radio-button>
          <el-radio-button value="rate">成功率</el-radio-button>
        </el-radio-group>
      </div>
      <BaseChart v-if="heatmapHasData" :option="heatmapOption" height="560px" />
      <el-empty v-else description="暂无热力图数据" />
    </section>

    <!-- ============ 4. problem_type 细分 Top10 (横向堆叠柱) ============ -->
    <section v-loading="loading" class="section-card">
      <div class="chart-header">
        <span class="block-title" style="margin: 0">
          problem_type 细分 Top10
          <!-- ★ #205 数据说明 -->
          <el-tooltip raw-content effect="dark" placement="top"
            content="<b>来源</b>：GET /analytics/refund/problem-type-detail?top=10<br/><b>口径</b>：按原始 problem_type 文本分组（<b>不做</b>7大类归一化）、退款类接待SN去重；成功/失败=与 T+7 失败SN集求交；金额=该类失败SN的失败金额合计；横向堆叠柱=成功/失败SN，旁注成功率<br/><b>时点</b>：窗口=父页筛选区间；接口缓存 120s<br/><b>「空」的含义</b>：区间内无退款接待或记录缺 problem_type 文本时为空<br/><b>下钻</b>：不下钻 —— problem_type 已是最细原始文本维度（规范 §3.4）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="trend-hint">横向堆叠柱(成功/失败) · 旁注成功率%</span>
      </div>
      <BaseChart v-if="problemTypeHasData" :option="problemTypeOption" height="420px" />
      <el-empty v-else description="暂无 problem_type 细分数据" />
    </section>

    <!-- ============ 5. 组间对比 (el-table + BaseChart横向柱) ============ -->
    <section v-loading="loading" class="section-card">
      <div class="block-title" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span>
          组间对比
          <!-- ★ #205 数据说明 -->
          <el-tooltip raw-content effect="dark" placement="top"
            content="<b>来源</b>：GET /analytics/refund/group-comparison<br/><b>口径</b>：按人员表组名(Person.group_name)聚合人员明细；组成功率=组接待SN口径（成功 ÷ 接待）；人均成功率=组内人员成功率的算术平均；退款操作金额=组内成员执行退款合计；<b>挽回估算金额 = 组内成功SN × 同归一化原因的失败SN平均金额</b>（无同原因均值时用整体失败均值），是估算值非实收<br/><b>时点</b>：窗口=父页筛选区间；接口缓存 120s<br/><b>「空」的含义</b>：区间内无退款接待时显示「暂无组对比数据」<br/><b>下钻</b>：组 → 人，点右侧「人员明细」切到「人员」页签（带当前窗口）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <!-- ★ #205 下钻（规范 §3.2①）：组 → 人，切父页「人员」页签；窗口经 props 透传不丢 -->
        <el-link type="primary" :underline="false" size="small" @click="goPersonnel">人员明细 →</el-link>
      </div>
      <el-row :gutter="16">
        <el-col :xs="24" :md="14">
          <el-table :data="groupData" border stripe size="small" max-height="360" style="width: 100%">
            <el-table-column prop="group" label="组名" width="120" fixed />
            <el-table-column prop="person_cnt" label="人数" width="70" align="right" sortable :sort-method="(a,b)=>numCmp(a.person_cnt,b.person_cnt)">
              <template #default="{ row }">{{ formatNum(row.person_cnt) }}</template>
            </el-table-column>
            <el-table-column prop="total_sn" label="接待SN" width="90" align="right" sortable :sort-method="(a,b)=>numCmp(a.total_sn,b.total_sn)">
              <template #default="{ row }">{{ formatNum(row.total_sn) }}</template>
            </el-table-column>
            <el-table-column prop="success_sn" label="成功" width="70" align="right" sortable :sort-method="(a,b)=>numCmp(a.success_sn,b.success_sn)">
              <template #default="{ row }"><span style="color: #16a34a">{{ formatNum(row.success_sn) }}</span></template>
            </el-table-column>
            <el-table-column prop="failed_sn" label="失败" width="70" align="right" sortable :sort-method="(a,b)=>numCmp(a.failed_sn,b.failed_sn)">
              <template #default="{ row }"><span style="color: #dc2626">{{ formatNum(row.failed_sn) }}</span></template>
            </el-table-column>
            <el-table-column prop="success_rate" label="成功率" width="100" align="center" sortable :sort-method="(a,b)=>numCmp(a.success_rate,b.success_rate)">
              <template #default="{ row }">
                <el-tag size="small" :type="rateTagType(row.success_rate)" :effect="rateTagEffect(row.success_rate)">
                  {{ rateText(row.success_rate) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="total_refund_op_amount" label="退款操作金额" width="130" align="right" sortable :sort-method="(a,b)=>numCmp(a.total_refund_op_amount,b.total_refund_op_amount)">
              <template #default="{ row }">
                <span :style="{ color: num(row.total_refund_op_amount) > 0 ? '#b91c1c' : '#9ca3af', fontWeight: num(row.total_refund_op_amount) > 0 ? 600 : 400 }">
                  ¥{{ formatMoney(row.total_refund_op_amount) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="estimated_save_amount" label="挽回估算金额" width="130" align="right" sortable :sort-method="(a,b)=>numCmp(a.estimated_save_amount,b.estimated_save_amount)">
              <template #default="{ row }">
                <span :style="{ color: num(row.estimated_save_amount) > 0 ? '#16a34a' : '#9ca3af' }">
                  ¥{{ formatMoney(row.estimated_save_amount) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="avg_success_rate" label="人均成功率" width="100" align="center" sortable :sort-method="(a,b)=>numCmp(a.avg_success_rate,b.avg_success_rate)">
              <template #default="{ row }">
                <el-tag size="small" :type="rateTagType(row.avg_success_rate)" effect="plain">
                  {{ rateText(row.avg_success_rate) }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-col>
        <el-col :xs="24" :md="10">
          <BaseChart v-if="groupHasData" :option="groupOption" height="360px" />
          <el-empty v-else description="暂无组对比数据" />
        </el-col>
      </el-row>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { Refresh, QuestionFilled } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { refundDeep } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
})

// ★ #205 下钻：切到父页「人员」页签看按人明细（emit 给 RefundRecovery 处理）
const emit = defineEmits(['switch-tab'])
function goPersonnel() {
  emit('switch-tab', 'personnel')
}

// ---- 状态 ----
const loading = ref(false)
const errorMsg = ref('')
const grain = ref('day')          // 粒度: day/week
const matrixMode = ref('failed')  // 热力图色阶模式: failed/total/rate

// 数据
const reasonData = ref({ dates: [], reasons: [], series: [] })
const staffTypeData = ref({ dates: [], staff_types: [], series: [] })
const problemTypeData = ref({ items: [] })
const groupData = ref([])

// 颜色配置
const REASON_COLORS = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#6366f1', '#ec4899']
const STAFF_TYPE_COLORS = {
  '专职挽回': '#059669',
  '其他人员挽回': '#6366f1',
  '退款操作': '#f97316',
}

// ============ 工具函数 ============
function num(v) {
  const n = Number(v)
  return Number.isNaN(n) ? 0 : n
}
function numCmp(a, b) {
  return num(a) - num(b)
}
function formatNum(v) {
  const n = Number(v)
  if (v == null || v === '' || Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}
// 保留1位小数
function round1(v) {
  return Math.round(num(v) * 10) / 10
}
function rateText(v) {
  if (v == null || v === '') return '-'
  return Number(v).toFixed(1) + '%'
}
// 成功率颜色: ≥38% 绿 / 30-38% 黄 / <30% 红
function rateColor(v) {
  const n = Number(v || 0)
  if (n >= 38) return '#16a34a'
  if (n >= 30) return '#ca8a04'
  return '#dc2626'
}
function rateTagType(v) {
  const n = Number(v || 0)
  if (n >= 38) return 'success'
  if (n >= 30) return 'warning'
  return 'danger'
}
function rateTagEffect(v) {
  return Number(v || 0) >= 38 ? 'dark' : 'plain'
}
function pickStaffColor(name) {
  return STAFF_TYPE_COLORS[name] || '#6b7280'
}

// ============ 数据加载 ============
// 加载趋势数据 (原因维度 + 人员类型维度 并行)
async function loadTrend() {
  errorMsg.value = ''
  try {
    const params = { start: props.start, end: props.end, grain: grain.value }
    const [reasonRes, staffRes] = await Promise.all([
      refundDeep.typeTrend({ ...params, dimension: 'reason' }),
      refundDeep.typeTrend({ ...params, dimension: 'staff_type' }),
    ])
    reasonData.value = reasonRes.data || { dates: [], reasons: [], series: [] }
    staffTypeData.value = staffRes.data || { dates: [], staff_types: [], series: [] }
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载趋势数据失败'
  }
}

// 加载 problem_type 细分 Top10
async function loadProblemType() {
  try {
    const res = await refundDeep.problemTypeDetail({ start: props.start, end: props.end, top: 10 })
    problemTypeData.value = res.data || { items: [] }
  } catch (e) {
    problemTypeData.value = { items: [] }
  }
}

// 加载组间对比
async function loadGroupComparison() {
  try {
    const res = await refundDeep.groupComparison({ start: props.start, end: props.end })
    groupData.value = res.data?.groups || []
  } catch (e) {
    groupData.value = []
  }
}

// 全量加载
async function loadAll() {
  loading.value = true
  errorMsg.value = ''
  try {
    await Promise.all([loadTrend(), loadProblemType(), loadGroupComparison()])
  } finally {
    loading.value = false
  }
}

// ============ 图表配置 ============

// ---- 1. 各原因类型 双Y轴堆叠柱+多线 ----
const reasonHasData = computed(() => {
  const d = reasonData.value
  return d.dates && d.dates.length && d.series && d.series.length
})

const reasonOption = computed(() => {
  const { dates = [], reasons = [], series = [] } = reasonData.value
  if (!dates.length || !series.length) return {}

  // X轴日期 (短格式 MM-DD)
  const xData = dates.map(d => (d && d.length >= 10 ? d.slice(5) : d))

  // 堆叠柱: 每个原因一层 (total_sn)
  const barSeries = reasons.map((reason, idx) => {
    const s = series.find(x => x.reason === reason)
    const trendMap = new Map()
    if (s && Array.isArray(s.trend)) {
      for (const t of s.trend) trendMap.set(String(t.date), t)
    }
    const color = REASON_COLORS[idx % REASON_COLORS.length]
    return {
      name: reason,
      type: 'bar',
      stack: 'reason_total',
      data: dates.map(d => {
        const t = trendMap.get(String(d))
        return t ? num(t.total_sn) : 0
      }),
      itemStyle: { color },
      barMaxWidth: 24,
    }
  })

  // 多线: 每个原因一条成功率线
  const lineSeries = reasons.map((reason, idx) => {
    const s = series.find(x => x.reason === reason)
    const trendMap = new Map()
    if (s && Array.isArray(s.trend)) {
      for (const t of s.trend) trendMap.set(String(t.date), t)
    }
    const color = REASON_COLORS[idx % REASON_COLORS.length]
    return {
      name: `${reason} 成功率`,
      type: 'line',
      yAxisIndex: 1,
      data: dates.map(d => {
        const t = trendMap.get(String(d))
        return t ? round1(t.success_rate) : 0
      }),
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      itemStyle: { color },
      lineStyle: { width: 2, color },
    }
  })

  const legendData = [...reasons, ...reasons.map(r => `${r} 成功率`)]

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const dateIdx = params[0].dataIndex
        const date = dates[dateIdx]
        let html = `<b>${date}</b><br/>`
        html += '<table style="font-size:12px;border-spacing:0 2px;">'
        html += '<tr><th align="left" style="padding-right:12px">原因</th><th align="right" style="padding-right:8px">接待</th><th align="right" style="padding-right:8px">成功</th><th align="right">成功率</th></tr>'
        for (let i = 0; i < reasons.length; i++) {
          const reason = reasons[i]
          const s = series.find(x => x.reason === reason)
          const t = s?.trend?.find(x => String(x.date) === String(date))
          if (!t) continue
          const color = REASON_COLORS[i]
          html += `<tr>
            <td align="left"><span style="display:inline-block;width:8px;height:8px;background:${color};border-radius:50%;margin-right:4px"></span>${reason}</td>
            <td align="right" style="padding-right:8px">${num(t.total_sn)}</td>
            <td align="right" style="padding-right:8px;color:#16a34a">${num(t.success_sn)}</td>
            <td align="right" style="color:#0f766e">${round1(t.success_rate)}%</td>
          </tr>`
        }
        html += '</table>'
        return html
      },
    },
    legend: { data: legendData, top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, top: 60, bottom: 56 },
    xAxis: { type: 'category', data: xData, boundaryGap: true, axisLabel: { fontSize: 11, rotate: dates.length > 14 ? 30 : 0 } },
    yAxis: [
      { type: 'value', name: 'SN数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '成功率%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [...barSeries, ...lineSeries],
  }
})

// ---- 2. 各人员类型 双Y轴分组柱+多线 ----
const staffTypeHasData = computed(() => {
  const d = staffTypeData.value
  return d.dates && d.dates.length && d.series && d.series.length
})

const staffTypeOption = computed(() => {
  const { dates = [], staff_types = [], series = [] } = staffTypeData.value
  if (!dates.length || !series.length) return {}

  const xData = dates.map(d => (d && d.length >= 10 ? d.slice(5) : d))

  // 分组柱: 每个人员类型一组 (total_sn)
  const barSeries = staff_types.map((st) => {
    const s = series.find(x => x.staff_type === st)
    const trendMap = new Map()
    if (s && Array.isArray(s.trend)) {
      for (const t of s.trend) trendMap.set(String(t.date), t)
    }
    const color = pickStaffColor(st)
    return {
      name: st,
      type: 'bar',
      data: dates.map(d => {
        const t = trendMap.get(String(d))
        return t ? num(t.total_sn) : 0
      }),
      itemStyle: { color },
      barMaxWidth: 20,
    }
  })

  // 多线: 每个人员类型一条成功率线 (虚线区分)
  const lineSeries = staff_types.map((st) => {
    const s = series.find(x => x.staff_type === st)
    const trendMap = new Map()
    if (s && Array.isArray(s.trend)) {
      for (const t of s.trend) trendMap.set(String(t.date), t)
    }
    const color = pickStaffColor(st)
    return {
      name: `${st} 成功率`,
      type: 'line',
      yAxisIndex: 1,
      data: dates.map(d => {
        const t = trendMap.get(String(d))
        return t ? round1(t.success_rate) : 0
      }),
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      itemStyle: { color },
      lineStyle: { width: 2, color, type: 'dashed' },
    }
  })

  const legendData = [...staff_types, ...staff_types.map(s => `${s} 成功率`)]

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const dateIdx = params[0].dataIndex
        const date = dates[dateIdx]
        let html = `<b>${date}</b><br/>`
        html += '<table style="font-size:12px;border-spacing:0 2px;">'
        html += '<tr><th align="left" style="padding-right:12px">人员类型</th><th align="right" style="padding-right:8px">接待</th><th align="right" style="padding-right:8px">成功</th><th align="right" style="padding-right:8px">失败</th><th align="right">成功率</th></tr>'
        for (const st of staff_types) {
          const s = series.find(x => x.staff_type === st)
          const t = s?.trend?.find(x => String(x.date) === String(date))
          if (!t) continue
          html += `<tr>
            <td align="left"><span style="display:inline-block;width:8px;height:8px;background:${pickStaffColor(st)};border-radius:50%;margin-right:4px"></span>${st}</td>
            <td align="right" style="padding-right:8px">${num(t.total_sn)}</td>
            <td align="right" style="padding-right:8px;color:#16a34a">${num(t.success_sn)}</td>
            <td align="right" style="padding-right:8px;color:#dc2626">${num(t.failed_sn)}</td>
            <td align="right" style="color:#0f766e">${round1(t.success_rate)}%</td>
          </tr>`
        }
        html += '</table>'
        return html
      },
    },
    legend: { data: legendData, top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, top: 60, bottom: 56 },
    xAxis: { type: 'category', data: xData, boundaryGap: true, axisLabel: { fontSize: 11, rotate: dates.length > 14 ? 30 : 0 } },
    yAxis: [
      { type: 'value', name: 'SN数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '成功率%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [...barSeries, ...lineSeries],
  }
})

// ---- 3. 日期×原因 矩阵热力图 (复用 reasonData) ----
const heatmapHasData = computed(() => {
  const d = reasonData.value
  return d.dates && d.dates.length && d.reasons && d.reasons.length && d.series && d.series.length
})

const heatmapOption = computed(() => {
  const { dates: allDates = [], reasons = [], series = [] } = reasonData.value
  if (!allDates.length || !reasons.length || !series.length) return {}

  // 只取近30天
  const dates = allDates.length > 30 ? allDates.slice(-30) : allDates.slice()
  // 日期倒序: 最新的在上 (yAxis)
  const yDates = [...dates].reverse()

  // 构建热力图数据 [[xIndex, yIndex, value, trendItem], ...]
  // 第4位存 trendItem 供 tooltip 使用
  const data = []
  let maxVal = 0
  for (let xi = 0; xi < reasons.length; xi++) {
    const reason = reasons[xi]
    const s = series.find(x => x.reason === reason)
    const trendMap = new Map()
    if (s && Array.isArray(s.trend)) {
      for (const t of s.trend) trendMap.set(String(t.date), t)
    }
    for (let yi = 0; yi < yDates.length; yi++) {
      const d = yDates[yi]
      const t = trendMap.get(String(d))
      let val = 0
      if (t) {
        if (matrixMode.value === 'failed') val = num(t.failed_sn)
        else if (matrixMode.value === 'total') val = num(t.total_sn)
        else val = round1(t.success_rate)
      }
      if (val > maxVal) maxVal = val
      data.push([xi, yi, val, t])
    }
  }

  const isRate = matrixMode.value === 'rate'
  // 颜色色阶: 失败数/接待数 = 绿→红 (低好高差); 成功率 = 红→绿 (低差高好)
  const colors = isRate
    ? ['#dc2626', '#f59e0b', '#eab308', '#84cc16', '#16a34a']
    : ['#22c55e', '#84cc16', '#eab308', '#f59e0b', '#dc2626']

  return {
    tooltip: {
      formatter: (p) => {
        const reason = reasons[p.data[0]]
        const date = yDates[p.data[1]]
        const t = p.data[3]
        const val = p.data[2]
        const valLabel = isRate ? `${val}%` : val
        if (!t) return `<b>${date} · ${reason}</b><br/>无数据`
        return `<b>${date} · ${reason}</b><br/>` +
          `接待: <b>${num(t.total_sn)}</b><br/>` +
          `成功: <b>${num(t.success_sn)}</b><br/>` +
          `失败: <b>${num(t.failed_sn)}</b><br/>` +
          `成功率: <b>${round1(t.success_rate)}%</b><br/>` +
          `当前指标: <b>${valLabel}</b>`
      },
    },
    grid: { left: 100, right: 24, top: 20, bottom: 70 },
    xAxis: {
      type: 'category',
      data: reasons,
      splitArea: { show: true },
      axisLabel: { fontSize: 11, rotate: 18, interval: 0 },
    },
    yAxis: {
      type: 'category',
      data: yDates,
      splitArea: { show: true },
      axisLabel: { fontSize: 10 },
    },
    visualMap: {
      min: 0,
      max: isRate ? 100 : Math.max(maxVal, 1),
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: { color: colors },
      text: ['高', '低'],
      textStyle: { fontSize: 11 },
    },
    series: [{
      name: isRate ? '成功率' : (matrixMode.value === 'failed' ? '失败数' : '接待数'),
      type: 'heatmap',
      data,
      label: {
        show: true,
        formatter: (p) => p.data[2],
        fontSize: 10,
      },
      emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.3)' } },
    }],
  }
})

// ---- 4. problem_type 细分 Top10 横向堆叠柱 ----
const problemTypeHasData = computed(() => {
  return problemTypeData.value?.items && problemTypeData.value.items.length > 0
})

const problemTypeOption = computed(() => {
  const items = problemTypeData.value?.items || []
  if (!items.length) return {}

  // Y轴=problem_type (倒序, Top1在最上面)
  const sorted = [...items].sort((a, b) => num(b.total_sn) - num(a.total_sn))
  const yData = sorted.map(it => it.problem_type || '(未知)')

  const successData = sorted.map(it => num(it.success_sn))
  const failedData = sorted.map(it => num(it.failed_sn))
  const rateData = sorted.map(it => round1(it.success_rate))

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = sorted[i] || {}
        return `<b>${it.problem_type || '-'}</b><br/>` +
          `归一化原因: ${it.normalized_reason || '-'}<br/>` +
          `接待SN: <b>${num(it.total_sn)}</b><br/>` +
          `成功: <span style="color:#16a34a">${num(it.success_sn)}</span> / 失败: <span style="color:#dc2626">${num(it.failed_sn)}</span><br/>` +
          `成功率: <b>${round1(it.success_rate)}%</b><br/>` +
          `金额: ¥${formatMoney(num(it.total_amount))}`
      },
    },
    legend: { data: ['成功SN', '失败SN'], top: 0 },
    grid: { left: 160, right: 80, top: 36, bottom: 24 },
    xAxis: { type: 'value', name: 'SN数', axisLabel: { fontSize: 11 } },
    yAxis: {
      type: 'category',
      data: yData,
      axisLabel: { fontSize: 11, width: 140, overflow: 'truncate' },
    },
    series: [
      {
        name: '成功SN',
        type: 'bar',
        stack: 'total',
        data: successData,
        itemStyle: { color: '#16a34a' },
        barMaxWidth: 22,
      },
      {
        name: '失败SN',
        type: 'bar',
        stack: 'total',
        data: failedData,
        itemStyle: { color: '#dc2626' },
        barMaxWidth: 22,
        // 旁注成功率%
        label: {
          show: true,
          position: 'right',
          fontSize: 11,
          formatter: (p) => `${rateData[p.dataIndex]}%`,
          color: '#374151',
        },
      },
    ],
  }
})

// ---- 5. 组间对比 横向柱 ----
const groupHasData = computed(() => groupData.value && groupData.value.length > 0)

const groupOption = computed(() => {
  const groups = groupData.value || []
  if (!groups.length) return {}

  // 按成功率升序 (低在下, 高在上)
  const sorted = [...groups].sort((a, b) => num(a.success_rate) - num(b.success_rate))
  const yData = sorted.map(g => g.group || '(未命名)')
  const rateData = sorted.map(g => round1(g.success_rate))
  const avgRateData = sorted.map(g => round1(g.avg_success_rate))

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const g = sorted[i] || {}
        return `<b>${g.group || '-'}</b><br/>` +
          `成功率: ${round1(g.success_rate)}%<br/>` +
          `人均成功率: ${round1(g.avg_success_rate)}%<br/>` +
          `接待SN: ${num(g.total_sn)} / 人数: ${num(g.person_cnt)}<br/>` +
          `退款操作金额: ¥${formatMoney(num(g.total_refund_op_amount))}<br/>` +
          `挽回估算金额: ¥${formatMoney(num(g.estimated_save_amount))}`
      },
    },
    legend: { data: ['组成功率', '人均成功率'], top: 0 },
    grid: { left: 100, right: 56, top: 36, bottom: 24 },
    xAxis: { type: 'value', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' } },
    yAxis: { type: 'category', data: yData, axisLabel: { fontSize: 11 } },
    series: [
      {
        name: '组成功率',
        type: 'bar',
        data: rateData,
        itemStyle: { color: '#059669' },
        barMaxWidth: 18,
        label: { show: true, position: 'right', fontSize: 11, formatter: '{c}%' },
      },
      {
        name: '人均成功率',
        type: 'bar',
        data: avgRateData,
        itemStyle: { color: '#6366f1' },
        barMaxWidth: 18,
        label: { show: true, position: 'right', fontSize: 11, formatter: '{c}%' },
      },
    ],
  }
})

// ============ 生命周期 ============
// 监听 props 变化自动加载
watch(
  () => [props.start, props.end],
  () => { loadAll() }
)

onMounted(loadAll)
</script>

<style scoped>
.type-trend {
  width: 100%;
}
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
}
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.trend-hint {
  font-size: 12px;
  color: #6b7280;
}
.error-msg {
  color: #f56c6c;
  padding: 12px;
  background: #fef0f0;
  border-radius: 4px;
  margin-bottom: 16px;
}

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .filter-bar { gap: 6px; }
  .block-title { font-size: 14px; }
  .trend-hint { font-size: 11px; }
  .section-card { padding: 12px; }
  .chart-header { flex-wrap: wrap; gap: 6px; }
  :deep(.el-table) { min-width: 620px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
  :deep(.el-card) { overflow: hidden; }
}
</style>
