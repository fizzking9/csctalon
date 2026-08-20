<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">投诉分析</div>
        <div class="page-sub">三源统一统计 · 内部投诉 / 商户号投诉 / 外部投诉 · 315/卡商补充明细(workbuddy推送)</div>
      </div>
      <div class="flex gap-8">
        <el-date-picker
          v-model="range"
          type="daterange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          value-format="YYYY-MM-DD"
          style="width: 260px"
          @change="loadData"
        />
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </div>

    <el-row :gutter="16" class="mb-16">
      <el-col v-for="c in kpiCards" :key="c.key" :xs="12" :sm="8" :md="6">
        <div class="kpi-card" :style="{ background: c.bg }">
          <div class="kpi-label" :style="{ color: c.color }">{{ c.label }}</div>
          <div class="kpi-value" :style="{ color: c.color }">{{ c.value }}</div>
          <div v-if="c.sub" class="kpi-sub">{{ c.sub }}</div>
        </div>
      </el-col>
    </el-row>

    <el-tabs v-model="tab">
      <el-tab-pane v-for="src in sources" :key="src.key" :label="src.label" :name="src.key">
        <el-row :gutter="16">
          <el-col :xs="24" :lg="12">
            <el-card class="mb-16">
              <template #header><span style="font-weight: 600">归因/分类分布</span></template>
              <el-table :data="data[src.key].by_category" border stripe size="small" max-height="360">
                <el-table-column prop="name" label="类型" />
                <el-table-column prop="count" label="数量" width="100" align="right" />
              </el-table>
              <el-empty v-if="!data[src.key].by_category.length" description="暂无数据" :image-size="50" />
            </el-card>
          </el-col>
          <el-col :xs="24" :lg="12">
            <el-card class="mb-16">
              <template #header><span style="font-weight: 600">涉及接待/处理人员</span></template>
              <el-table :data="data[src.key].by_handler" border stripe size="small" max-height="360">
                <el-table-column prop="name" label="人员" />
                <el-table-column prop="count" label="数量" width="100" align="right" />
              </el-table>
              <el-empty v-if="!data[src.key].by_handler.length" description="暂无数据" :image-size="50" />
            </el-card>
          </el-col>
          <el-col :xs="24">
            <el-card>
              <template #header><span style="font-weight: 600">趋势(按日)</span></template>
              <BaseChart v-if="trendOption(src.key)" :option="trendOption(src.key)" height="320px" />
              <el-empty v-else description="暂无趋势数据" />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- 补充明细: 315外部投诉 / 卡商群投诉(workbuddy推送, 按设备SN关联服务工单) -->
      <el-tab-pane label="补充明细(315/卡商)" name="supplementary">
        <div class="flex gap-8 mb-16">
          <el-select v-model="supSource" style="width: 160px" @change="loadSupplementary">
            <el-option label="全部来源" value="" />
            <el-option label="315外部投诉" value="external" />
            <el-option label="卡商群投诉" value="card" />
          </el-select>
          <el-select v-model="supMatched" style="width: 160px" @change="loadSupplementary">
            <el-option label="全部" value="" />
            <el-option label="仅已关联工单" value="matched" />
            <el-option label="仅未关联" value="unmatched" />
          </el-select>
          <div class="sup-hint">由 workbuddy 定期推送 · 核心匹配键:设备SN · 关联工单号可点击查看</div>
        </div>
        <el-table :data="supplItems" border stripe size="small" max-height="520">
          <el-table-column prop="source" label="来源" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.source === '315外部投诉' ? 'danger' : 'warning'" effect="plain">{{ row.source }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="complaint_date" label="投诉日期" width="120" />
          <el-table-column prop="complaint_no" label="投诉编号" width="150" show-overflow-tooltip />
          <el-table-column prop="complainant" label="投诉人" width="100" />
          <el-table-column prop="complainant_phone" label="投诉手机号" width="130" />
          <el-table-column prop="device_sn" label="设备SN" width="140">
            <template #default="{ row }">
              <el-link v-if="row.device_sn" type="primary" :underline="false" @click.stop="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="problem" label="投诉问题" min-width="220" show-overflow-tooltip />
          <el-table-column prop="problem_category" label="问题分类" width="110" show-overflow-tooltip />
          <el-table-column prop="result" label="结果" width="100" />
          <el-table-column prop="handler" label="跟进/处理人" width="100" />
          <el-table-column prop="handle_detail" label="处理明细" min-width="160" show-overflow-tooltip />
          <el-table-column label="关联工单" width="130">
            <template #default="{ row }">
              <el-link v-if="row.matched_ticket_no" type="primary" :underline="false" @click.stop="openDeviceAudit(row.device_sn)">{{ row.matched_ticket_no }}</el-link>
              <span v-else class="sup-none">未关联</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="flex-between mt-8">
          <div class="sup-total">共 {{ supplTotal }} 条</div>
          <el-button size="small" @click="loadSupplementary">刷新</el-button>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { complaintSummary, complaintSupplementary } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'

