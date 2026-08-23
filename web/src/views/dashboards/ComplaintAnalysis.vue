<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">投诉分析</div>
        <div class="page-sub">三源统一统计 · 管理摘要 · 外部投诉重点 · 商户号投诉 · 受理分析</div>
      </div>
      <div class="filter-bar">
        <el-radio-group v-model="rangeMode" size="small">
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
          <el-radio-button label="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="rangeMode === 'custom'"
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width: 240px"
        />
        <el-button type="primary" :icon="Search" :loading="loading" @click="loadData">分析</el-button>
      </div>
    </div>

    <!-- 未加载时的提示 -->
    <section v-if="!loaded" class="empty-hint">
      <el-empty :image-size="80" description="">
        <template #description>
          <div class="empty-tip">
            <div class="empty-main">选择时间范围后点击 <el-button size="small" type="primary" :icon="Search">分析</el-button> 加载数据</div>
            <div class="empty-sub">按需加载 · 避免进入页面即产生大量请求</div>
          </div>
        </template>
      </el-empty>
    </section>

    <template v-if="loaded && data">
      <!-- ============ TAB: 决策概览 ============ -->
      <section v-if="activeTab === 'overview'" class="block-card">
        <div class="block-head">
          <span class="block-title">管理摘要</span>
          <span class="block-hint">自动结论 · 随筛选区间更新</span>
        </div>
        <el-row :gutter="12" class="mb-12">
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#eff6ff">
              <div class="kpi-label" style="color:#2563eb">投诉总量</div>
              <div class="kpi-value">{{ formatNum(s.summary.total) }}</div>
              <div class="kpi-sub">内{{ s.summary.internal_total }} · 商{{ s.summary.merchant_total }} · 外{{ s.summary.external_total }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fef2f2">
              <div class="kpi-label" style="color:#dc2626">积压未处理</div>
              <div class="kpi-value">{{ formatNum(s.summary.backlog_total) }}</div>
              <div class="kpi-sub">截止当前全部未完成</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f0fdf4">
              <div class="kpi-label" style="color:#16a34a">完成率</div>
              <div class="kpi-value">{{ (s.summary.completion_rate * 100).toFixed(1) }}<span class="unit">%</span></div>
              <div class="kpi-sub">已完成 {{ formatNum(s.summary.done_total) }} 条</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fff7ed">
              <div class="kpi-label" style="color:#ea580c">平均处理时长</div>
              <div class="kpi-value">{{ s.summary.avg_handle_hours > 24 ? (s.summary.avg_handle_hours / 24).toFixed(1) + '天' : s.summary.avg_handle_hours + 'h' }}</div>
              <div class="kpi-sub">有真实完成时间的工单</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f5f3ff">
              <div class="kpi-label" style="color:#7c3aed">外部投诉</div>
              <div class="kpi-value">{{ formatNum(s.external_total) }}</div>
              <div class="kpi-sub">315平台 · 关联{{ formatNum(s.external_matched) }} · 未关联{{ formatNum(s.external_unmatched) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fefce8">
              <div class="kpi-label" style="color:#ca8a04">商户号投诉</div>
              <div class="kpi-value">{{ formatNum(s.summary.merchant_total) }}</div>
              <div class="kpi-sub">{{ merchantEntitySummary }}</div>
            </div>
          </el-col>
        </el-row>

        <!-- 来源分布饼图 + 趋势堆叠图 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">投诉来源分布</div>
              <BaseChart v-if="sourcePieOption" :option="sourcePieOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="16">
            <div class="chart-card">
              <div class="chart-title">三源趋势(按日)</div>
              <BaseChart v-if="trendStackOption" :option="trendStackOption" height="260px" />
              <el-empty v-else description="暂无趋势数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 处理人 Top10 + 时效分布 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">处理人 Top10</div>
              <BaseChart v-if="handlerBarOption" :option="handlerBarOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">处理时效分布</div>
              <BaseChart v-if="timelinessOption" :option="timelinessOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>
      </section>

      <!-- ============ TAB: 外部投诉重点 ============ -->
      <section v-if="activeTab === 'external'" class="block-card">
        <div class="block-head">
          <span class="block-title">外部投诉专项分析</span>
          <span class="block-hint">按投诉日期统计 · 今日登记见卡片 · 明细表可看登记时间</span>
        </div>

        <!-- 外部投诉 KPI -->
        <el-row :gutter="12" class="mb-16">
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#fef2f2">
              <div class="kpi-label" style="color:#dc2626">外部投诉总量</div>
              <div class="kpi-value">{{ formatNum(s.external_total) }}</div>
              <div class="kpi-sub">按投诉日期 · 覆盖全部平台</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#fefce8">
              <div class="kpi-label" style="color:#ca8a04">今日登记</div>
              <div class="kpi-value">{{ formatNum(s.external_today_registered) }}</div>
              <div class="kpi-sub">今天同步/登记进来的</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#f0fdf4">
              <div class="kpi-label" style="color:#16a34a">已关联工单</div>
              <div class="kpi-value">{{ formatNum(s.external_matched) }}</div>
              <div class="kpi-sub">已落工单跟踪</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#fffbeb">
              <div class="kpi-label" style="color:#b45309">未关联工单</div>
              <div class="kpi-value">{{ formatNum(s.external_unmatched) }}</div>
              <div class="kpi-sub">尚未落工单 · 需跟进 · {{ platformSummary }}</div>
            </div>
          </el-col>
        </el-row>

        <!-- 平台分布 + 问题分类 + 平台趋势 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">外部投诉平台分布</div>
              <BaseChart v-if="platformPieOption" :option="platformPieOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">投诉问题分类</div>
              <BaseChart v-if="externalCategoryOption" :option="externalCategoryOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">外部投诉趋势(按日)</div>
              <BaseChart v-if="externalTrendOption" :option="externalTrendOption" height="260px" />
              <el-empty v-else description="暂无趋势数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 外部投诉明细表(315表, 完整内容) -->
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span style="font-weight:600">外部投诉明细（{{ externalDetail.length }} 条）</span>
              <el-button size="small" @click="loadExternalDetail">刷新</el-button>
            </div>
          </template>
          <el-table :data="externalDetail" v-loading="extDetailLoading" border stripe size="small" max-height="400">
            <el-table-column label="投诉日期" width="100" sortable>
              <template #default="{ row }">{{ formatTime(row.complaint_date) }}</template>
            </el-table-column>
            <el-table-column label="登记时间" width="100">
              <template #default="{ row }">
                <span v-if="row.created_at" class="dim-text">{{ formatTime(row.created_at) }}</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="platform" label="平台" width="120" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag size="small" effect="plain" type="danger">{{ row.platform || '未标注' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="complaint_no" label="投诉编号" width="150" show-overflow-tooltip />
            <el-table-column label="投诉内容" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="problem-text">{{ row.problem || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="complaint_category" label="问题分类" width="110" show-overflow-tooltip />
            <el-table-column prop="entity" label="被投诉主体" width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.entity || row.company_entity || '—' }}</template>
            </el-table-column>
            <el-table-column prop="device_sn" label="设备SN" width="130">
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="处理/跟进" width="100" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link v-if="row.handler" type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="结果" width="100" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag v-if="row.result" size="small" :type="row.result.includes('结案') ? 'success' : 'info'" effect="plain">{{ row.result }}</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="关联工单" width="130">
              <template #default="{ row }">
                <el-link v-if="row.matched_ticket_no" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.matched_ticket_no }}</el-link>
                <el-tag v-else size="small" type="warning" effect="plain">未关联</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </section>

      <!-- ============ TAB: 商户号投诉 ============ -->
      <section v-if="activeTab === 'merchant'" class="block-card">
        <div class="block-head">
          <span class="block-title">商户号投诉分析</span>
          <span class="block-hint">微信投诉 · 被投诉主体(商户号)与处理人(客服)分开统计</span>
        </div>

        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">被投诉主体分布（商户号）</div>
              <BaseChart v-if="merchantEntityOption" :option="merchantEntityOption" height="300px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">处理人 Top（客服）</div>
              <BaseChart v-if="merchantHandlerOption" :option="merchantHandlerOption" height="300px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 商户号投诉明细表 -->
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span style="font-weight:600">商户号投诉明细（{{ merchantDetail.length }} 条）</span>
              <el-button size="small" @click="loadMerchantDetail">刷新</el-button>
            </div>
          </template>
          <el-table :data="merchantDetail" v-loading="merDetailLoading" border stripe size="small" max-height="400">
            <el-table-column label="时间" width="120" sortable>
              <template #default="{ row }">{{ formatTime(row.time) }}</template>
            </el-table-column>
            <el-table-column label="被投诉主体" width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag v-if="row.entity" size="small" effect="plain" type="warning">{{ row.entity }}</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="分类" width="110" show-overflow-tooltip />
            <el-table-column prop="problem" label="投诉内容" min-width="220" show-overflow-tooltip />
            <el-table-column prop="device_sn" label="设备SN" width="130">
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="处理人" width="100">
              <template #default="{ row }">
                <el-link v-if="row.handler && row.handler !== '未分配'" type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.status === '已完成' ? 'success' : row.status === '处理中' ? 'warning' : 'danger'" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </section>

      <!-- ============ TAB: 受理分析 ============ -->
      <section v-if="activeTab === 'handling'" class="block-card">
        <div class="block-head">
          <span class="block-title">受理分析</span>
          <span class="block-hint">内/商/外 三源为不同维度任务 · 处理人效率分组展示</span>
        </div>

        <!-- 三源切换 -->
        <el-radio-group v-model="effSource" size="small" class="mb-12">
          <el-radio-button label="internal">内部投诉</el-radio-button>
          <el-radio-button label="merchant">商户号投诉</el-radio-button>
          <el-radio-button label="external">外部投诉</el-radio-button>
        </el-radio-group>

        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="16">
            <div class="chart-card">
              <div class="chart-title">处理人效率排行（{{ effSourceLabel }}）</div>
              <BaseChart v-if="handlerEffOption" :option="handlerEffOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">处理时效分布</div>
              <BaseChart v-if="timelinessOption" :option="timelinessOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 处理人效率明细表(按三源) -->
        <el-card shadow="never" class="mb-16">
          <template #header><span style="font-weight:600">处理人效率明细（{{ effSourceLabel }}）</span></template>
          <el-table :data="currentEff" border stripe size="small" max-height="300">
            <el-table-column label="排名" width="60" type="index" />
            <el-table-column prop="name" label="处理人" width="120" />
            <el-table-column label="受理总数" width="110" align="right">
              <template #default="{ row }">{{ formatNum(row.total) }}</template>
            </el-table-column>
            <el-table-column label="已完成" width="110" align="right">
              <template #default="{ row }">{{ formatNum(row.done) }}</template>
            </el-table-column>
            <el-table-column label="完成率" width="110" align="right">
              <template #default="{ row }">{{ row.total > 0 ? (row.done / row.total * 100).toFixed(1) + '%' : '—' }}</template>
            </el-table-column>
            <el-table-column label="平均处理时长" width="140" align="right">
              <template #default="{ row }">{{ row.avg_hours > 0 ? (row.avg_hours > 24 ? (row.avg_hours / 24).toFixed(1) + '天' : row.avg_hours.toFixed(1) + 'h') : '—' }}</template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 积压未处理明细 -->
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span style="font-weight:600">积压未处理 ({{ s.summary.backlog_total }} 条)</span>
              <el-button size="small" @click="loadBacklog">刷新</el-button>
            </div>
          </template>
          <el-table :data="backlogList" v-loading="backlogLoading" border stripe size="small" max-height="360">
            <el-table-column label="来源" width="100">
              <template #default="{ row }">
                <el-tag :type="sourceTagType(row.source)" size="small" effect="plain">{{ row.source }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="登记时间" width="120" sortable>
              <template #default="{ row }">{{ formatTime(row.register_time) }}</template>
            </el-table-column>
            <el-table-column label="被投诉主体" width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag v-if="row.entity" size="small" effect="plain" type="warning">{{ row.entity }}</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="分类" width="120" show-overflow-tooltip />
            <el-table-column prop="device_sn" label="设备SN" width="130">
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="problem" label="投诉内容" min-width="180" show-overflow-tooltip />
            <el-table-column label="处理人" width="90">
              <template #default="{ row }">
                <el-link v-if="row.handler && row.handler !== '未分配'" type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </section>
    </template>

    <!-- Tab 导航 -->
    <div v-if="loaded" class="block-tabs">
      <el-radio-group v-model="activeTab" size="small">
        <el-radio-button label="overview">决策概览</el-radio-button>
        <el-radio-button label="external">外部投诉重点</el-radio-button>
        <el-radio-button label="merchant">商户号投诉</el-radio-button>
        <el-radio-button label="handling">受理分析</el-radio-button>
      </el-radio-group>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import BaseChart from '@/components/BaseChart.vue'
import { complaintAnalysis, complaintDetail, complaintSupplementary } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'

const { openDeviceAudit, openPersonAudit } = useAudit()

const loaded = ref(false)
const loading = ref(false)
const extDetailLoading = ref(false)
const merDetailLoading = ref(false)
const backlogLoading = ref(false)
const activeTab = ref('overview')
const rangeMode = ref('30')
const customRange = ref([])
const data = ref(null)
const externalDetail = ref([])
const merchantDetail = ref([])
const backlogList = ref([])
const effSource = ref('internal')

const s = computed(() => data.value || {
  summary: { total: 0, internal_total: 0, merchant_total: 0, external_total: 0, backlog_total: 0, done_total: 0, completion_rate: 0, avg_handle_hours: 0 },
  source_distribution: [],
  trend: [],
  handler_top: [],
  platform_distribution: [],
  external_category_distribution: [],
  external_trend: [],
  external_total: 0,
  external_matched: 0,
  external_unmatched: 0,
  external_today_registered: 0,
  merchant_category: [],
  merchant_entity_distribution: [],
  merchant_handler_distribution: [],
  timeliness: [],
  handler_efficiency: { internal: [], merchant: [], external: [] },
  external_platform_trend: [],
})

const effSourceLabel = computed(() => ({
  internal: '内部投诉',
  merchant: '商户号投诉',
  external: '外部投诉',
}[effSource.value] || '内部投诉'))

const currentEff = computed(() => s.value.handler_efficiency?.[effSource.value] || [])

const platformSummary = computed(() => {
  const p = s.value.platform_distribution || []
  return p.slice(0, 3).map(x => x.name).join(' / ') || '暂无'
})

const merchantEntitySummary = computed(() => {
  const p = s.value.merchant_entity_distribution || []
  return p.slice(0, 2).map(x => x.name).join(' / ') || '暂无'
})

const effectiveStart = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  return dayjs().format('YYYY-MM-DD')
})

const params = computed(() => {
  const p = {}
  if (effectiveStart.value) p.start = effectiveStart.value
  if (effectiveEnd.value) p.end = effectiveEnd.value
  return p
})

// ── 来源分布饼图 ──
const sourcePieColors = ['#2563eb', '#7c3aed', '#dc2626']
const sourcePieOption = computed(() => {
  const d = s.value.source_distribution || []
  if (!d.length) return null
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['40%', '65%'], center: ['50%', '45%'],
      label: { show: false }, labelLine: { show: false },
      data: d.map((x, i) => ({ ...x, itemStyle: { color: sourcePieColors[i] } })),
    }],
  }
})

