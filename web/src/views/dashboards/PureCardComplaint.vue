<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">纯工信部投诉</div>
      <div class="filter-bar">
        <el-date-picker
          v-model="range"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始（留空=全量）"
          end-placeholder="结束"
          clearable
          style="width: 260px"
        />
        <el-button type="primary" size="small" :icon="Search" :loading="loading" @click="load">查询</el-button>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="reset">重置</el-button>
      </div>
    </div>

    <!-- ── 口径说明 ── -->
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        <span style="font-weight: 600">判据：工信部工单 ∧ SN 与电话在「卡商表 / 315 表」都无命中</span>
      </template>
      <div class="rule-body">
        <div>
          工单侧为 <b>经营登记</b>（BMS），文档侧为 <b>接待过程</b>。两边按
          <el-tag size="small" type="danger" effect="plain">SN 或 电话</el-tag>
          <b>合并去重</b>后，仍无对应记录的才计入本页。
        </div>
        <div class="dim">
          ★ 为什么必须「SN <b>或</b> 电话」：同一客户可能有多台设备（工单管 A 机、卡商单登记 B 机），
          或同客户换机后 SN 变更 —— 此时 <b>电话才是可靠的关联键</b>。
          只用 SN 精确匹配会把这类误判为纯工信部。
        </div>
      </div>
    </el-alert>

    <!-- ── 汇总卡片 ── -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="12" :md="6">
        <div class="kpi-card" style="background:#f8fafc">
          <div class="kpi-label">工信部工单总数</div>
          <div class="kpi-value" style="color:#475569">{{ formatNum(data.total_tickets) }}</div>
          <div class="kpi-sub">经营登记口径</div>
        </div>
      </el-col>
      <el-col :xs="12" :md="6">
        <div class="kpi-card" style="background:#eff6ff">
          <div class="kpi-label">已对账</div>
          <div class="kpi-value" style="color:#2563eb">{{ formatNum(data.matched) }}</div>
          <div class="kpi-sub">SN 或电话命中</div>
        </div>
      </el-col>
      <el-col :xs="12" :md="6">
        <div class="kpi-card" style="background:#fef2f2;border-left:3px solid #dc2626">
          <div class="kpi-label" style="color:#dc2626">★ 纯工信部</div>
          <div class="kpi-value" style="color:#dc2626">{{ formatNum(data.pure) }}</div>
          <div class="kpi-sub">两边都无记录 · 需人工核查</div>
        </div>
      </el-col>
      <el-col :xs="12" :md="6">
        <div class="kpi-card" style="background:#fafaf9">
          <div class="kpi-label">仅按 SN 精确匹配</div>
          <div class="kpi-value" style="color:#78716c">{{ formatNum(data.sn_exact_only) }}</div>
          <div class="kpi-sub">
            电话兜底多抓
            <span style="color:#16a34a;font-weight:600">
              {{ formatNum(Math.max((data.matched || 0) - (data.sn_exact_only || 0), 0)) }}
            </span>
            条
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- ── 命中路径 + 月度趋势 ── -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="24" :md="10">
        <ChartCard title="命中路径分布">
          <BaseChart v-if="hitPathOption" :option="hitPathOption" height="220px" />
          <el-empty v-else description="暂无数据" :image-size="40" />
        </ChartCard>
      </el-col>
      <el-col :xs="24" :md="14">
        <ChartCard title="月度分布（总数 vs 纯工信部）">
          <BaseChart v-if="monthlyOption" :option="monthlyOption" height="220px" />
          <el-empty v-else description="暂无数据" :image-size="40" />
        </ChartCard>
      </el-col>
    </el-row>

    <!-- ── 清单 ── -->
    <el-card shadow="never">
      <template #header>
        <div class="flex-between">
          <span style="font-weight:600">
            纯工信部清单（{{ filteredItems.length }} 条<span
              v-if="data.items?.length !== filteredItems.length"
            > / 共 {{ data.items?.length || 0 }} 条</span>）
            <el-tag v-if="data.truncated" size="small" type="warning" effect="plain" style="margin-left:6px">
              已达返回上限，请缩小时间范围
            </el-tag>
          </span>
          <div class="flex gap-8" style="flex-wrap:wrap">
            <el-tag v-if="statusFilter" closable size="small" effect="plain" @close="statusFilter = ''">
              状态: {{ statusFilter }}
            </el-tag>
            <el-button v-if="statusFilter" text size="small" @click="statusFilter = ''">清除筛选</el-button>
            <el-button size="small" :icon="Download" @click="exportCsv">导出</el-button>
          </div>
        </div>
      </template>

      <el-table
        v-loading="loading"
        :data="filteredItems"
        size="small"
        border
        stripe
        max-height="520"
      >
        <el-table-column label="工单号" width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.ticket_no }}</template>
        </el-table-column>
        <el-table-column label="登记时间" width="140" sortable :sort-method="(a, b) => sortTime(a.register_time, b.register_time)">
          <template #default="{ row }">{{ row.register_time || '—' }}</template>
        </el-table-column>
        <el-table-column label="设备SN" width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.device_sn || '—' }}</template>
        </el-table-column>
        <el-table-column label="电话" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.phone || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" :filters="statusFilters" :filter-method="(v, row) => row.status === v" filter-placement="bottom-end">
          <template #default="{ row }">
            <!-- 未完成 = 业务上值得关注（本页多数是这类） -->
            <el-tag v-if="!isDone(row.status)" size="small" type="danger" effect="plain">
              {{ row.status || '未标注' }}
            </el-tag>
            <el-tag v-else size="small" type="success" effect="plain">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="处理意见" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.handle_opinion">{{ row.handle_opinion }}</span>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <!-- 已对账的（include_checked 时才有）显示命中原因 -->
        <el-table-column v-if="hasChecked" label="命中原因" width="160">
          <template #default="{ row }">
            <template v-if="row.hit_paths?.length">
              <el-tag v-for="p in row.hit_paths" :key="p" size="small" type="info" effect="plain" style="margin-right:4px">
                {{ p }}
              </el-tag>
            </template>
            <el-tag v-else size="small" type="danger" effect="plain">纯工信部</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
