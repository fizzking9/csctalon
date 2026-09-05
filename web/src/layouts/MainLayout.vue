<template>
  <el-container class="layout-container">
    <el-aside v-if="!isMobile" :width="isCollapse ? '64px' : '220px'" class="aside">
      <div class="logo">
        <el-icon :size="24"><Monitor /></el-icon>
        <span v-show="!isCollapse" class="logo-text">数据分析工具应用</span>
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

        <!-- 3. 专项分析:管理层可见, 专项深入分析透视(退款/投诉/接待/提现全链路/绩效/专项跟进) -->
        <el-sub-menu v-if="authStore.isManager" index="dashboards">
          <template #title>
            <el-icon><DataAnalysis /></el-icon>
            <span>专项分析</span>
          </template>
          <el-menu-item index="/dashboards/refund">退款分析</el-menu-item>
          <el-menu-item index="/dashboards/refund-recovery">挽回分析</el-menu-item>
          <el-menu-item index="/dashboards/complaint-analysis">投诉分析</el-menu-item>
          <el-menu-item v-if="showByVisibility('debug_only')" index="/dashboards/complaint-audit">投诉核查</el-menu-item>
          <el-menu-item v-if="showByVisibility('debug_only')" index="/dashboards/reception">接待分析</el-menu-item>
          <el-menu-item v-if="showByVisibility('debug_only')" index="/dashboards/performance">绩效分析</el-menu-item>
          <el-menu-item v-if="showByVisibility('debug_only')" index="/dashboards/special">专项跟进</el-menu-item>
        </el-sub-menu>

        <!-- 设备/人员时序审计:业务功能(从退款看板-人员/设备分析「时序」跳转),不进入主菜单 -->
        <el-menu-item v-if="showByVisibility('debug_only')" index="/todos">
          <el-icon><Bell /></el-icon>
          <span>事项跟进</span>
        </el-menu-item>

        <el-menu-item v-if="showByVisibility('debug_only')" index="/tasks">
          <el-icon><List /></el-icon>
          <span>专项任务</span>
        </el-menu-item>

        <el-menu-item v-if="showByVisibility('debug_only')" index="/management/device-tags">
          <el-icon><CollectionTag /></el-icon>
          <span>设备专项标签</span>
        </el-menu-item>

        <el-menu-item v-if="showByVisibility('debug_only')" index="/management/invoices">
          <el-icon><Tickets /></el-icon>
          <span>发票登记</span>
        </el-menu-item>

        <el-menu-item v-if="showByVisibility('debug_only')" index="/management/kb-articles">
          <el-icon><Reading /></el-icon>
          <span>知识库</span>
        </el-menu-item>

        <!-- 策略发布:管理层看子菜单(管理+查阅),客服只看发布页 -->
        <el-sub-menu v-if="authStore.isManager && showByVisibility('debug_only')" index="strategy">
          <template #title>
            <el-icon><Document /></el-icon>
            <span>策略发布</span>
          </template>
          <el-menu-item v-if="authStore.isAdmin || authStore.isSupervisor" index="/strategy/manage">策略管理</el-menu-item>
          <el-menu-item index="/strategy/publish">文档查阅</el-menu-item>
        </el-sub-menu>
        <el-menu-item v-else-if="showByVisibility('debug_only')" index="/strategy/publish">
          <el-icon><Document /></el-icon>
          <span>策略发布</span>
        </el-menu-item>

        <!-- 报表中心(管理层,规划占位): 仅 admin + supervisor 可见全量报表 -->
        <el-menu-item v-if="(authStore.isAdmin || authStore.isSupervisor) && showByVisibility('debug_only')" index="/report/center">
          <el-icon><Printer /></el-icon>
          <span>报表中心</span>
        </el-menu-item>

        <!-- 管理闭环:报告→审计→措施→跟进→回看(管理层可见) -->
        <el-sub-menu v-if="authStore.isManager && showByVisibility('debug_only')" index="management">
          <template #title>
            <el-icon><Flag /></el-icon>
            <span>管理闭环</span>
          </template>
          <el-menu-item index="/management/reports">报告索引</el-menu-item>
          <el-menu-item index="/management/weekly">周报看板</el-menu-item>
          <el-menu-item index="/management/measures">管理措施</el-menu-item>
          <el-menu-item index="/management/policies">政策措施</el-menu-item>
          <el-menu-item index="/management/incidents">影响事件</el-menu-item>
          <el-menu-item index="/management/schedule">排班管理</el-menu-item>
          <el-menu-item index="/management/alerts">预警中心</el-menu-item>
          <el-menu-item index="/management/upstream-tickets">跨部门协作</el-menu-item>
          <el-menu-item v-if="authStore.isAdmin || authStore.isSupervisor" index="/management/templates">看板模板</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="authStore.isAdmin" index="system">
          <template #title>
            <el-icon><Setting /></el-icon>
            <span>系统管理</span>
          </template>
          <el-menu-item index="/system/users">用户管理</el-menu-item>
          <el-menu-item index="/system/persons">人员管理</el-menu-item>
          <el-menu-item index="/data/ingest">数据采集</el-menu-item>
          <el-menu-item index="/data/device-flow-audit">设备流量审计</el-menu-item>
          <el-menu-item index="/analytics/builder">视图构建器</el-menu-item>
          <el-menu-item index="/analytics/views">视图清单</el-menu-item>
          <el-menu-item index="/dashboards/designer">看板配置</el-menu-item>
          <el-menu-item index="/system/classification-rules">分类规则</el-menu-item>
          <el-menu-item index="/system/operation-logs">操作日志</el-menu-item>
          <el-menu-item index="/system/system-events">
            <el-badge v-if="eventUnread > 0" :value="eventUnread" :max="99" class="menu-badge">
              <span>事务通知</span>
            </el-badge>
            <span v-else>事务通知</span>
          </el-menu-item>
          <el-menu-item index="/system/cache">缓存管理</el-menu-item>
          <el-menu-item index="/system/external-access">外部接入</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-icon v-if="!isMobile" class="collapse-btn" @click="isCollapse = !isCollapse">
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
          <el-icon v-else class="collapse-btn" @click="mobileMenuOpen = true">
            <Menu />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item v-if="!isMobile" :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <!-- 快捷工具:人员分析/设备分析/数据同步(仅admin) -->
          <div class="quick-tools">
            <el-tooltip content="人员分析" placement="bottom">
              <el-button :icon="User" circle size="small" text @click="openPersonAnalysis" />
            </el-tooltip>
            <el-tooltip content="设备分析" placement="bottom">
              <el-button :icon="Monitor" circle size="small" text @click="openDeviceAnalysis" />
            </el-tooltip>
            <el-tooltip v-if="authStore.isManager" content="AI 分析助手" placement="bottom">
              <el-button :icon="MagicStick" circle size="small" text @click="openAssistant" />
            </el-tooltip>
            <el-tooltip v-if="authStore.isAdmin" content="手动同步(增量)" placement="bottom">
              <el-button
                :icon="Upload"
                circle
                size="small"
                text
                :loading="manualSyncLoading"
                :disabled="rawSync.is_syncing"
                @click="handleManualSync"
              />
            </el-tooltip>
            <el-tooltip v-if="authStore.isAdmin" content="事务通知" placement="bottom">
              <el-badge :value="eventUnread" :max="99" :hidden="eventUnread === 0" class="event-bell">
                <el-button :icon="Bell" circle size="small" text @click="router.push('/system/system-events')" />
              </el-badge>
            </el-tooltip>
          </div>
          <el-dropdown @command="handleCommand">
            <span class="user-info">
              <el-icon><UserFilled /></el-icon>
              <span class="user-name">{{ authStore.displayName }}</span>
              <el-tag class="role-tag" size="small" type="info" effect="plain">
                {{ roleLabel }}
              </el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="dashboard" divided>
                  <el-icon style="margin-right:6px"><HomeFilled /></el-icon>工作台
                </el-dropdown-item>
                <el-dropdown-item command="agenda">
                  <el-icon style="margin-right:6px"><Bell /></el-icon>我的待办
                </el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 同步中提示条(单行小字): 不阻塞任何功能, 只是告诉用户此刻数据可能稍旧,
           用户正常读写不受影响, PostgreSQL MVCC 天然支持并发。 -->
      <el-alert
        v-if="syncBanner.visible"
        :type="syncBanner.type"
        show-icon
        :closable="false"
        class="sync-banner"
      >
        <template #default>
          <div class="sync-banner-inner">
            <span class="sync-banner-text">{{ syncBanner.text }}</span>
            <el-progress
              v-if="syncBanner.percent != null"
              class="sync-banner-progress"
              :percentage="syncBanner.percent"
              :stroke-width="6"
              :status="syncBanner.progressStatus"
            />
          </div>
        </template>
      </el-alert>

      <el-main class="main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
        <!-- 全局页脚: 备案信息(网站备案要求) -->
        <footer class="global-footer">
          <span v-if="siteInfo.beian_note" class="footer-note">{{ siteInfo.beian_note }}</span>
          <a v-if="siteInfo.icp_no" href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">{{ siteInfo.icp_no }}</a>
          <a v-else href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">ICP备案号：待备案</a>
          <template v-if="siteInfo.psb_no">
            <span class="sep">|</span>
            <a href="https://beian.mps.gov.cn/" target="_blank" rel="noopener noreferrer">{{ siteInfo.psb_no }}</a>
          </template>
          <span class="copyright">© {{ currentYear }} {{ siteInfo.copyright_holder || 'tanzk' }} · {{ siteInfo.site_name || '数据分析工具应用' }}</span>
        </footer>
      </el-main>
    </el-container>
  </el-container>

  <!-- 移动端抽屉菜单(仅开放: 今日看板 / 运营看板 / 数据采集[管理员]) -->
  <el-drawer
    v-model="mobileMenuOpen"
    direction="ltr"
    size="72%"
    :with-header="false"
    class="mobile-drawer"
  >
    <div class="mobile-menu">
      <div class="mobile-menu-user">
        <el-icon><UserFilled /></el-icon>
        <span class="mobile-menu-name">{{ authStore.displayName }}</span>
        <el-tag size="small" type="info" effect="plain">{{ roleLabel }}</el-tag>
      </div>
      <el-menu :default-active="activeMenu" router @select="mobileMenuOpen = false">
        <el-menu-item index="/dashboards/today">
          <el-icon><DataLine /></el-icon>
          <span>今日看板</span>
        </el-menu-item>
        <el-menu-item index="/dashboards/operation">
          <el-icon><TrendCharts /></el-icon>
          <span>运营看板</span>
        </el-menu-item>
        <!-- 专项分析: 管理层可见(与桌面端一致, 含退款/挽回/投诉) -->
        <el-sub-menu v-if="authStore.isManager" index="mobile-dashboards">
          <template #title>
            <el-icon><DataAnalysis /></el-icon>
            <span>专项分析</span>
          </template>
          <el-menu-item index="/dashboards/refund">退款分析</el-menu-item>
          <el-menu-item index="/dashboards/refund-recovery">挽回分析</el-menu-item>
          <el-menu-item index="/dashboards/complaint-analysis">投诉分析</el-menu-item>
        </el-sub-menu>
        <el-menu-item v-if="authStore.isAdmin" index="/data/ingest">
          <el-icon><Upload /></el-icon>
          <span>数据采集</span>
        </el-menu-item>
      </el-menu>
      <el-button type="danger" plain class="mobile-logout" @click="logout">退出登录</el-button>
    </div>
  </el-drawer>

    <!-- 全局审计弹窗(点击任意 SN / 人员 唤起) -->
    <AuditDialog />

    <!-- 全局 AI 分析助手(右上角按钮唤起, 管理层可见) -->
    <AssistantDrawer />
  </template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Bell, User, Monitor, Upload, MagicStick } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { taskStatus, taskSubmit } from '@/api/fetch'