const { openDeviceAudit } = useAudit()

const tab = ref('internal')
const range = ref(null)
const loading = ref(false)
const sources = [
  { key: 'internal', label: '内部投诉' },
  { key: 'merchant', label: '商户号投诉' },
  { key: 'external', label: '外部投诉' },
]
const data = reactive({
  internal: { total: 0, by_category: [], by_handler: [], trend: [] },
  merchant: { total: 0, by_category: [], by_handler: [], trend: [] },
  external: { total: 0, by_category: [], by_handler: [], trend: [] },
})
const supplementary = reactive({ external: { total: 0, matched: 0 }, card: { total: 0, matched: 0 } })

// ---- 补充明细(315/卡商) ----
const supSource = ref('')
const supMatched = ref('')
const supplItems = ref([])
const supplTotal = ref(0)

const kpiCards = computed(() => [
  { key: 'internal', label: '内部投诉', value: data.internal.total, color: '#2563eb', bg: '#eff6ff' },
  { key: 'merchant', label: '商户号投诉', value: data.merchant.total, color: '#7c3aed', bg: '#f5f3ff' },
  { key: 'external', label: '外部投诉', value: data.external.total, color: '#dc2626', bg: '#fef2f2' },
  { key: 'total', label: '投诉合计', value: data.internal.total + data.merchant.total + data.external.total, color: '#0f766e', bg: '#f0fdfa' },
  { key: 'ext_suppl', label: '315补充明细', value: supplementary.external.total, color: '#b91c1c', bg: '#fef2f2', sub: `已关联${supplementary.external.matched} · 未关联${Math.max(supplementary.external.total - supplementary.external.matched, 0)}` },
  { key: 'card_suppl', label: '卡商补充明细', value: supplementary.card.total, color: '#b45309', bg: '#fffbeb', sub: `已关联${supplementary.card.matched} · 未关联${Math.max(supplementary.card.total - supplementary.card.matched, 0)}` },
])

function trendOption(key) {
  const rows = data[key].trend || []
  if (!rows.length) return null
  const x = rows.map((r) => String(r.day).slice(0, 10))
  const y = rows.map((r) => Number(r.count) || 0)
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 24, bottom: 40, top: 24 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '投诉量' },
    series: [{ type: 'bar', data: y, barMaxWidth: 18, itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] } }],
  }
}

function params() {
  const p = {}
  if (range.value && range.value.length === 2) {
    p.start = range.value[0]
    p.end = range.value[1]
  }
  return p
}

async function loadData() {
  loading.value = true
  try {
    const res = await complaintSummary(params())
    const d = res.data || {}
    data.internal = d.internal || { total: 0, by_category: [], by_handler: [], trend: [] }
    data.merchant = d.merchant || { total: 0, by_category: [], by_handler: [], trend: [] }
    data.external = d.external || { total: 0, by_category: [], by_handler: [], trend: [] }
    const suppl = d.supplementary || {}
    supplementary.external = suppl.external || { total: 0, matched: 0 }
    supplementary.card = suppl.card || { total: 0, matched: 0 }
  } finally {
    loading.value = false
  }
}

async function loadSupplementary() {
  const p = params()
  if (supSource.value) p.source_type = supSource.value
  if (supMatched.value === 'matched') p.matched = true
  if (supMatched.value === 'unmatched') p.matched = false
  p.limit = 2000
  const res = await complaintSupplementary(p)
  supplItems.value = res.data?.items || []
  supplTotal.value = res.data?.total || 0
}

onMounted(() => {
  loadData()
  loadSupplementary()
})
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.mt-8 { margin-top: 8px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.kpi-card { border-radius: 10px; padding: 14px 16px; min-height: 76px; display: flex; flex-direction: column; gap: 6px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 13px; font-weight: 600; }
.kpi-value { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 12px; color: #6b7280; }
.sup-hint { font-size: 12px; color: #909399; }
.sup-total { font-size: 12px; color: #6b7280; }
.sup-none { color: #c0c4cc; font-size: 12px; }
</style>
