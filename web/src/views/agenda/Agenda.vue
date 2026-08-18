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

          <el-table :data="items" v-loading="loading" border stripe>
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
          <el-table :data="notifications" v-loading="notifLoading" border stripe>
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
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Bell, Refresh } from '@element-plus/icons-vue'
import { getAgenda, listNotifications, unreadCount, markRead, markAllRead } from '@/api/agenda'
import dayjs from 'dayjs'

const router = useRouter()
const tab = ref('agenda')
const items = ref([])
const notifications = ref([])
const unread = ref(0)
const loading = ref(false)
const notifLoading = ref(false)

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
}

onMounted(loadAll)
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
</style>
