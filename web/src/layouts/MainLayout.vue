<template>
  <el-container class="layout-container">
    <el-aside :width="isCollapse ? '64px' : '220px'" class="aside">
      <div class="logo">
        <el-icon :size="24"><Monitor /></el-icon>
        <span v-show="!isCollapse" class="logo-text">客服中心管理</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapse"
        :collapse-transition="false"
        router
        background-color="#001529"
        text-color="#b7c0cd"
        active-text-color="#fff"
      >
        <!-- 1. 运营看板(日/周/月/季切换, 全员可见, 第一位) -->
        <el-menu-item index="/dashboards/today">
          <el-icon><DataLine /></el-icon>
          <span>运营看板</span>
        </el-menu-item>

        <!-- 2. 工作台(次之: 待办/任务/快捷入口导向) -->
        <el-menu-item index="/dashboard">
          <el-icon><HomeFilled /></el-icon>
          <span>工作台</span>
        </el-menu-item>

        <!-- 3. 决策看板:管理层可见, 专项深入分析透视(退款/投诉/接待/提现全链路/绩效/专项跟进) -->
        <el-sub-menu v-if="authStore.isManager" index="dashboards">
          <template #title>
            <el-icon><DataAnalysis /></el-icon>
            <span>决策看板</span>
          </template>
          <el-menu-item index="/dashboards/refund">退款看板</el-menu-item>
          <el-menu-item index="/dashboards/complaint">投诉看板</el-menu-item>
          <el-menu-item index="/dashboards/reception">接待看板</el-menu-item>
          <el-menu-item index="/dashboards/refund-withdrawal">退款-提现全链路看板</el-menu-item>
          <el-menu-item index="/dashboards/performance">绩效看板</el-menu-item>
          <el-menu-item index="/dashboards/special">专项跟进</el-menu-item>
        </el-sub-menu>

        <!-- 4. 数据中心 -->
        <el-sub-menu index="data">
          <template #title>
            <el-icon><FolderOpened /></el-icon>
            <span>数据中心</span>
          </template>
          <el-menu-item index="/data/ingest">数据采集</el-menu-item>
          <el-menu-item index="/analytics/builder">视图构建器</el-menu-item>
          <el-menu-item index="/analytics/views">分析视图</el-menu-item>
          <el-menu-item index="/audit/device">设备审计</el-menu-item>
          <el-menu-item index="/audit/person">人员审计</el-menu-item>
          <el-menu-item v-if="authStore.isManager" index="/dashboards/designer">看板配置</el-menu-item>
        </el-sub-menu>

        <el-menu-item index="/todos">
          <el-icon><Bell /></el-icon>
          <span>事项跟进</span>
        </el-menu-item>

        <el-menu-item index="/tasks">
          <el-icon><List /></el-icon>
          <span>专项任务</span>
        </el-menu-item>

        <!-- 策略发布:管理层看子菜单(管理+查阅),客服只看发布页 -->
        <el-sub-menu v-if="authStore.isManager" index="strategy">
          <template #title>
            <el-icon><Document /></el-icon>
            <span>策略发布</span>
          </template>
          <el-menu-item index="/strategy/manage">策略管理</el-menu-item>
          <el-menu-item index="/strategy/publish">文档查阅</el-menu-item>
        </el-sub-menu>
        <el-menu-item v-else index="/strategy/publish">
          <el-icon><Document /></el-icon>
          <span>策略发布</span>
        </el-menu-item>

        <!-- 报表中心(管理层,规划占位) -->
        <el-menu-item v-if="authStore.isManager" index="/report/center">
          <el-icon><Printer /></el-icon>
          <span>报表中心</span>
        </el-menu-item>

        <!-- 管理闭环:报告→审计→措施→跟进→回看(管理层可见) -->
        <el-sub-menu v-if="authStore.isManager" index="management">
          <template #title>
            <el-icon><Flag /></el-icon>
            <span>管理闭环</span>
          </template>
          <el-menu-item index="/management/reports">报告索引</el-menu-item>
          <el-menu-item index="/management/weekly">周报看板</el-menu-item>
          <el-menu-item index="/management/measures">管理措施</el-menu-item>
          <el-menu-item index="/management/incidents">异常事件</el-menu-item>
          <el-menu-item index="/management/templates">看板模板</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="authStore.isAdmin" index="system">
          <template #title>
            <el-icon><Setting /></el-icon>
            <span>系统管理</span>
          </template>
          <el-menu-item index="/system/users">用户管理</el-menu-item>
          <el-menu-item index="/system/persons">人员管理</el-menu-item>
          <el-menu-item index="/system/classification-rules">分类规则</el-menu-item>
          <el-menu-item index="/system/operation-logs">操作日志</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="isCollapse = !isCollapse">
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-dropdown @command="handleCommand">
            <span class="user-info">
              <el-icon><UserFilled /></el-icon>
              {{ authStore.displayName }}
              <el-tag size="small" type="info" effect="plain" style="margin-left: 6px">
                {{ roleLabel }}
              </el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 同步中提示条: 不阻塞任何功能, 只是告诉用户此刻数据可能稍旧,
           用户正常读写不受影响, PostgreSQL MVCC 天然支持并发。 -->
      <el-alert
        v-if="syncBanner.visible"
        :title="syncBanner.title"
        :type="syncBanner.type"
        :description="syncBanner.description"
        show-icon
        :closable="false"
        class="sync-banner"
      >
        <template v-if="syncBanner.percent != null" #default>
          <el-progress
            :percentage="syncBanner.percent"
            :stroke-width="8"
            :text-inside="false"
            :status="syncBanner.progressStatus"
          />
        </template>
      </el-alert>

      <el-main class="main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { taskStatus } from '@/api/fetch'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isCollapse = ref(false)

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => route.meta.title || '')
const roleLabel = computed(() => {
  const map = { admin: '管理员', supervisor: '主管', agent: '客服' }
  return map[authStore.role] || ''
})