// ── 三源趋势堆叠图 ──
const trendStackOption = computed(() => {
  const t = s.value.trend || []
  if (!t.length) return null
  const x = t.map(r => r.day)
  return {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 24, bottom: 48, top: 16 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 10, rotate: 45 } },
    yAxis: { type: 'value' },
    series: [
      { name: '内部投诉', type: 'bar', stack: 'total', data: t.map(r => r['内部投诉'] || 0), itemStyle: { color: '#2563eb' } },
      { name: '商户号投诉', type: 'bar', stack: 'total', data: t.map(r => r['商户号投诉'] || 0), itemStyle: { color: '#7c3aed' } },
      { name: '外部投诉', type: 'bar', stack: 'total', data: t.map(r => r['外部投诉'] || 0), itemStyle: { color: '#dc2626' } },
    ],
  }
})

// ── 处理人 Top10 横向柱状图 ──
const handlerBarOption = computed(() => {
  const h = s.value.handler_top || []
  if (!h.length) return null
  const items = [...h].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name) },
    series: [{
      type: 'bar', data: items.map(x => x.count),
      itemStyle: { color: '#7c3aed', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 18,
    }],
  }
})

// ── 时效分布柱状图 ──
const timelinessOption = computed(() => {
  const t = s.value.timeliness || []
  if (!t.length || t.every(x => x.count === 0)) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 48, right: 24, bottom: 32, top: 8 },
    xAxis: { type: 'category', data: t.map(x => x.name), axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '工单数' },
    series: [{
      type: 'bar', data: t.map(x => x.count),
      itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 24,
    }],
  }
})