import { systemEventSummary } from '@/api/systemEvent'
import { getSiteInfo } from '@/api/siteInfo'
import AuditDialog from '@/components/AuditDialog.vue'
import AssistantDrawer from '@/components/AssistantDrawer.vue'
import { useAudit } from '@/composables/useAudit'
import { useAssistant } from '@/composables/useAssistant'
import { showByVisibility } from '@/utils/env'

const { openQuickAnalysis } = useAudit()
const { openAssistant } = useAssistant()

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isCollapse = ref(false)
const isMobile = ref(false)
const mobileMenuOpen = ref(false)
const manualSyncLoading = ref(false)

// 站点备案信息(全局页脚展示)
const currentYear = new Date().getFullYear()
const siteInfo = ref({})
async function loadSiteInfo() {
  try {
    const res = await getSiteInfo()
    siteInfo.value = res.data || {}
  } catch (e) {
    siteInfo.value = {}
  }
}
onMounted(loadSiteInfo)

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => route.meta.title || '')
const roleLabel = computed(() => {
  const map = { admin: '系统管理员', supervisor: '主管', specialty_lead: '专项组长', specialist: '专员', agent: '普通客服' }
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
let _pollTimer2 = null
let _mq = null
// 系统事务通知未读数(侧边栏/顶部角标, 15s 轮询)
const eventUnread = ref(0)

async function _pollEventUnread() {
  try {
    const res = await systemEventSummary()
    eventUnread.value = res.data?.unread || 0
  } catch (e) {
    // 静默: 接口异常不影响主布局
  }
}

function _onMediaChange(e) {
  isMobile.value = e.matches
}

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
  const pct = rawSync.percent ?? 0
  const phase = rawSync.current_phase ? ` · ${rawSync.current_phase}` : ''
  if (rawSync.is_writing_heavy) {
    // 写入高峰(红色 warning→danger 级): 正在导入/构建宽表,缓存策略已切换
    return {
      visible: true,
      type: 'warning',
      text: `后台同步中(写入高峰) ${pct}%${phase}`,
      percent: pct,
      progressStatus: pct >= 100 ? 'success' : '',
    }
  }
  // 仅抓取/等待阶段(黄色 info): 主要是 Playwright 等待 BMS, 对 DB 没压力
  return {
    visible: true,
    type: 'info',
    text: `后台同步最新数据中 ${pct}%${phase}`,
    percent: pct,
    progressStatus: '',
  }
})

