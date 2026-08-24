<template>
  <div class="decision-overview" v-loading="loading">
    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>
    <template v-if="data">
      <!-- ===== 区块0: 管理摘要(结论条 + KPI) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">管理摘要</span>
          <span class="block-hint">自动结论 · 随筛选区间更新</span>
        </div>
        <el-row :gutter="12" class="mb-12">
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#eff6ff">
              <div class="kpi-label" style="color:#2563eb">现金流出总额</div>
              <div class="kpi-value">¥{{ formatMoney(summary.cash_out_total) }}</div>
              <div class="kpi-sub">{{ summary.cash_out_count }} 笔 · 四路经营合计</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f0fdf4">
              <div class="kpi-label" style="color:#16a34a">较上一周期</div>
              <div class="kpi-value" :style="{ color: chainColor }">{{ chainPctText }}</div>
              <div class="kpi-sub">同长度窗口对比</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fef2f2">
              <div class="kpi-label" style="color:#dc2626">涉诉退款占比</div>
              <div class="kpi-value">{{ summary.complaint_ratio }}<span class="unit">%</span></div>
              <div class="kpi-sub">涉诉 ¥{{ formatMoney(summary.complaint_amount) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fff7ed">
              <div class="kpi-label" style="color:#ea580c">钱包净水位</div>
              <div class="kpi-value" :style="{ color: netColor }">{{ summary.wallet_net >= 0 ? '+' : '' }}{{ formatMoney(summary.wallet_net) }}</div>
              <div class="kpi-sub">转入 − 转出（含系统扣费）</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f5f3ff">
              <div class="kpi-label" style="color:#7c3aed">线上直退</div>
              <div class="kpi-value">{{ summary.online_count }}<span class="unit">笔</span></div>
              <div class="kpi-sub">¥{{ formatMoney(summary.online_amount) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fefce8">
              <div class="kpi-label" style="color:#ca8a04">商户号实际现金流</div>
              <div class="kpi-value">¥{{ formatMoney(summary.actual_cashflow) }}</div>
              <div class="kpi-sub">{{ summary.actual_cashflow_count }} 笔 · 对账基准 E<template v-if="summary.e_data_until">（数据截止 {{ summary.e_data_until }}）</template></div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f0fdfa">
              <div class="kpi-label" style="color:#0f766e">对账偏差</div>
              <div class="kpi-value" :style="{ color: (summary.reconcile_diff ?? 0) >= 0 ? '#dc2626' : '#16a34a' }">{{ (summary.reconcile_diff ?? 0) >= 0 ? '+' : '' }}{{ formatMoney(summary.reconcile_diff) }}</div>
              <div class="kpi-sub">经营四路 − 实际E（不累加）</div>
            </div>
          </el-col>
        </el-row>
        <el-alert
          v-if="summary.conclusions && summary.conclusions.length"
          type="info" :closable="false" show-icon
        >
          <div v-for="(c, i) in summary.conclusions" :key="i" class="conclusion-line">{{ c }}</div>
        </el-alert>
      </section>

      <!-- ===== 区块1: 资金流出全景(钱出去多少) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">一、资金流出全景</span>
          <span class="block-hint">四路经营 A/B/C/D（堆叠）= 经营估算 · E 商户号实际现金流（曲线，对账基准，不与四路累加）</span>
        </div>
        <el-alert v-if="summary.e_data_until" type="warning" :closable="false" show-icon class="mb-12">
          <template #title>商户号现金流(E)为数据快照，截止 {{ summary.e_data_until }}。截止日后 E=0，对账偏差仅供参考，需持续同步商户号流水。</template>
        </el-alert>
        <BaseChart v-if="routeOption.series?.length" :option="routeOption" height="300px" />
        <el-empty v-else description="暂无资金流出数据" :image-size="60" />
        <el-table :data="routes.table" border stripe size="small" class="mt-12" max-height="260">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="A 线上直退" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['A 线上直退_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['A 线上直退_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="B 钱包提现" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['B 钱包提现_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['B 钱包提现_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="C 闪猫退款" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['C 闪猫退款_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['C 闪猫退款_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="D 手动退款" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['D 手动退款_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['D 手动退款_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="四路合计" align="right" width="150">
            <template #default="{ row }">
              <span class="num strong">{{ row.total_cnt }}笔</span>
              <span class="amt strong">¥{{ formatMoney(row.total_amt) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="商户号实际(E)" align="right" width="150">
            <template #default="{ row }">
              <span class="num">{{ row.actual_cnt }}笔</span>
              <span class="amt" style="color:#ca8a04">¥{{ formatMoney(row.actual_amt) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="对账偏差" align="right" fixed="right" width="150">
            <template #default="{ row }">
              <span class="amt" :style="{ color: (row.diff_amt ?? 0) >= 0 ? '#dc2626' : '#16a34a' }">{{ (row.diff_amt ?? 0) >= 0 ? '+' : '' }}{{ formatMoney(row.diff_amt) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块2: 钱包水位(钱卡在哪) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">二、钱包水位监控</span>
          <span class="block-hint">判断: 净水位过高=沉淀+待提现负债(挤兑/投诉风险) · 过低需区分转入不足 vs 过账快(结合涉诉比率判把关) → 行动: 排查提现顺畅度/查转入端/审视退款政策</span>
        </div>
        <BaseChart v-if="waterOption.series?.length" :option="waterOption" height="300px" />
        <el-empty v-else description="暂无钱包水位数据" :image-size="60" />
        <el-table :data="waterTable" border stripe size="small" class="mt-12" max-height="240">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="转入(订单退费转入)" align="right">
            <template #default="{ row }"><span class="num">{{ row.tin_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.tin_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="转出(用户提现)" align="right">
            <template #default="{ row }"><span class="num">{{ row.wd_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.wd_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="系统扣费" align="right">
            <template #default="{ row }"><span class="num">{{ row.fee_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.fee_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="当日净水位" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.net >= 0 ? 'pos' : 'neg'">{{ row.net >= 0 ? '+' : '' }}{{ formatMoney(row.net) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="累计净水位" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.cum >= 0 ? 'pos' : 'neg'">{{ row.cum >= 0 ? '+' : '' }}{{ formatMoney(row.cum) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块3: 手动退款登记水位(登记 vs 已退 = 蓄水) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">三、手动退款登记水位</span>
          <span class="block-hint">登记(商户号手机退款处理登记) − 已退款完成 = 蓄水 · 手动操作为主,存在登记后未退的积压</span>
        </div>
        <div class="water-summary mb-12" v-if="manualWaterTable.length">
          登记蓄水总额 <b :class="manualWaterTotal >= 0 ? 'pos' : 'neg'">{{ manualWaterTotal >= 0 ? '+' : '' }}¥{{ formatMoney(manualWaterTotal) }}</b>
          （登记 ¥{{ formatMoney(manualRegTotal) }} − 已退 ¥{{ formatMoney(manualDoneTotal) }}）
        </div>
        <BaseChart v-if="manualWaterOption.series?.length" :option="manualWaterOption" height="300px" />
        <el-empty v-else description="暂无手动退款登记数据" :image-size="60" />
        <el-table :data="manualWaterTable" border stripe size="small" class="mt-12" max-height="240">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="登记(登记日期)" align="right">
            <template #default="{ row }"><span class="num">{{ row.reg_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.reg_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="已退款完成(操作时间)" align="right">
            <template #default="{ row }"><span class="num">{{ row.done_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.done_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="当日蓄水" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.net >= 0 ? 'pos' : 'neg'">{{ row.net >= 0 ? '+' : '' }}{{ formatMoney(row.net) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="累计蓄水" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.cum >= 0 ? 'pos' : 'neg'">{{ row.cum >= 0 ? '+' : '' }}{{ formatMoney(row.cum) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块4: 涉诉归因(风险敞口) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">四、涉诉 vs 普通退款</span>
          <span class="block-hint">线上直退 · 涉诉=SN关联工单(外部&gt;商户号&gt;内部) 或 审核备注投诉标识(涉投诉/客诉/315/争议等) · 判断: 占比高=该退才退(控制到位,同时盯投诉绝对量) / 占比低=警惕宽松退款 → 行动: 转投诉/商品质量专项</span>
        </div>
        <BaseChart v-if="complaintOption.series?.length" :option="complaintOption" height="300px" />
        <el-empty v-else description="暂无涉诉归因数据" :image-size="60" />
        <el-table :data="complaintTable" border stripe size="small" class="mt-12" max-height="240">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="内部投诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.int_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.int_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="商户号投诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.mer_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.mer_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="外部投诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.ext_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.ext_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="备注涉诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.rmk_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.rmk_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="涉诉合计" align="right">
            <template #default="{ row }"><span class="num strong">{{ row.comp_cnt }}笔</span><span class="amt strong">¥{{ formatMoney(row.comp_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="普通退款" align="right">
            <template #default="{ row }"><span class="num">{{ row.nor_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.nor_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="涉诉占比" align="right" width="100">
            <template #default="{ row }"><el-tag size="small" :type="row.ratio >= 40 ? 'danger' : row.ratio >= 25 ? 'warning' : 'info'">{{ row.ratio }}%</el-tag></template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块5: 原因归因 + 人员归因(为什么退/谁在退) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">五、退款原因 & 操作人员</span>
          <span class="block-hint">线上直退 · 判断: 主因类别与主力人员 → 行动: 针对主因解决上游问题(网络质量/商品问题/话术)以减少退款 · 未关联工单占比高时原因覆盖不足</span>
        </div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="14" class="mb-12">
            <div class="sub-title">退款原因 Top（按笔数）</div>
            <BaseChart v-if="reasonOption.series?.length" :option="reasonOption" height="320px" />
            <el-empty v-else description="暂无原因数据" :image-size="60" />
          </el-col>
          <el-col :xs="24" :md="10" class="mb-12">
            <div class="sub-title">操作人员 Top（按金额）</div>
            <el-table :data="operators" border stripe size="small" max-height="360">
              <el-table-column prop="name" label="人员" min-width="120" fixed />
              <el-table-column prop="count" label="笔数" width="70" align="right" sortable />
              <el-table-column prop="amount" label="金额(¥)" width="110" align="right" sortable>
                <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
              </el-table-column>
              <el-table-column prop="avg_amount" label="均单(¥)" width="100" align="right">
                <template #default="{ row }">{{ formatMoney(row.avg_amount) }}</template>
              </el-table-column>
            </el-table>
          </el-col>
        </el-row>
      </section>

      <!-- ===== 区块6: 明细下钻(可查可追) ===== -->
      <section class="block-card">
        <div class="block-head">
          <span class="block-title">六、退款明细</span>
          <span class="block-hint">共 {{ detailTotal }} 条 · 点击 SN 可查看设备审计 · 全量数据可点右上角「导出」下载 Excel</span>
        </div>
        <el-table :data="detailList" border stripe size="small" max-height="420">
          <el-table-column prop="refund_no" label="退款单号" width="150" fixed show-overflow-tooltip />
          <el-table-column prop="device_sn" label="设备SN" width="140" fixed>
            <template #default="{ row }">
              <span class="sn-chip" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="refund_reason" label="退款原因" min-width="130" show-overflow-tooltip />
          <el-table-column prop="operator" label="操作人" width="110" show-overflow-tooltip />
          <el-table-column prop="refund_amount" label="金额(¥)" width="110" align="right" sortable>
            <template #default="{ row }">{{ formatMoney(row.refund_amount) }}</template>
          </el-table-column>
          <el-table-column prop="d1_refund_created" label="发起时间" width="160" show-overflow-tooltip />
        </el-table>
        <div class="pag-wrap">
          <el-pagination
            v-model:current-page="detailPage"
            v-model:page-size="detailPageSize"
            :total="detailTotal"
            :page-sizes="[30, 50, 100, 200]"
            layout="total, sizes, prev, pager, next, jumper"
            background
            small
            @current-change="loadDetail"
            @size-change="detailPage = 1; loadDetail()"
          />
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import BaseChart from '@/components/BaseChart.vue'
import { refundDeep, refundWithdrawal } from '@/api/analytics'
import { useAudit } from '@/composables/useAudit'
import { formatMoney } from '@/utils/format'

const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  grain: { type: String, default: 'day' },
  reloadKey: { type: Number, default: 0 },
})

const { openDeviceAudit } = useAudit()

const loading = ref(false)
const errorMsg = ref('')
const data = ref(null)
const detailList = ref([])
const detailPage = ref(1)
const detailPageSize = ref(50)
const detailTotal = ref(0)

const summary = computed(() => data.value?.summary || {})
const routes = computed(() => data.value?.routes_daily || { dates: [], series: {}, table: [] })
const water = computed(() => data.value?.wallet_water || { dates: [] })
const complaint = computed(() => data.value?.complaint_split || { dates: [] })
const operators = computed(() => data.value?.reason_operator?.operators || [])

const chainPctText = computed(() => {
  const p = summary.value.chain_pct
  if (p == null) return '—'
  return (p >= 0 ? '+' : '') + Number(p).toFixed(1) + '%'
})
const chainColor = computed(() => {
  const p = Number(summary.value.chain_pct || 0)
  return p >= 0 ? '#dc2626' : '#16a34a'
})
const netColor = computed(() => (Number(summary.value.wallet_net || 0) >= 0 ? '#ea580c' : '#16a34a'))

const waterTable = computed(() => {
  const w = water.value
  if (!w.dates?.length) return []
  return w.dates.map((d, i) => ({
    date: d,
    tin_cnt: w.transfer_in_cnt?.[i] ?? 0, tin_amt: w.transfer_in_amt?.[i] ?? 0,
    wd_cnt: w.withdraw_cnt?.[i] ?? 0, wd_amt: w.withdraw_amt?.[i] ?? 0,
    fee_cnt: w.fee_cnt?.[i] ?? 0, fee_amt: w.fee_amt?.[i] ?? 0,
    net: w.net?.[i] ?? 0, cum: w.cum_net?.[i] ?? 0,
  }))
})

// ---- 手动退款登记水位(登记 vs 已退 = 蓄水) ----
const mwater = computed(() => data.value?.manual_water || { dates: [], reg_cnt: [], reg_amt: [], done_cnt: [], done_amt: [], net: [], cum_net: [] })
const manualWaterTable = computed(() => {
  const m = mwater.value
  if (!m.dates?.length) return []
  return m.dates.map((d, i) => ({
    date: d,
    reg_cnt: m.reg_cnt?.[i] ?? 0, reg_amt: m.reg_amt?.[i] ?? 0,
    done_cnt: m.done_cnt?.[i] ?? 0, done_amt: m.done_amt?.[i] ?? 0,
    net: m.net?.[i] ?? 0, cum: m.cum_net?.[i] ?? 0,
  }))
})
const manualWaterTotal = computed(() => data.value?.summary?.manual_water_total ?? 0)
const manualRegTotal = computed(() => mwater.value.reg_amt?.reduce((a, b) => a + (b || 0), 0) || 0)
const manualDoneTotal = computed(() => mwater.value.done_amt?.reduce((a, b) => a + (b || 0), 0) || 0)

const manualWaterOption = computed(() => {
  const m = mwater.value
  if (!m.dates?.length) return {}
  const dates = m.dates.map(d => String(d).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['登记', '已退', '蓄水', '累计蓄水'], top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '蓄水', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      { name: '登记', type: 'bar', stack: 'm', data: m.reg_amt, itemStyle: { color: '#f59e0b' }, barMaxWidth: 30 },
      { name: '已退', type: 'bar', stack: 'm', data: m.done_amt, itemStyle: { color: '#34d399' }, barMaxWidth: 30 },
      { name: '蓄水', type: 'line', yAxisIndex: 1, data: m.net, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
      { name: '累计蓄水', type: 'line', yAxisIndex: 1, data: m.cum_net, smooth: true, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' } },
    ],
  }
})

const complaintTable = computed(() => {
  const c = complaint.value
  if (!c.dates?.length) return []
  return c.dates.map((d, i) => ({
    date: d,
    int_cnt: c.internal_cnt?.[i] ?? 0, int_amt: c.internal_amt?.[i] ?? 0,
    mer_cnt: c.merchant_cnt?.[i] ?? 0, mer_amt: c.merchant_amt?.[i] ?? 0,
    ext_cnt: c.external_cnt?.[i] ?? 0, ext_amt: c.external_amt?.[i] ?? 0,
    rmk_cnt: c.remark_cnt?.[i] ?? 0, rmk_amt: c.remark_amt?.[i] ?? 0,
    comp_cnt: c.complaint_cnt?.[i] ?? 0, comp_amt: c.complaint_amt?.[i] ?? 0,
    nor_cnt: c.normal_cnt?.[i] ?? 0, nor_amt: c.normal_amt?.[i] ?? 0,
    ratio: c.ratio?.[i] ?? 0,
  }))
})

// ---- 图表 ----
const ROUTE_COLORS = {
  'A 线上直退': '#2563eb', 'B 钱包提现': '#f59e0b', 'C 闪猫退款': '#10b981', 'D 手动退款': '#a78bfa',
}

const routeOption = computed(() => {
  const r = routes.value
  if (!r.dates?.length) return {}
  const keys = Object.keys(ROUTE_COLORS)
  // E 实际现金流(对账基准) 作为独立曲线 overlay, 不与四路堆叠累加
  const eAmt = (r.table || []).map(row => row.actual_amt ?? 0)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: [...keys, '商户号实际(E)'], top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: r.dates.map(d => String(d).slice(5)), axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '实际(E)', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      ...keys.map(k => ({
        name: k, type: 'bar', stack: 'routes', barMaxWidth: 40,
        data: r.series[k]?.amounts || [],
        itemStyle: { color: ROUTE_COLORS[k] },
      })),
      { name: '商户号实际(E)', type: 'line', yAxisIndex: 1, data: eAmt, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#ca8a04' }, lineStyle: { width: 2.5, color: '#ca8a04' }, z: 10 },
    ],
  }
})

const waterOption = computed(() => {
  const w = water.value
  if (!w.dates?.length) return {}
  const dates = w.dates.map(d => String(d).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['转入', '转出', '净水位', '累计净水位'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '净水位', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      { name: '转入', type: 'bar', stack: 'w', data: w.transfer_in_amt, itemStyle: { color: '#10b981' }, barMaxWidth: 30 },
      { name: '转出', type: 'bar', stack: 'w', data: w.withdraw_amt, itemStyle: { color: '#f87171' }, barMaxWidth: 30 },
      { name: '净水位', type: 'line', yAxisIndex: 1, data: w.net, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
      { name: '累计净水位', type: 'line', yAxisIndex: 1, data: w.cum_net, smooth: true, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' } },
    ],
  }
})

const COMP_COLORS = { '内部投诉': '#f97316', '商户号投诉': '#eab308', '外部投诉': '#dc2626', '备注涉诉': '#8b5cf6', '普通退款': '#9ca3af' }

const complaintOption = computed(() => {
  const c = complaint.value
  if (!c.dates?.length) return {}
  const dates = c.dates.map(d => String(d).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: Object.keys(COMP_COLORS).concat(['涉诉占比%']), top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '占比%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '内部投诉', type: 'bar', stack: 'c', data: c.internal_amt, itemStyle: { color: COMP_COLORS['内部投诉'] }, barMaxWidth: 40 },
      { name: '商户号投诉', type: 'bar', stack: 'c', data: c.merchant_amt, itemStyle: { color: COMP_COLORS['商户号投诉'] }, barMaxWidth: 40 },
      { name: '外部投诉', type: 'bar', stack: 'c', data: c.external_amt, itemStyle: { color: COMP_COLORS['外部投诉'] }, barMaxWidth: 40 },
      { name: '备注涉诉', type: 'bar', stack: 'c', data: c.remark_amt, itemStyle: { color: COMP_COLORS['备注涉诉'] }, barMaxWidth: 40 },
      { name: '普通退款', type: 'bar', stack: 'c', data: c.normal_amt, itemStyle: { color: COMP_COLORS['普通退款'] }, barMaxWidth: 40 },
      { name: '涉诉占比%', type: 'line', yAxisIndex: 1, data: c.ratio, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' } },
    ],
  }
})

const reasonOption = computed(() => {
  const list = data.value?.reason_operator?.reasons || []
  if (!list.length) return {}
  const sorted = [...list].sort((a, b) => b.count - a.count).slice(0, 10)
  const yData = sorted.map(r => r.name)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const i = params[0].dataIndex
        const it = sorted[i] || {}
        return `<b>${it.name}</b><br/>笔数: <b>${it.count}</b><br/>金额: ¥${formatMoney(it.amount)}<br/>占比: ${it.ratio}%`
      } },
    grid: { left: 150, right: 90, top: 16, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    yAxis: { type: 'category', data: yData, axisLabel: { fontSize: 11, width: 130, overflow: 'truncate' } },
    series: [{
      name: '笔数', type: 'bar', data: sorted.map(r => r.count), barMaxWidth: 18,
      itemStyle: { color: '#6366f1', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', fontSize: 11, formatter: (p) => `${p.value}笔` },
    }],
  }
})

async function loadDetail() {
  const params = {}
  if (props.start) params.start = props.start + 'T00:00:00'
  if (props.end) params.end = props.end + 'T23:59:59'
  params.sort = 'd1_desc'
  params.page = detailPage.value
  params.page_size = detailPageSize.value
  try {
    const res = await refundWithdrawal.detail(params)
    detailTotal.value = res?.data?.total || 0
    detailList.value = res?.data?.list || []
  } catch (e) {
    detailList.value = []
  }
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const params = { grain: props.grain }
    if (props.start) params.start = props.start
    if (props.end) params.end = props.end
    const [ovRes] = await Promise.all([refundDeep.decisionOverview(params)])
    data.value = ovRes?.data || null
    detailPage.value = 1
    loadDetail()
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载决策概览失败'
  } finally {
    loading.value = false
  }
}

watch(() => [props.start, props.end, props.grain, props.reloadKey], load)
onMounted(load)
</script>

<style scoped>
.decision-overview { width: 100%; }
.block-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.block-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 600; color: #111827; }
.block-hint { font-size: 12px; color: #6b7280; }
.sub-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 10px; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mt-12 { margin-top: 12px; }
.error-msg { color: #f56c6c; padding: 12px; background: #fef0f0; border-radius: 4px; margin-bottom: 16px; }
.conclusion-line { line-height: 1.8; font-size: 13px; color: #334155; }
.kpi-card { border-radius: 10px; padding: 12px 14px 10px; border: 1px solid rgba(148,163,184,0.15); display: flex; flex-direction: column; gap: 3px; min-height: 84px; }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { margin-top: 2px; font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; }
.unit { font-size: 12px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
.num { color: #374151; margin-right: 8px; }
.num.strong { font-weight: 700; }
.amt { color: #9ca3af; font-size: 12px; }
.amt.strong { font-weight: 600; color: #374151; }
.pos { color: #ea580c; font-weight: 600; }
.neg { color: #16a34a; font-weight: 600; }
.water-summary { font-size: 13px; color: #374151; padding: 8px 12px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0; }
.water-summary b { font-size: 14px; margin-left: 4px; }
.sn-chip { color: #2563eb; cursor: pointer; font-weight: 600; text-decoration: underline; text-decoration-style: dotted; }
.sn-chip:hover { color: #1d4ed8; }
.pag-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