// ── 外部投诉平台分布饼图 ──
const platformColors = ['#dc2626', '#ea580c', '#ca8a04', '#65a30d', '#0891b2', '#7c3aed', '#db2777', '#4f46e5']
const platformPieOption = computed(() => {
  const p = s.value.platform_distribution || []
  if (!p.length) return null
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['40%', '65%'], center: ['50%', '45%'],
      label: { show: false }, labelLine: { show: false },
      data: p.map((x, i) => ({ ...x, itemStyle: { color: platformColors[i % platformColors.length] } })),
    }],
  }
})

// ── 外部投诉问题分类柱状图 ──
const externalCategoryOption = computed(() => {
  const c = s.value.external_category_distribution || []
  if (!c.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 90, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: c.map(x => x.name).reverse() },
    series: [{
      type: 'bar', data: c.map(x => x.count).reverse(),
      itemStyle: { color: '#dc2626', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 16,
    }],
  }
})

// ── 外部投诉趋势(按日) ──
const externalTrendOption = computed(() => {
  const t = s.value.external_trend || []
  if (!t.length) return null
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 24, bottom: 40, top: 16 },
    xAxis: { type: 'category', data: t.map(r => r.day), axisLabel: { fontSize: 10, rotate: 45 } },
    yAxis: { type: 'value', name: '投诉数' },
    series: [{
      type: 'line', data: t.map(r => r.count), smooth: true,
      areaStyle: { opacity: 0.15 }, itemStyle: { color: '#dc2626' }, lineStyle: { color: '#dc2626' },
    }],
  }
})

