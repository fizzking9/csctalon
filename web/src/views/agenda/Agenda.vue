<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">我的待办</div>
        <div class="page-sub">汇聚「该我处理的事」: 待办 / 异常 / 措施 / 政策 / 工单 · 系统推着人走</div>
      </div>
      <div class="flex" style="gap: 8px">
        <el-badge :value="unread" :hidden="!unread">
          <el-button :icon="Bell" @click="tab = 'notifications'">通知</el-button>
        </el-badge>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="待办聚合" name="agenda">
        <el-card>
          <div v-if="summary" class="summary-row mb-16">
            <div v-for="s in summary" :key="s.label" class="summary-chip">
              <span class="summary-label">{{ s.label }}</span>
              <span class="summary-value" :style="{ color: s.color }">{{ s.count }}</span>
            </div>
          </div>

          <el-table v-loading="loading" :data="items" border stripe>
            <el-table-column label="类别" width="110">
              <template #default="{ row }">
                <el-tag :type="catType(row.category)" size="small" effect="plain">{{ row.category }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="标题" show-overflow-tooltip />
            <el-table-column label="优先级" width="80">
              <template #default="{ row }">
                <el-tag v-if="row.priority" :type="priorityType(row.priority)" size="small">{{ row.priority }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="截止/时间" width="150">
              <template #default="{ row }">{{ row.deadline ? formatTime(row.deadline) : '-' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">{{ statusLabel(row.status) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="center">
              <template #default="{ row }">
                <el-button v-if="jumpTarget(row)" link type="primary" @click="go(row)">查看</el-button>
                <span v-else>-</span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!loading && !items.length" description="暂无待办事项" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="通知中心" name="notifications">
        <el-card>
          <div class="flex-between mb-16">
            <span class="page-sub">站内通知与提醒</span>
            <el-button v-if="unread" link type="primary" @click="handleMarkAll">全部已读</el-button>
          </div>
          <el-table v-loading="notifLoading" :data="notifications" border stripe>
            <el-table-column label="" width="60" align="center">
              <template #default="{ row }">
                <el-badge is-dot :hidden="row.is_read" />
              </template>
            </el-table-column>
            <el-table-column prop="title" label="标题" show-overflow-tooltip />
            <el-table-column prop="content" label="内容" show-overflow-tooltip />
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="center">
              <template #default="{ row }">
                <el-button v-if="!row.is_read" link type="primary" @click="handleRead(row)">已读</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!notifLoading && !notifications.length" description="暂无通知" />
        </el-card>
      </el-tab-pane>

      <!-- #116 AI 报告历史: 仅本人可见, 永久保留, 列表+详情抽屉 -->
      <el-tab-pane label="AI 报告历史" name="ai_reports">
        <el-card>
          <div v-if="aiSummary" class="summary-row mb-16">
            <div class="summary-chip">
              <span class="summary-label">总报告</span>
              <span class="summary-value">{{ aiSummary.total }}</span>
            </div>
            <div class="summary-chip">
              <span class="summary-label">近 30 天</span>
              <span class="summary-value">{{ aiSummary.last_30d }}</span>
            </div>
            <div class="summary-chip">
              <span class="summary-label">累计 tokens</span>
              <span class="summary-value">{{ aiSummary.total_tokens_sum }}</span>
            </div>
          </div>

          <div class="flex-between mb-16" style="flex-wrap: wrap; gap: 8px">
            <div class="flex" style="gap: 8px; flex-wrap: wrap">
              <el-date-picker
                v-model="aiFilter.dateRange"
                type="daterange"
                size="small"
                value-format="YYYY-MM-DD"
                start-placeholder="起"
                end-placeholder="止"
                :disabled-date="disableFuture"
                style="width: 230px"
                @change="loadAiReports"
              />
              <el-select v-model="aiFilter.period" size="small" placeholder="周期" clearable style="width: 110px" @change="loadAiReports">
                <el-option label="当日" value="daily" />
                <el-option label="本周" value="weekly" />
                <el-option label="本月" value="monthly" />
                <el-option label="区间" value="range" />
              </el-select>
            </div>
            <el-button :icon="Refresh" size="small" @click="loadAiReports">刷新</el-button>
          </div>

          <el-table v-loading="aiLoading" :data="aiReports" border stripe>
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="窗口" width="160">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ aiPeriodLabel(row.period) }}</el-tag>
                <span v-if="row.window_label" style="margin-left: 4px; color: #6b7280">{{ row.window_label }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="question" label="问句" show-overflow-tooltip />
            <el-table-column label="耗时" width="100">
              <template #default="{ row }">{{ row.total_ms ? (row.total_ms / 1000).toFixed(1) + 's' : '-' }}</template>
            </el-table-column>
            <el-table-column label="tokens" width="100">
              <template #default="{ row }">
                <span v-if="row.total_tokens">入{{ row.prompt_tokens }} / 出{{ row.completion_tokens }} / 总{{ row.total_tokens }}</span>
                <span v-else style="color: #c0c4cc">-</span>
              </template>
            </el-table-column>
            <el-table-column label="模型" width="180">
              <template #default="{ row }">
                <span style="font-size: 12px; color: #6b7280">{{ row.llm_model || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openAiReport(row)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!aiLoading && !aiReports.length" description="暂无 AI 报告(在 AI 助手页发问后, 报告会自动保留到此)" />

          <div v-if="aiPagination.total > aiPagination.page_size" class="flex" style="justify-content: end; margin-top: 12px">
            <el-pagination
              v-model:current-page="aiPagination.page"
              :page-size="aiPagination.page_size"
              :total="aiPagination.total"
              layout="prev, pager, next, total"
              @current-change="loadAiReports"
            />
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- #116 AI 报告详情抽屉 -->
    <el-drawer
      v-model="aiDetail.visible"
      direction="rtl"
      :size="isMobile ? '92%' : '720px'"
      class="ai-report-detail-drawer"
      destroy-on-close
    >
      <template #header>
        <div>
          <div class="page-title" style="margin: 0; font-size: 16px">{{ aiDetail.question || 'AI 报告详情' }}</div>
          <div class="page-sub" style="margin-top: 4px" v-if="aiDetail.created_at">
            {{ formatTime(aiDetail.created_at) }}
            <span v-if="aiDetail.window_label" style="margin-left: 8px">· {{ aiDetail.window_label }}</span>
            <span v-if="aiDetail.total_ms" style="margin-left: 8px">· ⏱ {{ (aiDetail.total_ms / 1000).toFixed(1) }}s</span>
            <span v-if="aiDetail.total_tokens" style="margin-left: 8px">· 📊 总{{ aiDetail.total_tokens }} tokens</span>
          </div>
        </div>
      </template>
      <div v-loading="aiDetail.loading">
        <div v-if="aiDetail.answer_md" class="markdown-body" v-html="renderMarkdown(aiDetail.answer_md)"></div>
        <el-empty v-else-if="!aiDetail.loading" description="报告内容为空" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Bell, Refresh } from '@element-plus/icons-vue'
import { getAgenda, listNotifications, unreadCount, markRead, markAllRead } from '@/api/agenda'
import { aiReports as aiReportsApi, aiReportsSummary, aiReportDetail } from '@/api/aiAssistant'
import { renderMarkdown } from '@/utils/sanitize'
import dayjs from 'dayjs'

const router = useRouter()
const tab = ref('agenda')
const items = ref([])
const notifications = ref([])
const unread = ref(0)
const loading = ref(false)
const notifLoading = ref(false)

// ───────────────────────────────────────────────────────────────
// #116 AI 报告历史: 仅本人可见, 永久保留, 列表+详情抽屉
//   AI 助手页保持现状(即时问答); 我的待办页加完整历史复查入口。
// ───────────────────────────────────────────────────────────────
const aiSummary = ref(null)
const aiFilter = ref({ dateRange: null, period: null })
const aiReports = ref([])
const aiLoading = ref(false)
const aiPagination = ref({ page: 1, page_size: 20, total: 0 })
const aiDetail = ref({
  visible: false,
  loading: false,
  question: '',
  created_at: null,
  window_label: '',
  total_ms: null,
  total_tokens: null,
  answer_md: '',
})

// 移动端响应式(抽屉尺寸)
const isMobile = ref(false)
let _mq = null
function _onMediaChange(e) { isMobile.value = e.matches }

const aiPeriodLabel = (p) => ({
  daily: '当日', weekly: '本周', monthly: '本月', range: '区间', auto: '自动',
}[p] || p || '-')

// 禁选未来日期(数据时间字段使用规范: AI 报告只看历史)
const disableFuture = (date) => date && date.valueOf() > Date.now()

async function loadAiReports() {
  aiLoading.value = true
  try {
    const params = {
      page: aiPagination.value.page,
      page_size: aiPagination.value.page_size,
    }
    if (aiFilter.value.dateRange?.length === 2) {
      params.start = aiFilter.value.dateRange[0]
      params.end = aiFilter.value.dateRange[1]
    }
    if (aiFilter.value.period) params.period = aiFilter.value.period
    const [listRes, sumRes] = await Promise.all([
      aiReportsApi(params),
      aiReportsSummary(),
    ])
    aiReports.value = listRes.data.items || []
    aiPagination.value.total = listRes.data.total || 0
    aiSummary.value = sumRes.data || null
  } catch (e) {
    ElMessage.error('加载 AI 报告历史失败')
  } finally {
    aiLoading.value = false
  }
}

async function openAiReport(row) {
  aiDetail.value = {
    visible: true,
    loading: true,
    question: row.question || '',
    created_at: row.created_at || null,
    window_label: row.window_label || '',
    total_ms: row.total_ms ?? null,
    total_tokens: row.total_tokens ?? null,
    answer_md: '',
  }
  try {
    const res = await aiReportDetail(row.id)
    const d = res.data
    if (d) {
      aiDetail.value.question = d.question ?? aiDetail.value.question
      aiDetail.value.created_at = d.created_at ?? aiDetail.value.created_at
      aiDetail.value.window_label = d.window_label ?? aiDetail.value.window_label
      aiDetail.value.total_ms = d.total_ms ?? aiDetail.value.total_ms
      aiDetail.value.total_tokens = d.total_tokens ?? aiDetail.value.total_tokens
      aiDetail.value.answer_md = d.answer_md || ''
    } else {
      ElMessage.error('报告内容为空')
    }
  } catch (e) {
    ElMessage.error('加载报告详情失败')
  } finally {
    aiDetail.value.loading = false
  }
}

// 切换到 AI 报告历史 tab 时懒加载(避免无谓请求)
watch(tab, (v) => {
  if (v === 'ai_reports' && !aiReports.value.length && !aiLoading.value) {
    loadAiReports()
  }
})

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const catType = (c) => ({ 待办事项: 'warning', 异常事故: 'danger', 管理措施: 'primary', 待读政策: 'info', 待处理工单: 'success' }[c] || 'info')
const priorityType = (p) => ({ 紧急: 'danger', 高: 'danger', 中: 'warning', 低: 'info' }[p] || 'info')
const statusLabel = (s) => {
  const m = { pending: '待办', processing: '处理中', confirming: '待确认', done: '已完成', registered: '登记', calmed: '已平息', reviewed: '已复盘', executing: '执行中', pending_review: '待回看', unread: '未读', '待处理': '待处理', '已完成': '已完成' }
  return m[s] || s || '-'
}

const summary = computed(() => {
  const count = (c) => items.value.filter((i) => i.category === c).length
  const rows = [
    { label: '待办事项', count: count('待办事项'), color: '#e6a23c' },
    { label: '异常事故', count: count('异常事故'), color: '#f56c6c' },
    { label: '管理措施', count: count('管理措施'), color: '#409eff' },
    { label: '待读政策', count: count('待读政策'), color: '#909399' },
    { label: '待处理工单', count: count('待处理工单'), color: '#67c23a' },
  ]
  return rows
})

function jumpTarget(row) {
  return ['todo', 'incident', 'measure', 'policy'].includes(row.ref_type)
}

function go(row) {
  const map = {
    todo: `/todos/${row.ref_id}`,
    incident: '/management/incidents',
    measure: '/management/measures',
    policy: '/strategy/publish',
  }
  const path = map[row.ref_type]
  if (path) router.push(path)
}

async function loadAgenda() {
  loading.value = true
  try {
    const res = await getAgenda()
    items.value = res.data.items || []
    unread.value = res.data.unread_notifications || 0
  } finally {
    loading.value = false
  }
}

async function loadNotifications() {
  notifLoading.value = true
  try {
    const res = await listNotifications({ page: 1, page_size: 50 })
    notifications.value = res.data.items || []
    const uc = await unreadCount()
    unread.value = uc.data.unread || 0
  } finally {
    notifLoading.value = false
  }
}

async function handleRead(row) {
  await markRead(row.id)
  row.is_read = true
  unread.value = Math.max(0, unread.value - 1)
}

async function handleMarkAll() {
  await markAllRead()
  unread.value = 0
  notifications.value.forEach((n) => (n.is_read = true))
  ElMessage.success('已全部标记已读')
}

function loadAll() {
  loadAgenda()
  loadNotifications()
  if (tab.value === 'ai_reports') loadAiReports()
}

onMounted(() => {
  // 移动端媒体监听(抽屉尺寸自适应)
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
  loadAll()
})
</script>

<style scoped>
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.mb-16 {
  margin-bottom: 16px;
}
.flex {
  display: flex;
  align-items: center;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.gap-8 {
  gap: 8px;
}
.summary-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.summary-chip {
  flex: 1;
  min-width: 120px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.summary-label {
  font-size: 12px;
  color: #909399;
}
.summary-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
/* #116 AI 报告详情抽屉: Markdown 渲染样式(与 AI 助手页一致) */
.markdown-body {
  font-size: 14px;
  line-height: 1.7;
  color: #303133;
  word-break: break-word;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 16px 0 8px;
  font-weight: 700;
}
.markdown-body :deep(h1) { font-size: 18px; }
.markdown-body :deep(h2) { font-size: 16px; }
.markdown-body :deep(h3) { font-size: 15px; }
.markdown-body :deep(h4) { font-size: 14px; }
.markdown-body :deep(p) { margin: 8px 0; }
.markdown-body :deep(ul),
.markdown-body :deep(ol) { padding-left: 24px; margin: 8px 0; }
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 6px 10px;
  font-size: 13px;
}
.markdown-body :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}
.markdown-body :deep(code) {
  background: #f5f7fa;
  padding: 2px 4px;
  border-radius: 3px;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 13px;
}
.markdown-body :deep(pre) {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
}
.markdown-body :deep(blockquote) {
  border-left: 4px solid #dcdfe6;
  padding-left: 12px;
  color: #606266;
  margin: 8px 0;
}
</style>
