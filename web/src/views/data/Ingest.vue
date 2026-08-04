<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="ingest-tabs" lazy>
      <el-tab-pane label="数据查看" name="browser">
        <Browser />
      </el-tab-pane>
      <el-tab-pane label="BMS同步" name="fetch">
        <FetchRefund />
      </el-tab-pane>
      <el-tab-pane label="SCRM同步" name="scrm">
        <FetchScrm />
      </el-tab-pane>
      <el-tab-pane label="文件上传" name="upload">
        <Upload />
      </el-tab-pane>
      <el-tab-pane label="采集记录" name="records">
        <Records :embedded="true" />
      </el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="同步设置" name="sync-settings">
        <SyncSettings :embedded="true" />
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
import FetchRefund from './FetchRefund.vue'
import FetchScrm from './FetchScrm.vue'
import Browser from './Browser.vue'
import Records from './Records.vue'
import SyncSettings from '@/views/system/SyncSettings.vue'
import DataSources from '@/views/system/DataSources.vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

// 4 个全员 tab + SCRM(新增) + 2 个管理员 tab(末尾)
const ALL_TABS = ['browser', 'fetch', 'scrm', 'upload', 'records', 'sync-settings', 'data-sources']
const PUBLIC_TABS = ['browser', 'fetch', 'scrm', 'upload', 'records']

function resolveInitialTab() {
  const want = route.query.tab
  if (want && ALL_TABS.includes(want)) {
    // 管理员才能访问末尾两个 tab,否则强制回退到 BMS 同步
    if (!isAdmin.value && (want === 'sync-settings' || want === 'data-sources')) return 'fetch'
    return want
  }
  // ?ingest=fetch 旧兼容
  if (route.query.ingest === 'fetch') return 'fetch'
  // 默认: BMS 同步(日常最高频)
  return 'fetch'
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