// ── 商户号被投诉主体分布 ──
const merchantEntityOption = computed(() => {
  const c = s.value.merchant_entity_distribution || []
  if (!c.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 150, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: c.map(x => x.name).reverse() },
    series: [{
      type: 'bar', data: c.map(x => x.count).reverse(),
      itemStyle: { color: '#ca8a04', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 16,
    }],
  }
})

// ── 商户号处理人 ──
const merchantHandlerOption = computed(() => {
  const h = s.value.merchant_handler_distribution || []
  if (!h.length) return null
  const items = [...h].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name) },
    series: [{
      type: 'bar', data: items.map(x => x.count),
      itemStyle: { color: '#7c3aed', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 16,
    }],
  }
})

// ── 处理人效率排行(当前三源) ──
const handlerEffOption = computed(() => {
  const e = currentEff.value
  if (!e.length) return null
  const items = [...e].sort((a, b) => b.total - a.total).slice(0, 10).reverse()
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const p = ps[0]
        const row = e.find(x => x.name === p?.name)
        if (!row) return ''
        return `${row.name}<br/>受理: ${row.total}<br/>完成: ${row.done}<br/>完成率: ${row.total > 0 ? (row.done / row.total * 100).toFixed(1) : 0}%<br/>平均时长: ${row.avg_hours > 24 ? (row.avg_hours / 24).toFixed(1) + '天' : row.avg_hours.toFixed(1) + 'h'}`
      }
    },
    grid: { left: 80, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name) },
    series: [{
      type: 'bar', data: items.map(x => x.total),
      itemStyle: { color: '#409eff', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 18,
    }],
  }
})

