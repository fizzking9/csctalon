<template>
  <div v-loading="loading" class="reconcile-lines">
    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <!-- 进度卡 -->
    <el-row v-if="summary" :gutter="12" class="mb-12">
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#eff6ff">
          <div class="kpi-label" style="color:#2563eb">E 笔总数</div>
          <div class="kpi-value">{{ summary.total }}<span class="unit">笔</span></div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#f0fdf4">
          <div class="kpi-label" style="color:#16a34a">已核对</div>
          <div class="kpi-value">{{ summary.confirmed }}<span class="unit">笔</span></div>
          <div class="kpi-sub">{{ summary.total ? Math.round(summary.confirmed / summary.total * 100) : 0 }}%</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#fef2f2">
          <div class="kpi-label" style="color:#dc2626">待核对</div>
          <div class="kpi-value">{{ (summary.by_status?.['待核对'] || 0) }}<span class="unit">笔</span></div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#f5f3ff">
          <div class="kpi-label" style="color:#7c3aed">差异/排除</div>
          <div class="kpi-value">{{ (summary.by_status?.['差异已确认'] || 0) + (summary.by_status?.['已排除重复'] || 0) + (summary.by_status?.['存疑待查'] || 0) }}<span class="unit">笔</span></div>
        </div>
      </el-col>
    </el-row>

    <!-- 过滤 + 刷新 -->
    <div class="flex-between mb-12">
      <div class="filter-bar">
        <el-radio-group v-model="routeFilter" size="small" @change="load">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="A">A线上</el-radio-button>
          <el-radio-button value="B">B钱包</el-radio-button>
          <el-radio-button value="C">C闪猫</el-radio-button>
          <el-radio-button value="D">D手动</el-radio-button>
          <el-radio-button value="unmatched">未登记</el-radio-button>
        </el-radio-group>
        <el-select v-model="statusFilter" size="small" placeholder="确认状态" clearable style="width:130px;margin-left:8px" @change="load">
          <el-option v-for="s in statusOptions" :key="s" :label="s" :value="s" />
        </el-select>
      </div>
      <el-button size="small" :icon="Refresh" @click="load">刷新逐笔</el-button>
    </div>

    <!-- E 笔逐笔列表 -->
    <el-table :data="lines" border stripe size="small" max-height="460">
      <el-table-column prop="platform_order_no" label="平台订单号" width="180" fixed show-overflow-tooltip />
      <el-table-column label="E 退款时间" width="140">
        <template #default="{ row }">{{ fmtDate(row.refund_time) }}</template>
      </el-table-column>
      <el-table-column label="金额" width="100" align="right">
        <template #default="{ row }">¥{{ fmtMoney(row.refund_amount) }}</template>
      </el-table-column>
      <el-table-column label="自动归类" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="routeTagType(row.auto_route)">{{ routeLabel(row.auto_route) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="候选匹配" min-width="180">
        <template #default="{ row }">
          <template v-if="row.matched_ref?.length">
            <el-tooltip placement="top">
              <template #content>
                <div v-for="(c, i) in row.matched_ref" :key="i" class="cand-line">
                  [{{ routeLabel(c.route) }}] {{ c.ref_no || c.source_id || '' }} ¥{{ fmtMoney(c.amount) }}{{ c.extra ? ' · ' + c.extra : '' }}
                </div>
              </template>
              <el-tag size="small" type="info" effect="plain">{{ row.matched_ref.length }} 个候选</el-tag>
            </el-tooltip>
          </template>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="确认状态" width="130">
        <template #default="{ row }">
          <el-select v-if="canWrite" v-model="row.confirm_status" size="small" style="width:120px"
                     :disabled="savingLine === row.report_id" @change="(v) => onStatusChange(row, v)">
            <el-option v-for="s in statusOptions" :key="s" :label="s" :value="s" />
          </el-select>
          <el-tag v-else size="small" :type="statusTagType(row.confirm_status)">{{ row.confirm_status || '待核对' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="备注/确认人" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="text-muted">{{ row.confirm_note || '' }}</span>
          <span v-if="row.confirmed_name" class="text-muted"> · {{ row.confirmed_name }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openNote(row)">备注</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager-row">
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
                     layout="total, prev, pager, next" small background @current-change="load" />
    </div>

    <!-- 备注输入 dialog -->
    <el-dialog v-model="noteDialog" title="逐笔备注" width="460px">
      <el-input v-model="noteText" type="textarea" :rows="3" placeholder="填写差异原因/处理说明(可选)" />
      <template #footer>
        <el-button size="small" @click="noteDialog = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingNote" @click="saveNote">保存备注</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { reconcile } from '@/api/reconcile'

const props = defineProps({
  checkId: { type: Number, default: null },
  canWrite: { type: Boolean, default: false },
})

const loading = ref(false)
const errorMsg = ref('')
const lines = ref([])
const summary = ref(null)
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const routeFilter = ref('')
const statusFilter = ref('')
const savingLine = ref(null)
const noteDialog = ref(false)
const noteText = ref('')
const savingNote = ref(false)
const noteRow = ref(null)

const statusOptions = ['已核对一致', '差异已确认', '存疑待查', '已排除重复', '待核对']

function routeLabel(r) {
  return { A: 'A线上', B: 'B钱包', C: 'C闪猫', D: 'D手动', unmatched: '未登记' }[r] || r || '—'
}
function routeTagType(r) {
  return { A: 'primary', B: 'info', C: 'success', D: 'warning', unmatched: 'danger' }[r] || 'info'
}
function statusTagType(s) {
  return { 已核对一致: 'success', 差异已确认: 'warning', 存疑待查: 'danger', 已排除重复: 'info' }[s] || 'info'
}
function fmtMoney(n) {
  if (n == null || isNaN(Number(n))) return '0.00'
  return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtDate(s) {
  if (!s) return '—'
  const t = s.includes('T') ? s : `${s}T00:00:00`
  return dayjs(t).format('YYYY-MM-DD HH:mm')
}

async function load() {
  if (!props.checkId) { lines.value = []; summary.value = null; return }
  loading.value = true
  errorMsg.value = ''
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (routeFilter.value) params.route = routeFilter.value
    if (statusFilter.value) params.status = statusFilter.value
    const res = await reconcile.checkLines(props.checkId, params)
    lines.value = res.data.items || []
    total.value = res.data.total || 0
    const sum = await reconcile.checkLinesSummary(props.checkId)
    summary.value = sum.data
  } catch (e) {
    errorMsg.value = '逐笔加载失败: ' + (e?.message || e)
  } finally {
    loading.value = false
  }
}

async function onStatusChange(row, status) {
  if (!props.canWrite) return
  savingLine.value = row.report_id
  try {
    await reconcile.checkLineConfirm(props.checkId, {
      report_id: row.report_id, confirm_status: status, confirm_note: row.confirm_note || '',
    })
    ElMessage.success('已更新')
    load()
  } catch (e) {
    ElMessage.error('更新失败: ' + (e?.message || e))
  } finally {
    savingLine.value = null
  }
}

function openNote(row) {
  noteRow.value = row
  noteText.value = row.confirm_note || ''
  noteDialog.value = true
}

async function saveNote() {
  if (!noteRow.value) return
  savingNote.value = true
  try {
    await reconcile.checkLineConfirm(props.checkId, {
      report_id: noteRow.value.report_id,
      confirm_status: noteRow.value.confirm_status || '待核对',
      confirm_note: noteText.value || '',
    })
    ElMessage.success('备注已保存')
    noteDialog.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.message || e))
  } finally {
    savingNote.value = false
  }
}

watch(() => props.checkId, () => { page.value = 1; load() })
</script>

<style scoped>
.reconcile-lines { min-height: 100px; }
.error-msg { color: #dc2626; padding: 8px 0; }
.kpi-card { border-radius: 8px; padding: 10px 12px; }
.kpi-label { font-size: 12px; margin-bottom: 4px; }
.kpi-value { font-size: 20px; font-weight: 700; color: #374151; }
.kpi-value .unit { font-size: 12px; font-weight: 400; color: #6b7280; }
.kpi-sub { font-size: 12px; color: #6b7280; margin-top: 2px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.filter-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.mb-12 { margin-bottom: 12px; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 8px; }
.cand-line { font-size: 12px; line-height: 1.7; white-space: nowrap; }
.text-muted { color: #9ca3af; }
</style>
