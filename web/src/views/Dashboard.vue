<template>
  <div class="page-container dashboard">
    <!-- 顶部问候 -->
    <div class="greeting">
      <div>
        <div class="page-title" style="margin: 0">{{ greeting }},{{ authStore.displayName }}</div>
        <div class="page-sub">{{ todayText }}</div>
      </div>
      <el-tag :type="roleTagType" effect="plain">{{ roleLabel }}</el-tag>
    </div>

    <!-- ============ 管理层工作台 (次之: 待办/任务/快捷入口导向) ============ -->
    <template v-if="authStore.isManager">
      <!-- 一、快捷入口 (最大卡片, 一点直达 运营看板/决策看板/核心管理页) -->
      <el-card class="mb-16" shadow="never">
        <template #header>
          <span style="font-weight:600">快捷入口</span>
        </template>
        <div class="shortcut-grid">
          <div class="shortcut-item shortcut-hero is-primary" @click="$router.push('/dashboards/today')">
            <el-icon :size="40"><DataLine /></el-icon>
            <div class="si-info">
              <div class="si-title">运营看板</div>
              <div class="si-desc">日 / 周 / 月 / 季度 · 全员工作一览</div>
            </div>
            <el-icon class="si-arrow"><ArrowRight /></el-icon>
          </div>

          <el-divider direction="vertical" style="height: 96px" />

          <div class="shortcut-item-group">
            <div class="si-subtitle" style="font-size:12px;color:#909399;margin-bottom:8px">决策看板 · 专项深入分析透视 (仅管理层)</div>
            <div class="shortcut-row">
              <div class="shortcut-chip" @click="$router.push('/dashboards/refund')">
                <el-icon color="#e6a23c"><Wallet /></el-icon>
                <span>退款看板</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/complaint')">
                <el-icon color="#f56c6c"><WarningFilled /></el-icon>
                <span>投诉看板</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/reception')">
                <el-icon color="#409eff"><Service /></el-icon>
                <span>接待看板</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/refund-withdrawal')">
                <el-icon color="#9b59b6"><Money /></el-icon>
                <span>提现全链路</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/performance')">
                <el-icon color="#67c23a"><UserFilled /></el-icon>
                <span>绩效看板</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/special')">
                <el-icon color="#909399"><Aim /></el-icon>
                <span>专项跟进</span>
              </div>
            </div>
          </div>

          <el-divider direction="vertical" style="height: 96px" />

          <div class="shortcut-item-group">
            <div class="si-subtitle" style="font-size:12px;color:#909399;margin-bottom:8px">管理闭环 · 日常流程</div>
            <div class="shortcut-row">
              <div class="shortcut-chip" @click="$router.push('/todos')">
                <el-icon color="#e6a23c"><Bell /></el-icon>
                <span>待办事项</span>
                <el-badge v-if="todoCount" :value="todoCount" class="sc-badge" />
              </div>
              <div class="shortcut-chip" @click="$router.push('/tasks')">
                <el-icon color="#409eff"><List /></el-icon>
                <span>专项任务</span>
                <el-badge v-if="taskCount" :value="taskCount" class="sc-badge" />
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/reports')">
                <el-icon color="#34495e"><Document /></el-icon>
                <span>报告索引</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/weekly')">
                <el-icon color="#16a085"><Calendar /></el-icon>
                <span>周报看板</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/measures')">
                <el-icon color="#d35400"><Flag /></el-icon>
                <span>管理措施</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/strategy/manage')">
                <el-icon color="#8e44ad"><EditPen /></el-icon>
                <span>策略管理</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/report/center')">
                <el-icon color="#2c3e50"><Printer /></el-icon>
                <span>报表中心</span>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 二、两栏: 待办事项 + 专项任务 (左) | 未读策略 + 最近操作 (右) -->
      <el-row :gutter="16">
        <el-col :xs="24" :md="14">
          <!-- 待办事项 (Top 10, 直接点击可跳转详情) -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>待办事项 <el-tag size="small" type="warning" round style="margin-left:6px">{{ todoCount }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/todos')">全部待办 ›</el-button>
              </div>
            </template>
            <ul class="mini-list" v-loading="loadingTodos">
              <li v-for="t in todoList" :key="t.id" class="mini-list-item" @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="todoPrioTag(t.priority)" effect="plain" class="mr-8">{{ t.priority || '中' }}</el-tag>
                <span class="mli-title">{{ t.title }}</span>
                <span class="mli-time">{{ formatDay(t.due_date || t.created_at) }}</span>
                <el-tag size="small" :type="todoStatusTag(t.status)" effect="plain">{{ t.status || '待处理' }}</el-tag>
              </li>
              <li v-if="!loadingTodos && !todoList.length" class="mli-empty">暂无待办，去 <el-link type="primary" @click.stop="$router.push('/todos')">创建一条</el-link> 吧 ✅</li>
            </ul>
          </el-card>

          <!-- 专项任务 (Top 10) -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>专项任务 <el-tag size="small" type="primary" round style="margin-left:6px">{{ taskCount }} 个进行中</el-tag></span>
                <el-button link type="primary" @click="$router.push('/tasks')">全部任务 ›</el-button>
              </div>
            </template>
            <ul class="mini-list" v-loading="loadingTasks">
              <li v-for="tk in taskList" :key="tk.id" class="mini-list-item" @click="$router.push(`/tasks/${tk.id}`)">
                <el-icon color="#409eff" style="flex-shrink:0"><List /></el-icon>
                <span class="mli-title">{{ tk.title }}</span>
                <div class="mli-progress" v-if="tk.progress != null">
                  <el-progress :percentage="Number(tk.progress) || 0" :stroke-width="6" :show-text="false" style="width: 120px" />
                  <span class="mli-progress-text">{{ tk.progress }}%</span>
                </div>
                <span class="mli-time">{{ formatDay(tk.end_date || tk.start_date || tk.created_at) }}</span>
                <el-tag size="small" :type="taskStatusTag(tk.status)" effect="dark">{{ tk.status || '进行中' }}</el-tag>
              </li>
              <li v-if="!loadingTasks && !taskList.length" class="mli-empty">暂无专项任务，去 <el-link type="primary" @click.stop="$router.push('/tasks')">新建任务</el-link></li>
            </ul>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="10">
          <!-- 未读策略 -->
          <el-card class="mb-16" shadow="never" v-if="unreadStrategies.length || loadingStrategies">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>未读策略规范 <el-tag v-if="unreadStrategies.length" size="small" type="warning" round>{{ unreadStrategies.length }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/strategy/publish')">全部策略 ›</el-button>
              </div>
            </template>
            <div class="strategy-list" v-loading="loadingStrategies">
              <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
                <span class="unread-dot" />
                <span class="strategy-title">{{ s.title }}</span>
                <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
              </div>
              <div v-if="!loadingStrategies && !unreadStrategies.length" class="text-muted" style="text-align:center;padding:16px">
                策略规范已全部阅读 ✅
              </div>
            </div>
          </el-card>

          <!-- 最近操作(仅 admin) -->
          <el-card v-if="authStore.isAdmin" shadow="never">
            <template #header>
              <div class="flex-between" style="margin: -8px 0">
                <span>最近系统操作</span>
                <el-button link type="primary" @click="$router.push('/system/operation-logs')">全部日志 ›</el-button>
              </div>
            </template>
            <div class="op-list" v-loading="loadingOps">
              <div v-for="op in recentOps" :key="op.id" class="op-item">
                <div class="op-top">
                  <el-tag size="small" :type="opTagType(op.action)">{{ op.action }}</el-tag>
                  <span class="op-time">{{ formatTime(op.created_at) }}</span>
                </div>
                <div class="op-meta">{{ op.username || '(未登录)' }} · {{ op.method }} {{ op.path }}</div>
              </div>
              <div v-if="!loadingOps && !recentOps.length" class="text-muted" style="text-align: center; padding: 16px">
                暂无操作记录
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- ============ 客服工作台 (次之: 快捷入口 + 策略 + 个人待办) ============ -->
    <template v-else>
      <el-row :gutter="16">
        <el-col :xs="24" :md="15">
          <!-- 未读策略(最关键) -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin: -8px 0">
                <span>策略规范 <el-tag v-if="unreadStrategies.length" size="small" type="warning" round>{{ unreadStrategies.length }} 未读</el-tag></span>
                <el-button link type="primary" @click="$router.push('/strategy/publish')">查看全部 ›</el-button>
              </div>
            </template>
            <div v-if="unreadStrategies.length || loadingStrategies" class="strategy-list" v-loading="loadingStrategies">
              <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
                <span class="unread-dot" />
                <span class="strategy-title">{{ s.title }}</span>
                <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
              </div>
            </div>
            <el-result v-else icon="success" title="策略已全部阅读" sub-title="当前没有未读的策略与规范">
              <template #extra>
                <el-button type="primary" @click="$router.push('/strategy/publish')">前往策略发布页</el-button>
              </template>
            </el-result>
          </el-card>

          <!-- 个人待办 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>我的待办 <el-tag size="small" type="warning" round style="margin-left:6px">{{ todoCount }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/todos')">全部待办 ›</el-button>
              </div>
            </template>
            <ul class="mini-list" v-loading="loadingTodos">
              <li v-for="t in todoList" :key="t.id" class="mini-list-item" @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="todoPrioTag(t.priority)" effect="plain">{{ t.priority || '中' }}</el-tag>
                <span class="mli-title">{{ t.title }}</span>
                <span class="mli-time">{{ formatDay(t.due_date || t.created_at) }}</span>
              </li>
              <li v-if="!loadingTodos && !todoList.length" class="mli-empty">暂无待办 👍</li>
            </ul>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="9">
          <!-- 快捷入口 -->
          <el-card class="mb-16" shadow="never">
            <template #header>快捷入口</template>
            <div class="agent-shortcuts">
              <div class="shortcut-tile" @click="$router.push('/dashboards/today')">
                <el-icon :size="32" color="#409eff"><DataLine /></el-icon>
                <span>运营看板</span>
              </div>
              <div class="shortcut-tile" @click="$router.push('/strategy/publish')">
                <el-icon :size="32" color="#9b59b6"><Document /></el-icon>
                <span>策略发布</span>
              </div>
              <div class="shortcut-tile" @click="$router.push('/todos')">
                <el-icon :size="32" color="#e6a23c"><Bell /></el-icon>
                <span>事项跟进</span>
                <el-badge v-if="todoCount" :value="todoCount" class="shortcut-badge" />
              </div>
              <div class="shortcut-tile" @click="$router.push('/tasks')">
                <el-icon :size="32" color="#16a085"><List /></el-icon>
                <span>专项任务</span>
                <el-badge v-if="taskCount" :value="taskCount" class="shortcut-badge" />
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { listTodos } from '@/api/todo'
import { listStrategy } from '@/api/strategy'
import { listTasks } from '@/api/task'
import { listOperationLogs } from '@/api/operationLog'
import dayjs from 'dayjs'
import {
  ArrowRight, Bell, List, Calendar, Flag, EditPen, Printer,
  Wallet, WarningFilled, Service, Money, UserFilled, Aim, Document, DataLine,
} from '@element-plus/icons-vue'

const authStore = useAuthStore()

const todoCount = ref(0)
const todoList = ref([])
const loadingTodos = ref(false)
const taskCount = ref(0)
const taskList = ref([])
const loadingTasks = ref(false)
const unreadStrategies = ref([])
const loadingStrategies = ref(false)
const recentOps = ref([])
const loadingOps = ref(false)

const greeting = computed(() => {
  const h = dayjs().hour()
  if (h < 6) return '凌晨好'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const todayText = computed(() => {
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][dayjs().day()]
  return dayjs().format('YYYY年MM月DD日') + ' ' + wd + ' · ' + (authStore.isManager ? '管理层工作台' : '客服工作台')
})
const roleLabel = computed(() => ({ admin: '管理员', supervisor: '主管', agent: '客服' }[authStore.role] || ''))
const roleTagType = computed(() => (authStore.role === 'admin' ? 'danger' : authStore.role === 'supervisor' ? 'warning' : 'info'))

const formatNum = (v) => (v == null ? '0' : Number(v).toLocaleString('zh-CN'))
const formatTime = (t) => (t ? dayjs(t).format('MM-DD HH:mm') : '-')
const formatDay = (t) => (t ? dayjs(t).format('YYYY-MM-DD') : '-')

function opTagType(a) {
  if (a.includes('失败') || a.includes('拒绝') || a.includes('删除')) return 'danger'
  if (a.includes('发布') || a.includes('创建')) return 'success'
  if (a.includes('更新') || a.includes('跟进')) return 'warning'
  if (a.includes('登录')) return 'info'
  return 'info'
}
function todoPrioTag(p) {
  const k = String(p || '中')
  if (k.includes('高') || k === 'H') return 'danger'
  if (k.includes('低') || k === 'L') return 'info'
  return 'warning'
}
function todoStatusTag(s) {
  const k = String(s || '待处理')
  if (k.includes('已完成') || k.includes('done')) return 'success'
  if (k.includes('处理中') || k.includes('进行')) return 'primary'
  if (k.includes('关闭') || k.includes('取消')) return 'info'
  return 'warning'
}
function taskStatusTag(s) {
  const k = String(s || '进行中')
  if (k.includes('已完成') || k.includes('完成')) return 'success'
  if (k.includes('进行') || k.includes('执行')) return 'primary'
  if (k.includes('暂停') || k.includes('风险')) return 'warning'
  if (k.includes('关闭') || k.includes('取消')) return 'info'
  return 'danger'
}

async function loadTodos() {
  loadingTodos.value = true
  try {
    const res = await listTodos({ page: 1, page_size: 10, sort: 'priority_desc' })
    todoCount.value = Number(res.data?.total) || 0
    todoList.value = (res.data?.items || []).slice(0, 10)
  } catch {
    todoCount.value = 0; todoList.value = []
  } finally {
    loadingTodos.value = false
  }
}
async function loadTasks() {
  loadingTasks.value = true
  try {
    const res = await listTasks({ page: 1, page_size: 10 })
    taskCount.value = Number(res.data?.total) || 0
    taskList.value = (res.data?.items || []).slice(0, 10)
  } catch {
    taskCount.value = 0; taskList.value = []
  } finally {
    loadingTasks.value = false
  }
}
async function loadStrategies() {
  loadingStrategies.value = true
  try {
    const res = await listStrategy({ status: 'published', page: 1, page_size: 50 })
    unreadStrategies.value = (res.data?.items || []).filter((s) => !s.has_read).slice(0, 5)
  } catch {
    unreadStrategies.value = []
  } finally {
    loadingStrategies.value = false
  }
}
async function loadRecentOps() {
  if (!authStore.isAdmin) return
  loadingOps.value = true
  try {
    const res = await listOperationLogs({ page: 1, page_size: 6 })
    recentOps.value = res.data?.items || []
  } catch {
    recentOps.value = []
  } finally {
    loadingOps.value = false
  }
}

onMounted(async () => {
  await Promise.all([
    loadTodos(), loadTasks(), loadStrategies(), authStore.isManager && loadRecentOps(),
  ].filter(Boolean))
})
</script>

<style scoped>
.dashboard { padding-bottom: 24px; }
.greeting {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.card-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.card-row > div {
  cursor: pointer;
}

/* 投诉工单卡片的 extra 占比样式 */
.card-complaint-extra {
  display: inline-block;
  font-size: 12px;
  font-weight: 500;
  color: #f56c6c;
  margin-top: 6px;
  padding: 1px 8px;
  background: #fef0f0;
  border-radius: 10px;
}

/*
以下样式已迁移到 components/dashboard/* 组件:
  .stat / .stat-icon / .stat-label / .stat-value / .stat-unit / .stat-sub  → KpiCard.vue
  .route-card / .route-sum / .route-label / .route-amount / .route-count / .route-tip → RouteSplit.vue
*/

.preset-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}
.preset-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.preset-item:hover {
  border-color: #409eff;
  background: #ecf5ff;
}
.preset-name {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preset-arrow {
  color: #c0c4cc;
}
.strategy-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.strategy-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
}
.strategy-item:hover {
  background: #f5f7fa;
}
.unread-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e6a23c;
  flex-shrink: 0;
}
.strategy-title {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mini-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
}
.mini-row:last-child {
  border-bottom: none;
}
.mini-row:hover .mini-label { color: #409eff; }
.mini-label {
  flex: 1;
  font-size: 14px;
  color: #303133;
}
.mini-count {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}
.mini-arrow { color: #c0c4cc; }
.op-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.op-item {
  padding: 6px 0;
  border-bottom: 1px solid #f5f5f5;
}
.op-item:last-child { border-bottom: none; }
.op-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2px;
}
.op-time {
  font-size: 12px;
  color: #909399;
}
.op-meta {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', Menlo, monospace;
}
.agent-shortcuts {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.shortcut-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 28px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
  min-width: 120px;
}
.shortcut-tile:hover {
  border-color: #409eff;
  background: #ecf5ff;
  transform: translateY(-2px);
}
.shortcut-tile span {
  font-size: 14px;
  color: #303133;
}
.shortcut-badge {
  position: absolute;
  top: 8px;
  right: 8px;
}
.text-muted { color: #c0c4cc; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
</style>
