<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="ingest-tabs" lazy>
      <el-tab-pane label="数据查看" name="browser">
        <Browser />
      </el-tab-pane>
      <el-tab-pane label="数据同步(统一入口)" name="sync">
        <SyncDashboard />
      </el-tab-pane>
      <el-tab-pane label="文件上传" name="upload">
        <Upload />
      </el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="投诉接入" name="doc-sync">
        <ComplaintIngest />
      </el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="共享文档采集" name="shared-doc">
        <SharedDocSources />
      </el-tab-pane>
      <el-tab-pane label="采集日志" name="logs">
        <CollectionLogs :embedded="true" />
      </el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="数据质量" name="data-quality">
        <DataQuality />
      </el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="数据源管理" name="data-sources">
        <DataSources :embedded="true" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import Upload from './Upload.vue'
import SyncDashboard from './SyncDashboard.vue'
import Browser from './Browser.vue'
import CollectionLogs from './CollectionLogs.vue'
import DataQuality from '@/views/management/DataQuality.vue'
import ComplaintIngest from './ComplaintIngest.vue'
import SharedDocSources from './SharedDocSources.vue'
import DataSources from '@/views/system/DataSources.vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const ALL_TABS = ['browser', 'sync', 'upload', 'doc-sync', 'shared-doc', 'logs', 'data-quality', 'data-sources']
const PUBLIC_TABS = ['browser', 'sync', 'upload', 'logs']

function resolveInitialTab() {
  const want = route.query.tab
  // 旧 tab 名兼容: 原来的 fetch(BMS同步) / scrm(SCRM同步) / sync-settings(旧同步设置) 都重定向到新的统一入口 sync
  if (want === 'fetch' || want === 'scrm') return 'sync'
  // 旧采集记录 → 重定向到新的统一采集日志
  if (want === 'records') return 'logs'
  // 旧同步设置 Tab → 重定向到 sync Tab,并展开「抓取模式 & 凭证设置」手风琴面板
  if (want === 'sync-settings') {
    router.replace({
      query: { ...route.query, tab: 'sync', panel: 'settings' },
    })
    return 'sync'
  }
  if (want && ALL_TABS.includes(want)) {
    if (!isAdmin.value && (want === 'data-sources' || want === 'doc-sync' || want === 'shared-doc')) return 'sync'
    return want
  }
  // ?ingest=fetch 旧兼容 → 新统一入口
  if (route.query.ingest === 'fetch') return 'sync'
  // 默认: 统一数据同步入口(日常最高频)
  return 'sync'
}
const activeTab = ref(resolveInitialTab())

// 切 tab 时同步 URL query, 刷新/分享链接时回到同一个 tab
watch(activeTab, (v) => {
  const query = { ...route.query, tab: v }
  if (query.ingest) delete query.ingest
  router.replace({ query })
})

// 旧兼容: 页面挂载后若 URL 是 ?ingest=fetch 且无 ?tab, 重定向到 tab=fetch
onMounted(() => {
  if (route.query.ingest === 'fetch' && !route.query.tab) {
    activeTab.value = 'fetch'
    const query = { ...route.query, tab: 'fetch' }
    delete query.ingest
    router.replace({ query })
  }
})
</script>

<style scoped>
.ingest-tabs :deep(.el-tabs__content) {
  padding-top: 4px;
}
.ingest-tabs :deep(.records-embedded),
.ingest-tabs :deep(.embedded-wrap) {
  padding: 0;
}
.ingest-tabs :deep(.records-embedded .el-card),
.ingest-tabs :deep(.embedded-wrap .el-card) {
  border-radius: 4px;
}
</style>