onMounted(() => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
  _pollSyncOnce()
  _pollTimer = setInterval(_pollSyncOnce, SYNC_POLL_MS)
  _pollEventUnread()
  // 事务通知角标与同步状态同节奏轮询
  _pollTimer2 = setInterval(_pollEventUnread, SYNC_POLL_MS)
})

onBeforeUnmount(() => {
  if (_mq) {
    _mq.removeEventListener('change', _onMediaChange)
    _mq = null
  }
  if (_pollTimer) {
    clearInterval(_pollTimer)
    _pollTimer = null
  }
  if (_pollTimer2) {
    clearInterval(_pollTimer2)
    _pollTimer2 = null
  }
})

async function logout() {
  await authStore.logout()
  router.push('/login')
}

async function handleCommand(cmd) {
  if (cmd === 'logout') {
    await logout()
  } else if (cmd === 'dashboard') {
    router.push('/dashboard')
  } else if (cmd === 'agenda') {
    router.push('/agenda')
  }
}

function openPersonAnalysis() {
  openQuickAnalysis('person')
}
function openDeviceAnalysis() {
  openQuickAnalysis('device')
}

// 右上角手动同步: 默认增量模式提交全部默认源,无需进入数据采集页面
async function handleManualSync() {
  if (manualSyncLoading.value) return
  manualSyncLoading.value = true
  try {
    const res = await taskSubmit({ time_range: 'incremental' })
    const taskId = res.data?.task_id
    ElMessage.success(res.data?.message || `同步任务已提交${taskId ? `(${taskId})` : ''},后台执行中…`)
    // 立即拉取一次进度,让顶部提示条尽快出现
    await _pollSyncOnce()
  } catch (e) {
    // 请求拦截器已弹错误详情(忙碌/冷却中/凭证缺失等),这里不再重复提示
  } finally {
    manualSyncLoading.value = false
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
.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}
.quick-tools {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-right: 8px;
  padding-right: 8px;
  border-right: 1px solid #e5e7eb;
}
.quick-tools .el-button {
  color: #6b7280;
}
.quick-tools .el-button:hover {
  color: #2563eb;
  background: #eff6ff;
}
.event-bell .el-badge__content {
  font-size: 10px;
}
.menu-badge .el-badge__content {
  font-size: 10px;
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
/* 全局页脚: 备案信息 */
.global-footer {
  margin-top: 24px;
  padding: 16px 0 4px;
  text-align: center;
  font-size: 12px;
  color: #909399;
  line-height: 1.8;
  border-top: 1px solid #e6e6e6;
}
.global-footer a {
  color: #909399;
  text-decoration: none;
}
.global-footer a:hover {
  color: #409eff;
}
.global-footer .footer-note {
  margin-right: 12px;
}
.global-footer .sep {
  margin: 0 8px;
  color: #c0c4cc;
}
.global-footer .copyright {
  margin-left: 12px;
}
.sync-banner {
  margin: 0 16px;
  border-radius: 6px;
  padding: 5px 12px;
  flex: none;
}
.sync-banner :deep(.el-alert__icon) {
  font-size: 14px;
}
.sync-banner-inner {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 16px;
}
.sync-banner-text {
  font-size: 12px;
  line-height: 1.4;
  color: inherit;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sync-banner-progress {
  flex: 0 0 180px;
}
.sync-banner-progress :deep(.el-progress__text) {
  font-size: 12px;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* 角色标签(桌面端头部用户信息) */
.role-tag {
  margin-left: 6px;
}

/* 移动端抽屉菜单 */
.mobile-drawer :deep(.el-drawer__body) {
  padding: 0;
}
.mobile-menu {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.mobile-menu-user {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  border-bottom: 1px solid #eee;
}
.mobile-menu-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}
.mobile-menu :deep(.el-menu) {
  border-right: none;
}
.mobile-logout {
  margin: auto 16px 16px;
}

/* 移动端响应式(<768px) */
@media (max-width: 768px) {
  .header {
    padding: 0 8px;
  }
  .header-left {
    gap: 8px;
  }
  .user-name,
  .role-tag {
    display: none;
  }
  .quick-tools {
    margin-right: 4px;
    padding-right: 4px;
  }
  .quick-tools .el-button {
    padding: 6px;
  }
  .main {
    padding: 8px;
  }
  .sync-banner {
    margin: 0 8px;
  }
}
</style>