/**
 * 纯工信部投诉单列页 (#63)
 *
 * 用户需求：
 *   「纯粹工信部的优先级不高，有统计记录就好了。回头单列一页展示即可。
 *     工单+文档 合并去重 统计展示。」
 *
 * 判据：
 *   纯工信部 = 工信部工单 ∧ NOT(卡商表 SN或电话命中) ∧ NOT(315表 SN或电话命中)
 *
 * ★ 为什么必须「SN 或 电话」：
 *   同一客户多台设备 / 换机后 SN 变更 → 只用 SN 精确匹配会误判。
 *   实测：SN 精确 314 条命中，加电话兜底 → 318 条（多抓 4 条）。
 *
 * ⚠️ 只读视图：不改任何数据（原 service_item 保持原样）。
 */
import { ref, computed, onMounted } from 'vue'
import { Search, Refresh, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import ChartCard from '@/components/dashboard/ChartCard.vue'
import BaseChart from '@/components/BaseChart.vue'
import { complaintPureCard } from '@/api/complaint'

const loading = ref(false)
const range = ref([])
const data = ref({
  total_tickets: 0, matched: 0, pure: 0, sn_exact_only: 0,
  by_hit_path: {}, monthly: [], items: [], truncated: false,
})
const statusFilter = ref('')

// 是否展示「命中原因」列 —— 只有请求了 include_checked 才有意义
const hasChecked = computed(() => (data.value.items || []).some(it => it.is_pure === false))

const filteredItems = computed(() => {
  const list = data.value.items || []
  if (!statusFilter.value) return list
  return list.filter(r => (r.status || '未标注') === statusFilter.value)
})

const statusFilters = computed(() => {
  const set = new Set((data.value.items || []).map(r => r.status || '未标注'))
  return [...set].map(v => ({ text: v, value: v }))
})

// 命中路径分布（横向柱图）
const hitPathOption = computed(() => {
  const rows = Object.entries(data.value.by_hit_path || {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => a.value - b.value)
  if (!rows.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 30, bottom: 20, top: 10 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: rows.map(d => d.name), axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar',
      data: rows.map(d => d.value),
      itemStyle: { color: '#2563eb' },
      barMaxWidth: 18,
      label: { show: true, position: 'right', fontSize: 11 },
    }],
  }
})

// 月度：总数(柱) + 纯工信部(柱, 叠加)
const monthlyOption = computed(() => {
  const rows = data.value.monthly || []
  if (!rows.length) return null
  const labels = rows.map(r => r.month)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
    grid: { left: 40, right: 20, bottom: 24, top: 30, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: 10, rotate: labels.length > 8 ? 40 : 0 },
    },
    yAxis: { type: 'value' },
    series: [
      {
        name: '工信部工单',
        type: 'bar',
        data: rows.map(r => r.total),
        itemStyle: { color: '#cbd5e1' },
        barMaxWidth: 22,
      },
      {
        name: '★ 纯工信部',
        type: 'bar',
        data: rows.map(r => r.pure),
        itemStyle: { color: '#dc2626' },
        barMaxWidth: 22,
      },
    ],
  }
})

function isDone(status) {
  const s = String(status || '')
  return ['已完成', '已结案', '已解决', '已关闭', '完成', '终止', '已终止'].some(k => s.includes(k))
}

function sortTime(a, b) {
  const va = a || ''
  const vb = b || ''
  if (va === vb) return 0
  if (!va) return 1
  if (!vb) return -1
  return va < vb ? -1 : 1
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  return Number.isNaN(n) ? '0' : n.toLocaleString('zh-CN')
}

async function load() {
  loading.value = true
  try {
    const params = {}
    if (range.value?.length === 2) {
      params.start = range.value[0]
      params.end = range.value[1]
    }
    const res = await complaintPureCard(params)
    const d = res.data || {}
    data.value = {
      total_tickets: d.total_tickets || 0,
      matched: d.matched || 0,
      pure: d.pure || 0,
      sn_exact_only: d.sn_exact_only || 0,
      by_hit_path: d.by_hit_path || {},
      monthly: d.monthly || [],
      items: d.items || [],
      truncated: !!d.truncated,
    }
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

function reset() {
  range.value = []
  statusFilter.value = ''
  load()
}

/** 导出当前筛选结果为 CSV（含 BOM, Excel 直接打开不乱码） */
function exportCsv() {
  const rows = filteredItems.value
  if (!rows.length) {
    ElMessage.warning('无可导出的数据')
    return
  }
  const header = ['工单号', '登记时间', '设备SN', '电话', '状态', '处理意见']
  const escape = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = [
    header.join(','),
    ...rows.map(r => [
      escape(r.ticket_no), escape(r.register_time), escape(r.device_sn),
      escape(r.phone), escape(r.status), escape(r.handle_opinion),
    ].join(',')),
  ]
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `纯工信部投诉_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(load)
</script>

<style scoped>
.page-container {
  padding: 0 4px 24px;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.flex { display: flex; }
.gap-8 { gap: 8px; }
.mb-16 { margin-bottom: 16px; }
.dim { color: #9ca3af; }

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 口径说明 */
.rule-body {
  font-size: 12px;
  line-height: 1.8;
  color: #334155;
}

/* 汇总卡片 */
.kpi-card {
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  height: 100%;
}
.kpi-label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}
.kpi-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}
.kpi-sub {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 4px;
}
</style>
