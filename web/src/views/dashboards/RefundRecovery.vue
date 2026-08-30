<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">挽回分析</div>
      <div class="filter-bar" :class="{ 'is-mobile': isMobile }">
        <el-radio-group v-model="tabMode" size="small" @change="onTabChange" class="tab-mode-group">
          <el-radio-button label="today">今日</el-radio-button>
          <el-radio-button label="personnel">人员</el-radio-button>
          <el-radio-button label="device">设备</el-radio-button>
          <el-radio-button label="type">类型</el-radio-button>
        </el-radio-group>
        <el-radio-group v-if="tabMode !== 'today'" v-model="rangeMode" size="small" class="range-mode-group">
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
          <el-radio-button label="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="tabMode !== 'today' && rangeMode === 'custom'"
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width: 240px"
        />
        <el-button type="primary" :icon="Search" :loading="analyzing" @click="runAnalysis">分析</el-button>
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

    <template v-else>
    <!-- ============ TAB1: 今日挽回 (原退款看板-今日视图) ============ -->
    <template v-if="tabMode === 'today'">
      <section class="section-card mb-24">
        <div class="flex-between mb-12">
          <div>
            <div class="block-title">今日退款挽回 · KPI 核心</div>
            <div class="block-sub">新口径:退款专项组接待真退款类问题 4类 · T+7 判定失败</div>
          </div>
          <el-tag size="small" effect="dark" type="danger">{{ todayDateStr }}</el-tag>
        </div>
        <el-row :gutter="12" class="mb-12">
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#eff6ff">
              <div class="kpi-mini-label" style="color:#2563eb">总接待SN(分母)</div>
              <div class="kpi-mini-sub">退款专员真退款类去重</div>
              <div class="kpi-mini-value" style="color:#2563eb">
                {{ formatNum(recoveryData.kpi.total_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#f0fdf4">
              <div class="kpi-mini-label" style="color:#16a34a">挽留成功</div>
              <div class="kpi-mini-sub">未进入退款流程</div>
              <div class="kpi-mini-value" style="color:#16a34a">
                {{ formatNum(recoveryData.kpi.success_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#fef2f2">
              <div class="kpi-mini-label" style="color:#dc2626">挽留失败</div>
              <div class="kpi-mini-sub">最终完成退款</div>
              <div class="kpi-mini-value" style="color:#dc2626">
                {{ formatNum(recoveryData.kpi.failed_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" :style="rateBg(recoveryData.kpi.success_rate)">
              <div class="kpi-mini-label" :style="rateColor(recoveryData.kpi.success_rate)">挽回成功率</div>
              <div class="kpi-mini-sub">成功SN / 总接待SN</div>
              <div class="kpi-mini-value" :style="rateColor(recoveryData.kpi.success_rate)">
                {{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#f3f4f6">
              <div class="kpi-mini-label" style="color:#6b7280">放弃单数</div>
              <div class="kpi-mini-sub">系统清退·无需退款等</div>
              <div class="kpi-mini-value" style="color:#6b7280">{{ formatNum(recoveryData.kpi.abandon_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.abandon_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#fef2f2">
              <div class="kpi-mini-label" style="color:#dc2626">失败单数</div>
              <div class="kpi-mini-sub">拦截失败·真实退款成功</div>
              <div class="kpi-mini-value" style="color:#dc2626">{{ formatNum(recoveryData.kpi.fail_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.fail_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#f0fdf4">
              <div class="kpi-mini-label" style="color:#16a34a">成功单数</div>
              <div class="kpi-mini-sub">拦截成功·金额暂不核算</div>
              <div class="kpi-mini-value" style="color:#16a34a">{{ formatNum(recoveryData.kpi.success_cnt) }}<span class="kpi-mini-unit">单</span></div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#eff6ff">
              <div class="kpi-mini-label" style="color:#2563eb">退充单</div>
              <div class="kpi-mini-sub">正常退充·金额互抵</div>
              <div class="kpi-mini-value" style="color:#2563eb">{{ formatNum(recoveryData.kpi.recharge_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.recharge_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-mini-card" style="background:#f0fdfa">
              <div class="kpi-mini-label" style="color:#0f766e">目标总单量</div>
              <div class="kpi-mini-value" style="color:#0f766e">{{ formatNum(recoveryData.kpi.target_total_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.target_total_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-mini-card" style="background:#fff7ed">
              <div class="kpi-mini-label" style="color:#7c2d12">实际退款金额</div>
              <div class="kpi-mini-sub">失败金额 + 退充金额</div>
              <div class="kpi-mini-money" style="font-size:22px;color:#7c2d12;font-weight:700;margin-top:4px">¥{{ formatMoney(Number(recoveryData.kpi.actual_refund_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="24" :sm="8" :md="4">
            <div class="kpi-mini-card" :style="feeRatioBg(recoveryData.kpi.fee_ratio)">
              <div class="kpi-mini-label" :style="feeRatioColor(recoveryData.kpi.fee_ratio)">拦截费占比 (单量)</div>
              <div class="kpi-mini-sub">成功单数/(成功+实际退款单数) · 目标37%+</div>
              <div class="kpi-mini-value" :style="feeRatioColor(recoveryData.kpi.fee_ratio)">
                {{ Number(recoveryData.kpi.fee_ratio||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
              </div>
            </div>
          </el-col>
        </el-row>
      </section>

      <section class="section-card mb-24">
        <div class="block-title mb-12">按时间粒度监控</div>
        <el-row :gutter="16">
          <el-col :xs="24" :lg="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    今日 按小时 · 挽回趋势 (08:00 ~ 当前)
                    <el-tooltip
                      content="失败 = 接待后 7 天内出现退款(退款单/退款工单/退款操作记录)。今日接待的退款可能未走完 7 天窗口，失败数偏低、成功率偏高，仅供参考"
                      placement="top"
                    >
                      <el-icon style="vertical-align:-2px;color:#b45309;cursor:help"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="success">日视图 · 小时粒度</el-tag>
                </div>
              </template>
              <BaseChart v-if="todayHourlyOption.series?.length" :option="todayHourlyOption" :height="isMobile ? '250px' : '340px'" />
              <el-empty v-else description="今日暂无小时级趋势数据" />
            </el-card>
          </el-col>
          <el-col :xs="24" :lg="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    近 7 天 · 按日 挽回趋势
                    <el-tooltip
                      content="失败 = 接待后 7 天内出现退款即判失败(T+7)。退款工单可能未同步，漏判会导致失败偏低、成功率偏高，仅供参考"
                      placement="top"
                    >
                      <el-icon style="vertical-align:-2px;color:#b45309;cursor:help"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="warning">日视图 · 日粒度</el-tag>
                </div>
              </template>
              <BaseChart v-if="dailyTrendOption.series?.length" :option="dailyTrendOption" :height="isMobile ? '250px' : '340px'" />
              <el-empty v-else description="暂无日趋势数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>

      <section class="section-card mb-24">
        <div class="block-title mb-12">高风险设备 · 金额分布</div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="14" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">Top5 高风险设备 (按退款金额倒序)</span>
                  <el-tag size="small" effect="plain" type="danger">T+7 失败</el-tag>
                </div>
              </template>
              <el-table :data="recoveryData.top_risk_devices || []" border stripe size="small" max-height="320">
                <el-table-column prop="device_sn" label="设备SN" min-width="140" fixed>
                  <template #default="{row}"><span style="font-weight:600">{{ row.device_sn }}</span></template>
                </el-table-column>
                <el-table-column prop="reason" label="失败原因" width="120">
                  <template #default="{row}">
                    <el-tag v-if="row.reason" size="small" :type="reasonTagType(row.reason)" effect="light">{{ row.reason }}</el-tag>
                    <span v-else style="color:#9ca3af">—</span>
                  </template>
                </el-table-column>
                <el-table-column prop="amount" label="退款金额" width="120" align="right" sortable>
                  <template #default="{row}">
                    <span :style="{color: Number(row.amount)>1000?'#dc2626':'#374151', fontWeight:600}">¥{{ formatMoney(row.amount) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="operator_name" label="接待人" min-width="100">
                  <template #default="{row}">{{ row.operator_name || row.operator || '—' }}</template>
                </el-table-column>
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{row}">
                    <el-tag v-if="row.status" size="small" :type="statusTagType(row.status)" effect="plain">{{ row.status }}</el-tag>
                    <span v-else style="color:#9ca3af">—</span>
                  </template>
                </el-table-column>
              </el-table>
              <el-empty v-if="!(recoveryData.top_risk_devices && recoveryData.top_risk_devices.length)" description="暂无高风险设备" />
            </el-card>
          </el-col>
          <el-col :xs="24" :md="10" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">失败设备 · 金额分桶</span>
                  <el-tag size="small" effect="plain" type="info">按退款金额区间</el-tag>
                </div>
              </template>
              <BaseChart v-if="amountBucketOption.series?.length" :option="amountBucketOption" height="320px" />
              <el-empty v-else description="暂无金额分桶数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>

      <section class="section-card">
        <div class="block-title mb-12">归因 · 绩效 (今日视角)</div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">挽留失败 SN 原因分布(归一化 7 大类)</span>
                  <el-tag size="small" effect="plain" type="info">柱=单量 / 线=占比%</el-tag>
                </div>
              </template>
              <BaseChart v-if="recoveryData.reason_dist?.length" :option="reasonBarOption" height="320px" />
              <el-empty v-else description="暂无原因分布数据" />
            </el-card>
          </el-col>
          <el-col :xs="24" :md="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">退款专员 接待/挽回/操作 明细</span>
                  <el-tag size="small" effect="plain" type="primary">按 接待SN 倒序</el-tag>
                </div>
              </template>
              <el-table :data="recoveryData.staff_detail || []" border stripe size="small" max-height="360">
                <el-table-column label="姓名" width="100" fixed><template #default="{row}"><span style="font-weight:600">{{row.name}}</span></template></el-table-column>
                <el-table-column label="组" width="96"><template #default="{row}"><el-tag size="small" effect="plain" :type="grpTagType(row.group)">{{row.group||'未分组'}}</el-tag></template></el-table-column>
                <el-table-column prop="total_sn" label="接待SN" width="82" align="right" sortable><template #default="{row}">{{formatNum(row.total_sn)}}个</template></el-table-column>
                <el-table-column prop="success_sn" label="成功" width="64" align="right"><template #default="{row}"><span style="color:#16a34a">{{formatNum(row.success_sn)}}</span></template></el-table-column>
                <el-table-column prop="failed_sn" label="失败" width="64" align="right"><template #default="{row}"><span style="color:#dc2626">{{formatNum(row.failed_sn)}}</span></template></el-table-column>
                <el-table-column prop="success_rate" label="成功率" width="84" align="right" sortable :sort-method="(a,b)=>Number(a.success_rate??-1)-Number(b.success_rate??-1)">
                  <template #default="{row}">
                    <template v-if="row.success_rate==null"><span style="color:#9ca3af">—</span></template>
                    <template v-else>
                      <el-tag size="small" :effect="Number(row.success_rate||0)>=38?'dark':'plain'" :type="Number(row.success_rate||0)>=38?'success':(Number(row.success_rate||0)>=30?'warning':'danger')">
                        {{Number(row.success_rate||0).toFixed(1)}}%
                      </el-tag>
                    </template>
                  </template>
                </el-table-column>
                <el-table-column prop="refund_op_cnt" label="退款操作SN" width="100" align="right" sortable><template #default="{row}"><span :style="{color:Number(row.refund_op_cnt||0)>0?'#1d4ed8':'#9ca3af',fontWeight:Number(row.refund_op_cnt||0)>0?600:400}">{{formatNum(row.refund_op_cnt)}}个</span></template></el-table-column>
                <el-table-column prop="refund_op_amount" label="退款操作金额" width="116" align="right" sortable><template #default="{row}"><span :style="{color:Number(row.refund_op_amount||0)>0?'#b91c1c':'#9ca3af',fontWeight:Number(row.refund_op_amount||0)>0?600:400}">¥{{formatMoney(row.refund_op_amount)}}</span></template></el-table-column>
              </el-table>
              <el-empty v-if="!recoveryData.staff_detail?.length" description="暂无人员数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>
    </template>

    <!-- ============ TAB2: 人员分析 ============ -->
    <PersonnelAnalysis v-else-if="tabMode==='personnel'" :start="effectiveStart" :end="effectiveEnd" />

    <!-- ============ TAB3: 设备分析 ============ -->
    <DeviceAnalysis v-else-if="tabMode==='device'" :start="effectiveStart" :end="effectiveEnd" />

    <!-- ============ TAB4: 类型趋势 ============ -->
    <TypeTrend v-else :start="effectiveStart" :end="effectiveEnd" />
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Search, QuestionFilled } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import BaseChart from '@/components/BaseChart.vue'
import PersonnelAnalysis from './refund/PersonnelAnalysis.vue'
import DeviceAnalysis from './refund/DeviceAnalysis.vue'
import TypeTrend from './refund/TypeTrend.vue'
import { today } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

const tabMode = ref('today')
const rangeMode = ref('30')
const customRange = ref([])
const loading = ref(false)
const analyzed = ref(false)
const analyzing = ref(false)

// 移动端响应式: 简化筛选栏文字/隐藏次要控件
const isMobile = ref(false)
let _mq = null
function _onMediaChange(e) { isMobile.value = e.matches }
onMounted(() => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
})
onBeforeUnmount(() => { _mq?.removeEventListener('change', _onMediaChange) })

const todayDateStr = computed(() => dayjs().format('YYYY-MM-DD (dddd)'))

const effectiveStart = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  return dayjs().format('YYYY-MM-DD')
})

function onTabChange() {
  // Tab 切换不自动加载, 保持"点分析才装载"约定
  analyzed.value = false
}

function runAnalysis() {
  analyzed.value = true
  analyzing.value = true
  if (tabMode.value === 'today') {
    loadAll()
  }
  setTimeout(() => { analyzing.value = false }, 300)
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}
function round2(v) {
  const n = Number(v || 0)
  return Math.round(n * 10) / 10
}

// ---- 三元样式 helpers ----
function rateBg(val) {
  const n = Number(val || 0)
  return { background: n >= 38 ? '#f0fdf4' : n >= 30 ? '#fefce8' : '#fef2f2' }
}
function rateColor(val) {
  const n = Number(val || 0)
  return { color: n >= 38 ? '#16a34a' : n >= 30 ? '#ca8a04' : '#dc2626' }
}
function feeRatioBg(val) {
  const n = Number(val || 0)
  return { background: n >= 37 ? '#f0fdf4' : '#fef2f2' }
}
function feeRatioColor(val) {
  const n = Number(val || 0)
  return { color: n >= 37 ? '#16a34a' : '#dc2626' }
}

const GRP_COLOR_MAP = {
  '朱海洋组': 'success', '董梦玲组': 'warning', '吴晓龙组': 'primary', '叶珍凤组': 'danger', '投诉专项组': 'info',
}
function grpTagType(g) {
  if (!g || g === '未分组') return 'info'
  return GRP_COLOR_MAP[g] || 'info'
}
const REASON_TAG_MAP = { '不想用了': 'info', '充值错误': 'warning', '设备故障': 'danger', '服务原因': 'primary', '其他': 'info' }
function reasonTagType(r) {
  if (!r) return 'info'
  return REASON_TAG_MAP[r] || 'info'
}
function statusTagType(s) {
  if (!s) return 'info'
  const t = String(s)
  if (t.includes('成功') || t.includes('完成')) return 'success'
  if (t.includes('失败') || t.includes('驳回')) return 'danger'
  if (t.includes('处理') || t.includes('审核')) return 'warning'
  return 'info'
}

// ---- 今日挽回数据 ----
function emptyKpi() {
  return {
    total_sn: 0, success_sn: 0, failed_sn: 0, success_rate: 0,
    abandon_cnt: 0, fail_cnt: 0, success_cnt: 0, recharge_cnt: 0, target_total_cnt: 0,
    abandon_amount: 0, fail_amount: 0, success_amount: 0, recharge_amount: 0,
    target_total_amount: 0, actual_refund_amount: 0, fee_ratio: 0,
  }
}
const recoveryData = ref({
  kpi: emptyKpi(), reason_dist: [], staff_detail: [], daily_trend: [], hourly_today: [],
  top_risk_devices: [], amount_buckets: [],
})

function _buildDoubleYOption({ x, barSeries = [], lineSeries = [], y2Min = null, y2Max = null, y2Suffix = '', legend = [], stack = false }) {
  const y1 = { type: 'value', name: '数量', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } }
  const y2 = {
    type: 'value', name: y2Suffix ? `百分比(${y2Suffix})` : '右',
    axisLabel: { fontSize: 11, formatter: y2Suffix ? `{value}${y2Suffix}` : '{value}' },
    splitLine: { show: false },
  }
  if (y2Min !== null && y2Min !== undefined) y2.min = y2Min
  if (y2Max !== null && y2Max !== undefined) y2.max = y2Max
  const series = []
  for (const b of barSeries) {
    series.push({
      name: b.name, type: 'bar', data: b.data, barMaxWidth: 20,
      stack: stack ? 'total' : undefined,
      itemStyle: { color: b.color || '#93c5fd', borderRadius: stack ? [0, 0, 0, 0] : [4, 4, 0, 0] },
    })
  }
  // 堆叠模式: 最后一根柱子的顶部加圆角, 形成圆顶效果
  if (stack && series.length) {
    const lastBar = series[series.length - 1]
    lastBar.itemStyle = { ...lastBar.itemStyle, borderRadius: [4, 4, 0, 0] }
  }
  for (const l of lineSeries) {
    series.push({ name: l.name, type: 'line', yAxisIndex: 1, data: l.data, smooth: true, symbol: 'circle', symbolSize: 7, itemStyle: { color: l.color || '#15803d' }, lineStyle: { width: l.lineWidth || 3, color: l.color || '#15803d' } })
  }
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      formatter: stack ? (params) => {
        // 堆叠模式: 汇总柱状系列总量, 并附带成功率线
        if (!params || !params.length) return ''
        const bars = params.filter(p => p.seriesType === 'bar')
        const line = params.find(p => p.seriesType === 'line')
        let html = `<b>${params[0].axisValue}</b>`
        let total = 0
        for (const b of bars) {
          total += Number(b.value) || 0
          html += `<br/>${b.marker}${b.seriesName}: <b>${Number(b.value) || 0}</b>`
        }
        if (line) html += `<br/>${line.marker}${line.seriesName}: <b>${line.value}</b>`
        html += `<br/>合计: <b>${total}</b>`
        return html
      } : undefined,
    },
    legend: { data: legend || [], top: 0, type: 'scroll' },
    grid: { left: 56, right: 72, bottom: 44, top: 44 },
    xAxis: { type: 'category', data: x, boundaryGap: true, axisLabel: { fontSize: 11 } },
    yAxis: [y1, y2],
    series,
    animationDuration: 400,
  }
}

const todayHourlyOption = computed(() => {
  const hl = recoveryData.value?.hourly_today || []
  if (!hl || !hl.length) return {}
  // 客服工作时段: 只展示 08:00 ~ 23:59, 过滤凌晨 0~7 点(基本无接待,全是空)
  const workHours = hl.filter(r => {
    const h = parseInt(r.hour, 10)
    return !isNaN(h) && h >= 8 && h <= 23
  })
  if (!workHours.length) return {}
  const x = workHours.map(r => r.hour)
  const succ = workHours.map(r => Number(r.success_sn) || 0)
  const fail = workHours.map(r => Number(r.failed_sn) || 0)
  const rates = workHours.map(r => {
    const t = Number(r.total_sn) || 0
    return t ? round2(Number(r.success_sn) / t * 100) : 0
  })
  return _buildDoubleYOption({
    x, stack: true, barSeries: [
      // 失败在下(红)、成功在上(绿), 堆叠总高 = 总接待量
      { name: '失败数', data: fail, color: '#fca5a5' },
      { name: '成功数', data: succ, color: '#34d399' },
    ],
    lineSeries: [{ name: '成功率%', data: rates, color: '#047857', lineWidth: 3 }],
    y2Min: 0, y2Max: 100, y2Suffix: '%',
    legend: ['成功数', '失败数', '成功率%'],
  })
})

const dailyTrendOption = computed(() => {
  const dl = recoveryData.value?.daily_trend || []
  if (!dl || !dl.length) return {}
  const sliceN = Math.min(7, dl.length)
  const arr = dl.slice(-sliceN)
  const x = arr.map(r => r.date ? String(r.date).slice(5) : '')
  const succ = arr.map(r => Number(r.success_sn) || 0)
  const fail = arr.map(r => Number(r.failed_sn) || 0)
  const rates = arr.map(r => round2(Number(r.success_rate) || 0))
  return _buildDoubleYOption({
    x, stack: true, barSeries: [
      // 失败在下(红)、成功在上(绿), 堆叠总高 = 总接待量
      { name: '失败数', data: fail, color: '#f87171' },
      { name: '成功数', data: succ, color: '#4ade80' },
    ],
    lineSeries: [{ name: '成功率%', data: rates, color: '#059669', lineWidth: 3 }],
    y2Min: 0, y2Max: 100, y2Suffix: '%',
    legend: ['成功数', '失败数', '成功率%'],
  })
})

const reasonBarOption = computed(() => {
  const list = recoveryData.value?.reason_dist || []
  if (!list.length) return {}
  const labels = list.map(x => x.reason)
  const cnts = list.map(x => Number(x.order_cnt) || 0)
  const ratios = list.map(x => round2(Number(x.ratio) || 0))
  const pal = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#6366f1', '#ec4899', '#0ea5e9']
  const barData = cnts.map((v, i) => ({ value: v, itemStyle: { color: pal[i % pal.length], borderRadius: [4, 4, 0, 0] } }))
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = list[i] || {}
        return `<b>${it.reason}</b><br/>失败SN: <b>${Number(it.order_cnt)||0}</b> (占比 ${round2(Number(it.ratio)||0)}%)<br/>涉及金额: ¥${formatMoney(Number(it.amount)||0)}`
      }
    },
    grid: { left: 56, right: 64, top: 36, bottom: 56 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11, rotate: 12, interval: 0 } },
    yAxis: [
      { type: 'value', name: '失败SN数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '占比%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    legend: { data: ['失败SN数', '占比%'], top: 0, type: 'scroll' },
    series: [
      { name: '失败SN数', type: 'bar', data: barData, barMaxWidth: 34, label: { show: true, position: 'top', fontSize: 11, formatter: (p) => `${p.value} (${ratios[p.dataIndex]}%)` } },
      { name: '占比%', type: 'line', yAxisIndex: 1, data: ratios, smooth: true, symbol: 'circle', symbolSize: 7, itemStyle: { color: '#7c3aed' }, lineStyle: { width: 3, color: '#7c3aed' } },
    ],
  }
})

const amountBucketOption = computed(() => {
  const list = recoveryData.value?.amount_buckets || []
  if (!list.length) return {}
  const palette = ['#93c5fd', '#fbbf24', '#fb923c', '#dc2626']
  const data = list.map((b, i) => ({ name: b.bucket, value: b.sn_cnt, amount: b.amount, ratio: b.ratio, itemStyle: { color: palette[i % palette.length] } }))
  return {
    tooltip: { trigger: 'item', formatter: (p) => {
      const d = p.data || {}
      return `<b>${p.name}</b><br/>设备数: <b>${p.value}</b> 个 (${Number(d.ratio||0).toFixed(1)}%)<br/>金额合计: ¥${formatMoney(d.amount)}`
    } },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['42%', '68%'], center: ['50%', '46%'], avoidLabelOverlap: true,
      label: { show: true, formatter: (p) => `${p.name}\n${p.value}个`, fontSize: 11 },
      data,
    }],
  }
})

async function loadAll() {
  loading.value = true
  try {
    const params = { start: dayjs().format('YYYY-MM-DD'), end: dayjs().format('YYYY-MM-DD') }
    const res = await today.refundRecovery(params)
    recoveryData.value = Object.assign(
      { kpi: emptyKpi(), reason_dist: [], staff_detail: [], daily_trend: [], hourly_today: [], top_risk_devices: [], amount_buckets: [] },
      res.data || {}
    )
  } catch (e) {
    // 静默,页面保留空态
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.filter-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.empty-hint { background: #fff; border-radius: 8px; padding: 48px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.empty-tip { text-align: center; }
.empty-main { font-size: 15px; color: #374151; margin-bottom: 8px; display: flex; align-items: center; justify-content: center; gap: 8px; }
.empty-sub { font-size: 12px; color: #9ca3af; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.block-title { font-size: 17px; font-weight: 700; color: #111827; margin-bottom: 4px; }
.block-sub { font-size: 12px; color: #6b7280; margin-bottom: 16px; }
.section-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.kpi-mini-card {
  border-radius: 10px; padding: 12px 14px 10px; border: 1px solid rgba(148,163,184,0.15);
  display: flex; flex-direction: column; gap: 3px; min-height: 88px;
  transition: transform .15s ease, box-shadow .15s ease;
}
.kpi-mini-card:hover { transform: translateY(-1px); box-shadow: 0 6px 16px -8px rgba(15,23,42,0.18); }
.kpi-mini-label { font-size: 12px; font-weight: 600; line-height: 1.3; }
.kpi-mini-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; opacity: 0.9; }
.kpi-mini-value { margin-top: 2px; font-size: 22px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-mini-unit { font-size: 13px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
.kpi-mini-money { margin-top: 1px; font-size: 11.5px; font-weight: 500; color: #374151; opacity: 0.92; }

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .filter-bar { gap: 8px; }
  .filter-bar.is-mobile .el-radio-group { margin-bottom: 2px; }
  .page-title { font-size: 18px; }
  .block-title { font-size: 15px; }
  .block-sub { font-size: 11px; }
  .section-card { padding: 12px; }
  .kpi-mini-card { padding: 8px 10px 8px; min-height: 72px; }
  .kpi-mini-label { font-size: 11px; }
  .kpi-mini-value { font-size: 19px; }
  .kpi-mini-sub { font-size: 9.5px; }
  .kpi-mini-money { font-size: 10.5px; }
  /* 表格在手机上保持可横向滚动 */
  :deep(.el-card) { overflow: hidden; }
  :deep(.el-table) { min-width: 560px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
}
</style>