function sourceTagType(source) {
  if (source === '内部投诉') return 'warning'
  if (source === '外部投诉') return 'danger'
  if (source === '商户号投诉') return 'primary'
  return 'info'
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

function formatTime(t) {
  if (!t) return '—'
  const d = dayjs(t)
  return d.isValid() ? d.format('MM-DD HH:mm') : '—'
}

// ── 加载数据 ──
async function loadData() {
  loading.value = true
  try {
    const res = await complaintAnalysis(params.value)
    data.value = res.data || {}
    loaded.value = true
    // 同时加载明细
    loadExternalDetail()
    loadMerchantDetail()
    loadBacklog()
  } finally {
    loading.value = false
  }
}

async function loadExternalDetail() {
  extDetailLoading.value = true
  try {
    const p = { ...params.value, limit: 300, source_type: 'external' }
    const res = await complaintSupplementary(p)
    // 补充 entity 字段 (315表无 entity 字段, 用 company_entity 兼容)
    externalDetail.value = (res.data?.items || []).map(it => ({ ...it, entity: it.entity || it.company_entity }))
  } finally {
    extDetailLoading.value = false
  }
}

async function loadMerchantDetail() {
  merDetailLoading.value = true
  try {
    const p = { ...params.value, limit: 200, include_supplementary: false, source_type: '商户号投诉' }
    const res = await complaintDetail(p)
    merchantDetail.value = res.data?.items || []
  } finally {
    merDetailLoading.value = false
  }
}

async function loadBacklog() {
  backlogLoading.value = true
  try {
    const p = { ...params.value, limit: 200, include_supplementary: false }
    const res = await complaintDetail(p)
    backlogList.value = (res.data?.items || []).filter(r => r.status !== '已完成')
  } finally {
    backlogLoading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.unit { font-size: 14px; font-weight: 400; color: #909399; }
.dim { color: #909399; }
.dim-text { color: #a0a4ab; font-size: 12px; }

.filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.kpi-card { border-radius: 10px; padding: 12px 14px; min-height: 68px; display: flex; flex-direction: column; gap: 4px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 11px; color: #6b7280; }

.block-card { margin-bottom: 16px; }
.block-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.block-title { font-size: 16px; font-weight: 600; color: #1f2937; }
.block-hint { font-size: 12px; color: #9ca3af; }
.block-tabs { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); z-index: 100; background: #fff; padding: 8px 16px; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); }

.chart-card { background: #fff; border-radius: 8px; padding: 12px; border: 1px solid #ebeef5; }
.chart-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 8px; }
.problem-text { font-size: 12px; color: #374151; }

.empty-hint { margin-top: 60px; }
.empty-tip { text-align: center; }
.empty-main { font-size: 15px; color: #6b7280; margin-bottom: 8px; }
.empty-sub { font-size: 13px; color: #9ca3af; }
</style>