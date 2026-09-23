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
        <!-- 菜单由 config/menu.js 唯一驱动(见该文件顶部注释):
             桌面端与移动端共用同一配置, 杜绝两份硬编码漂移 (#46, A-20260910-004) -->
        <MenuTree :nodes="MENU_GROUPS" />
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
                <!--
                  ───────────────────────── 设计说明（Design Rationale）─────────────────────────
                  目的：替系统管理员在 csctalon 与两个独立管理台（云上网络管理面板 / 算力通道
                        管理台）之间切换时省掉一次重复登录（低频，只在需要管网络或算力时用）。
                  逻辑：免密方向是「csctalon 签发 → 目标系统消费」——本系统有真实用户与角色体系，
                        而网络管理面板持有 docker.sock（等价宿主 root），不适合当业务系统的身份
                        权威，故由本系统签 60 秒一次性票据、目标侧验签后建自己的会话。
                        入口放「用户下拉」而非左侧菜单树：菜单树由 config/menu.js 唯一驱动且只承载
                        站内路由（有「菜单项必须存在于 router」的防悬空测试），外部系统链接塞进去
                        会破坏该契约。
                  口径：SSO_SHARED_SECRET 未配置则不显示网络管理面板入口；talon 地址来自
                        SSO_TALON_URL_LAN / SSO_TALON_URL_ZT（它没有公网入口，前端按当前访问
                        来源择一：从办公网打开用办公网地址，否则用 ZeroTier 地址；走 talon 自身
                        可信网段免密，不签票）。票据放 URL fragment，不发给服务端，故不进两侧
                        access log、不随 Referer 外泄。
                  取舍：不做「统一门户 / 单点登出」——两个目标系统仍保留各自独立登录（可用性冗余：
                        面板自身登录入口在票据链路故障时仍可用，本系统登录亦不依赖它们）；
                        不把 talon 纳入票据体系（它已有可信网段免密，签票属重复建设）；
                        不给 talon 配公网域名或反代（它按来源网段放行，任何反代都会让来源变成
                        代理机、等于对全网开放 —— 实测反代机源 IP 落入可信网段即可免密拿到全部
                        API Key，工单 #188）；
                        不做用户映射（本入口只服务系统管理员单人，不面向多角色）。
                  工单：#187 / #188
                  ─────────────────────────────────────────────────────────────────────────────
                -->
                <el-dropdown-item v-if="authStore.isAdmin && ssoEntries.netmgr_enabled" command="netmgr" divided>
                  <el-icon style="margin-right:6px"><Monitor /></el-icon>网络管理面板
                  <el-tooltip placement="left" effect="light" :show-after="200">
                    <template #content>
                      <div style="max-width:270px;line-height:1.6">
                        <b>为什么有这个入口</b><br />
                        替系统管理员省掉一次重复登录：点一下即用 csctalon 身份免密进入云上的
                        网络管理面板（ZeroTier / 代理组网管理）。<br /><br />
                        <b>怎么做到的</b><br />
                        本系统签发 60 秒一次性票据，面板验签后建立自己的会话；票据只走浏览器
                        地址片段，两端服务器都不记录。<br /><br />
                        <b>我们不做什么</b><br />
                        不向非管理员开放；面板自身登录入口保留 —— 本入口不可用时可直接登录面板。
                      </div>
                    </template>
                    <el-icon style="margin-left:6px;color:#c0c4cc;font-size:13px"><InfoFilled /></el-icon>
                  </el-tooltip>
                </el-dropdown-item>
                <el-dropdown-item v-if="authStore.isAdmin && talonTarget" command="talon">
                  <el-icon style="margin-right:6px"><Cpu /></el-icon>算力通道管理台
                  <el-tooltip placement="left" effect="light" :show-after="200">
                    <template #content>
                      <div style="max-width:280px;line-height:1.6">
                        <b>为什么有这个入口</b><br />
                        算力通道管理台与网络管理面板一样是独立系统，这里只放一个直链，方便管理员
                        从本系统跳过去。<br /><br />
                        <b>它只在办公网 / ZeroTier 内可达</b><br />
                        该管理台部署在内网，没有公网入口，因此这里按你的访问来源自动选地址：
                        从办公网打开本页用办公网地址，否则用 ZeroTier 地址。
                        <b>如果点开打不开，说明当前设备既不在办公网、也没接入 ZeroTier ——
                        先连上 ZeroTier 再点即可。</b><br /><br />
                        <b>我们不做什么</b><br />
                        不给它配公网域名或反代：该管理台按来源网段放行，任何反代都会让来源变成
                        代理机、等于对全网开放；也不做统一登出（两个系统会话互不影响）。
                      </div>
                    </template>
                    <el-icon style="margin-left:6px;color:#c0c4cc;font-size:13px"><InfoFilled /></el-icon>
                  </el-tooltip>
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

  <!-- 移动端抽屉菜单(与桌面端同源, 由 config/menu.js 驱动) -->
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
      <!-- 移动端与桌面端共用同一份菜单配置(config/menu.js) ——
           历史问题: 两端各自硬编码, 移动端只有 2 项, 与桌面严重不一致 (#46) -->
      <el-menu
        :default-active="activeMenu"
        router
        @select="mobileMenuOpen = false"
      >
        <MenuTree :nodes="MENU_GROUPS" />
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
import { Bell, User, Monitor, Upload, MagicStick, Cpu, InfoFilled } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { taskStatus, taskSubmit } from '@/api/fetch'
import { systemEventSummary } from '@/api/systemEvent'
import { getSiteInfo } from '@/api/siteInfo'
import { getSsoEntries, issueNetmgrTicket } from '@/api/sso'
import AuditDialog from '@/components/AuditDialog.vue'
import AssistantDrawer from '@/components/AssistantDrawer.vue'
import MenuTree from '@/components/MenuTree.vue'
import { MENU_GROUPS } from '@/config/menu'
import { useAudit } from '@/composables/useAudit'
import { useAssistant } from '@/composables/useAssistant'
import { useLayoutState, useLayoutStateWriter } from '@/composables/useLayoutState'

const { openQuickAnalysis } = useAudit()
const { openAssistant } = useAssistant()
// 布局层共享状态: 未读数写到这里, MenuTree(递归组件)只读消费, 避免 N 倍轮询
const { setEventUnread } = useLayoutStateWriter()
const layoutState = useLayoutState()

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

// ---------- 外部管理台入口（仅管理员，工单 #187/#188）----------
// 只拉「有没有配、地址是什么」；票据在点击时才签发（60 秒有效，不做预取/缓存）
const ssoEntries = reactive({ netmgr_enabled: false, talon_url_lan: '', talon_url_zt: '' })
const ssoJumping = ref(false)

async function loadSsoEntries() {
  if (!authStore.isAdmin) return
  try {
    const res = await getSsoEntries()
    ssoEntries.netmgr_enabled = !!res.data?.netmgr_enabled
    ssoEntries.talon_url_lan = res.data?.talon_url_lan || ''
    ssoEntries.talon_url_zt = res.data?.talon_url_zt || ''
  } catch (e) {
    // 静默：入口不可用不影响布局其它功能
  }
}

// talon 没有公网入口，只有办公网与 ZeroTier 两条路径，按当前访问来源选：
// 本页若就是从办公网地址打开的（hostname 与办公网地址一致）→ 办公网必然可达，用它；
// 其余情况（公网域名 / ZT 地址）→ 用 ZeroTier 地址，打不开时由 ⓘ 提示先连 ZT。
// 无法在前端探测可达性：https 页面 fetch http:// 目标会被浏览器按混合内容拦截。
const talonTarget = computed(() => {
  const lan = ssoEntries.talon_url_lan
  const zt = ssoEntries.talon_url_zt
  if (!lan) return zt
  if (!zt) return lan
  try {
    if (window.location.hostname === new URL(lan).hostname) return lan
  } catch (e) {
    // 配置不是合法 URL 时退化为用 ZeroTier 地址
  }
  return zt
})

// 免密进入网络管理面板：取一次性票据后整页跳转（票据即凭证，不落本地存储、不写日志）
async function openNetmgr() {
  if (ssoJumping.value) return
  ssoJumping.value = true
  try {
    const res = await issueNetmgrTicket()
    const url = res.data?.url
    if (!url) throw new Error('未获取到跳转地址')
    window.location.href = url
  } catch (e) {
    // 请求拦截器已提示错误详情（未配置 / 权限不足 / 网络异常），此处不重复提示
  } finally {
    ssoJumping.value = false
  }
}

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
// 存于 useLayoutState 共享单例(菜单树递归组件共用, 避免各自轮询)
const eventUnread = computed(() => layoutState.eventUnread)

async function _pollEventUnread() {
  try {
    const res = await systemEventSummary()
    setEventUnread(res.data?.unread || 0)
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
  loadSsoEntries()
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
  } else if (cmd === 'netmgr') {
    await openNetmgr()
  } else if (cmd === 'talon') {
    // 直链：新标签打开，保留本页状态（地址由后端配置下发并按访问来源择一，不在前端硬编码）
    window.open(talonTarget.value, '_blank', 'noopener')
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
