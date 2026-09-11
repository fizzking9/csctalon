<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">事务通知</div>
        <div class="page-sub">系统关键事务执行概要 · 同步 / 推送接收 / 导入 · 独立于数据采集详细日志</div>
      </div>
      <div class="filter-bar">
        <el-select v-model="filters.category" size="small" placeholder="类别" clearable style="width: 130px" @change="reload">
          <el-option v-for="c in categoryOptions" :key="c.value" :label="c.label" :value="c.value" />
        </el-select>
        <el-select v-model="filters.status" size="small" placeholder="结果" clearable style="width: 120px" @change="reload">
          <el-option label="成功" value="success" />
          <el-option label="失败" value="failed" />
          <el-option label="部分成功" value="partial" />
          <el-option label="信息" value="info" />
        </el-select>
        <el-checkbox v-model="filters.unread_only" size="small" @change="reload">仅未读</el-checkbox>
        <el-button size="small" :icon="Refresh" @click="reload">刷新</el-button>
        <el-button size="small" type="primary" plain :disabled="!unreadCount" @click="markAllRead">全部已读</el-button>
      </div>
    </div>

    <!-- KPI 概要 -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#eff6ff">
          <div class="kpi-label" style="color:#2563eb">未读事件</div>
          <div class="kpi-value">{{ formatNum(unreadCount) }}</div>
          <div class="kpi-sub">管理员待查看</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#f0fdf4">
          <div class="kpi-label" style="color:#16a34a">24h成功</div>
          <div class="kpi-value">{{ formatNum(recent24h.success) }}</div>
          <div class="kpi-sub">最近24小时</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#fef2f2">
          <div class="kpi-label" style="color:#dc2626">24h失败</div>
          <div class="kpi-value">{{ formatNum(recent24h.failed) }}</div>
          <div class="kpi-sub">最近24小时</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <div class="kpi-card" style="background:#fefce8">
          <div class="kpi-label" style="color:#ca8a04">24h部分/信息</div>
          <div class="kpi-value">{{ formatNum((recent24h.partial || 0) + (recent24h.info || 0)) }}</div>
          <div class="kpi-sub">partial + info</div>
        </div>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="events" border stripe size="small" max-height="560">
        <el-table-column label="时间" width="150" sortable>
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="类别" width="100">
          <template #default="{ row }">
            <el-tag :type="categoryTagType(row.category)" size="small" effect="plain">{{ categoryLabel(row.category) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标题" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span :style="{ fontWeight: row.is_read ? 400 : 600 }">{{ row.title }}</span>
            <el-tag v-if="row.action === 'complaint_push'" size="small" type="info" effect="plain" style="margin-left:6px">推送</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="概要" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ 'dim': !row.summary }">{{ row.summary || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.detail" size="small" text type="primary" @click="showDetail(row)">详情</el-button>
            <el-button v-if="!row.is_read" size="small" text @click="markRead(row)">标记已读</el-button>
            <span v-if="!row.detail && row.is_read" class="dim">—</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pag-wrap">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          size="small"
          @current-change="load"
          @size-change="page = 1; load()"
        />
      </div>
    </el-card>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="事务详情" width="560px">
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="时间">{{ formatTime(current?.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="类别">{{ categoryLabel(current?.category) }} / {{ current?.action }}</el-descriptions-item>
        <el-descriptions-item label="标题">{{ current?.title }}</el-descriptions-item>
        <el-descriptions-item label="结果">
          <el-tag :type="statusTagType(current?.status)" size="small">{{ statusLabel(current?.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="概要">{{ current?.summary || '—' }}</el-descriptions-item>
        <el-descriptions-item label="详情">
          <pre class="detail-pre">{{ current?.detail || '—' }}</pre>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { listSystemEvents, systemEventSummary, markSystemEventRead, markAllSystemEventsRead } from '@/api/systemEvent'

const loading = ref(false)
const events = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(30)
const summaryData = ref({ unread: 0, recent_24h: {} })
const detailVisible = ref(false)
const current = ref(null)
const filters = reactive({ category: '', status: '', unread_only: false })

const categoryOptions = [
  { value: 'sync', label: '数据同步' },
  { value: 'push', label: '推送接收' },
  { value: 'import', label: '导入' },
  { value: 'alert', label: '预警' },
  { value: 'schedule', label: '定时调度' },
  { value: 'match', label: '关联匹配' },
  { value: 'auth', label: '登录验证' },
]
const unreadCount = computed(() => summaryData.value.unread || 0)
const recent24h = computed(() => summaryData.value.recent_24h || {})

function categoryLabel(c) {
  return categoryOptions.find(x => x.value === c)?.label || c || '—'
}
function categoryTagType(c) {
  const map = { sync: 'primary', push: 'success', import: 'warning', alert: 'danger', schedule: 'info', match: 'info', auth: 'warning' }
  return map[c] || 'info'
}
function statusLabel(s) {
  return { success: '成功', failed: '失败', partial: '部分', info: '信息' }[s] || s
}
function statusTagType(s) {
  return { success: 'success', failed: 'danger', partial: 'warning', info: 'info' }[s] || 'info'
}
function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  return Number.isNaN(n) ? '0' : n.toLocaleString('zh-CN')
}
function formatTime(t) {
  if (!t) return '—'
  const d = dayjs(t)
  return d.isValid() ? d.format('MM-DD HH:mm:ss') : '—'
}

async function load() {
  loading.value = true
  try {
    const p = { page: page.value, page_size: pageSize.value }
    if (filters.category) p.category = filters.category
    if (filters.status) p.status = filters.status
    if (filters.unread_only) p.unread_only = true
    const res = await listSystemEvents(p)
    events.value = res.data?.items || []
    total.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  try {
    const res = await systemEventSummary()
    summaryData.value = res.data || { unread: 0, recent_24h: {} }
  } catch {}
}

function reload() {
  page.value = 1
  load()
  loadSummary()
}

function showDetail(row) {
  current.value = row
  detailVisible.value = true
}

async function markRead(row) {
  await markSystemEventRead(row.id)
  row.is_read = true
  loadSummary()
}

async function markAllRead() {
  const res = await markAllSystemEventsRead()
  ElMessage.success(res.message || '已全部标记已读')
  load()
  loadSummary()
}

onMounted(() => {
  load()
  loadSummary()
})
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mb-16 { margin-bottom: 16px; }
.kpi-card { border-radius: 10px; padding: 12px 14px; min-height: 68px; display: flex; flex-direction: column; gap: 4px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 11px; color: #6b7280; }
.dim { color: #9ca3af; }
.pag-wrap { margin-top: 12px; display: flex; justify-content: flex-end; }
.detail-pre { white-space: pre-wrap; word-break: break-all; font-size: 12px; margin: 0; color: #374151; }
</style>
