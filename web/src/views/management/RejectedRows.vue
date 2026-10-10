<template>
  <div class="rejected-rows">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin:0">拒收数据(未采纳行)</div>
        <div class="page-sub">A-20260908-003 外部数据统一质量治理 · 未入库行按行查看 · open/discard 默认不纳入统计</div>
      </div>
      <div class="ops">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 状态统计 -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="12" :sm="6">
        <div class="stat-card" :class="{ 'stat-active': status === 'open' }" @click="switchStatus('open')">
          <div class="stat-label">待判定(open)</div>
          <div class="stat-value" style="color:#e6a23c">{{ stats.open ?? 0 }}</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card" :class="{ 'stat-active': status === 'keep' }" @click="switchStatus('keep')">
          <div class="stat-label">已采纳(keep)</div>
          <div class="stat-value" style="color:#67c23a">{{ stats.by_status?.keep ?? 0 }}</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card" :class="{ 'stat-active': status === 'discard' }" @click="switchStatus('discard')">
          <div class="stat-label">已剔除(discard)</div>
          <div class="stat-value" style="color:#f56c6c">{{ stats.by_status?.discard ?? 0 }}</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="stat-card">
          <div class="stat-label">总数</div>
          <div class="stat-value">{{ stats.total ?? 0 }}</div>
        </div>
      </el-col>
    </el-row>

    <!-- 筛选 + 批量操作 -->
    <div class="filter-bar mb-16">
      <el-select v-model="filters.data_type" placeholder="数据类型" clearable style="width:160px" @change="load">
        <el-option v-for="dt in dataTypeOptions" :key="dt" :label="dt" :value="dt" />
      </el-select>
      <el-select v-model="filters.source" placeholder="来源" clearable style="width:160px" @change="load">
        <el-option v-for="s in sourceOptions" :key="s" :label="s" :value="s" />
      </el-select>
      <el-button type="primary" plain size="small" :disabled="!selection.length" @click="batchAction('keep')">
        采纳所选({{ selection.length }})
      </el-button>
      <el-button type="danger" plain size="small" :disabled="!selection.length" @click="batchAction('discard')">
        剔除所选({{ selection.length }})
      </el-button>
    </div>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe size="small" max-height="520"
                @selection-change="sel => (selection = sel)">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="data_type" label="数据类型" width="150" show-overflow-tooltip />
        <el-table-column prop="source" label="来源" width="130" />
        <el-table-column prop="reject_category" label="拒因" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="categoryType(row.reject_category)">{{ row.reject_category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="business_key" label="业务键" width="150" show-overflow-tooltip />
        <el-table-column prop="reject_reason" label="原因" min-width="180" show-overflow-tooltip />
        <el-table-column label="原始行" min-width="220">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="showRaw(row)">查看原始行</el-button>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" fixed="right">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'open'">
              <el-button size="small" type="success" link @click="review(row, 'keep')">采纳</el-button>
              <el-button size="small" type="danger" link @click="review(row, 'discard')">剔除</el-button>
            </template>
            <span v-else class="text-muted">{{ row.reviewed_by || '—' }} {{ (row.reviewed_at || '').slice(0, 10) }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pag-wrap">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="() => { page = 1; load() }"
        />
      </div>
    </el-card>

    <!-- 原始行详情 -->
    <el-dialog v-model="rawVisible" title="原始行内容(未采纳数据)" width="640px">
      <pre class="raw-json">{{ rawRowText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { listRejectedRows, rejectedStats, reviewRejectedRow, batchReviewRejected } from '@/api/dataQuality'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const status = ref('open')
const selection = ref([])
const stats = ref({})
const filters = ref({ data_type: '', source: '' })
const rawVisible = ref(false)
const rawRowText = ref('')

const dataTypeOptions = ['refund', 'external_complaint', 'card_complaint', 'manual_refund', 'wallet', 'ticket']
const sourceOptions = ['workbuddy_pull', 'upload', 'shared_doc', 'bms', 'scrm']

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, status: status.value }
    if (filters.value.data_type) params.data_type = filters.value.data_type
    if (filters.value.source) params.source = filters.value.source
    const res = await listRejectedRows(params)
    list.value = res.data?.items || []
    total.value = res.data?.total || 0
    const s = await rejectedStats()
    stats.value = s.data || {}
  } finally {
    loading.value = false
  }
}

function switchStatus(s) {
  status.value = s
  page.value = 1
  load()
}

function categoryType(c) {
  return { parse_fail: 'danger', validate_fail: 'warning', polluted: 'danger', dup_skip: 'info', empty_template: 'info' }[c] || 'info'
}
function statusType(s) {
  return { open: 'warning', keep: 'success', discard: 'danger' }[s] || 'info'
}
function statusLabel(s) {
  return { open: '待判定', keep: '已采纳', discard: '已剔除' }[s] || s
}
function showRaw(row) {
  rawRowText.value = JSON.stringify(row.raw_row || {}, null, 2)
  rawVisible.value = true
}

async function review(row, action) {
  try {
    await ElMessageBox.confirm(`确认将该行标记为「${statusLabel(action)}」?`, '人工判定', { type: 'warning' })
  } catch {
    return
  }
  await reviewRejectedRow(row.id, action)
  ElMessage.success(`已标记 ${statusLabel(action)}`)
  load()
}

async function batchAction(action) {
  const ids = selection.value.map(r => r.id)
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(`确认将所选 ${ids.length} 行标记为「${statusLabel(action)}」?`, '批量判定', { type: 'warning' })
  } catch {
    return
  }
  await batchReviewRejected(ids, action)
  ElMessage.success(`已批量处理 ${ids.length} 行`)
  selection.value = []
  load()
}

onMounted(load)
</script>

<style scoped>
.stat-card { border: 1px solid #ebeef5; border-radius: 8px; padding: 12px 16px; cursor: pointer; transition: all .2s; }
.stat-card:hover { border-color: #409eff; }
.stat-active { border-color: #409eff; background: #ecf5ff; }
.stat-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
.stat-value { font-size: 22px; font-weight: 600; }
.filter-bar { display: flex; gap: 8px; align-items: center; }
.raw-json { background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px; max-height: 480px; overflow: auto; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.pag-wrap { margin-top: 12px; display: flex; justify-content: flex-end; }
</style>