// ---------- 全局同步状态轮询 & 黄条提示 ----------
// 全局轻量轮询:15秒一次,仅用于UI提示,不阻塞任何操作
const SYNC_POLL_MS = 15 * 1000
const rawSync = reactive({
  is_syncing: false,
  is_writing_heavy: false,
  percent: 0,
  current_phase: null,
  current_source: null,
  task_id: null,
  last_success_at: null,
})

let _pollTimer = null

async function _pollSyncOnce() {
  try {
    const resp = await taskStatus(null, { silentError: true })
    const data = (resp && resp.data) || resp || {}
    rawSync.is_syncing = !!data.is_syncing
    rawSync.is_writing_heavy = !!data.is_writing_heavy
    rawSync.percent = typeof data.percent === 'number' ? data.percent : 0
    rawSync.current_phase = data.current_phase || null
    rawSync.current_source = data.current_source || null
    rawSync.task_id = data.task_id || null
    rawSync.last_success_at = data.last_success_at || null
  } catch (e) {
    // 网络/后端 502 等错误不影响 UI,静默忽略
  }
}

const syncBanner = computed(() => {
  if (!rawSync.is_syncing && !rawSync.is_writing_heavy) {
    return { visible: false }
  }
  const sourceLabel = rawSync.current_source === '__post_sync__'
    ? '后处理(关联匹配/宽表物化/统计刷新)'
    : rawSync.current_source
    ? `数据源「${rawSync.current_source}」`
    : ''
  const phaseLabel = rawSync.current_phase ? ` · 当前:${rawSync.current_phase}` : ''
  if (rawSync.is_writing_heavy) {
    // 写入高峰(红色 warning→danger 级): 正在导入/构建宽表,缓存策略已切换
    const descParts = []
    descParts.push(`正在批量写入数据库${sourceLabel ? ' — ' + sourceLabel : ''}${phaseLabel}`)
    descParts.push('所有页面照常可用(PostgreSQL MVCC 读写不互锁)，展示的可能是几分钟前的缓存数据,同步完成后会自动刷新。')
    return {
      visible: true,
      type: 'warning',
      title: `🔥 写入高峰期 · 同步中 ${rawSync.percent ?? 0}%`,
      description: descParts.join('  \n'),
      percent: rawSync.percent ?? null,
      progressStatus: rawSync.percent >= 100 ? 'success' : '',
    }
  }
  // 仅抓取/等待阶段(黄色 info): 主要是 Playwright 等待 BMS, 对 DB 没压力
  return {
    visible: true,
    type: 'info',
    title: `后台同步最新数据中 · 进度 ${rawSync.percent ?? 0}%`,
    description: `正在自动抓取/整理最新记录${phaseLabel}。所有功能正常使用，同步完成后自动更新缓存。`,
    percent: rawSync.percent ?? null,
    progressStatus: '',
  }
})

onMounted(() => {
  _pollSyncOnce()
  _pollTimer = setInterval(_pollSyncOnce, SYNC_POLL_MS)
})

onBeforeUnmount(() => {
  if (_pollTimer) {
    clearInterval(_pollTimer)
    _pollTimer = null
  }
})

async function handleCommand(cmd) {
  if (cmd === 'logout') {
    await authStore.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.layout-container {
  height: 100%;
}
.aside {
  background: #001529;
  transition: width 0.3s;
  overflow-x: hidden;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}
.logo-text {
  overflow: hidden;
}
.aside :deep(.el-menu) {
  border-right: none;
}
.header {
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e6e6e6;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.collapse-btn {
  font-size: 20px;
  cursor: pointer;
}
.header-right .user-info {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}
.main {
  background: #f0f2f5;
  padding: 16px;
  overflow-y: auto;
}
.sync-banner {
  margin: 0 16px;
  border-radius: 6px;
}
.sync-banner :deep(.el-alert__description) {
  white-space: pre-line;
}
.sync-banner :deep(.el-progress) {
  margin-top: 6px;
  max-width: 480px;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
