<template>
  <div class="page-container">
    <el-tabs v-model="activeTab" class="ingest-tabs" lazy>
      <el-tab-pane label="文件上传" name="upload">
        <Upload />
      </el-tab-pane>
      <el-tab-pane label="BMS 同步" name="fetch">
        <FetchRefund />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import Upload from './Upload.vue'
import FetchRefund from './FetchRefund.vue'

// 默认停在"文件上传";query.ingest=fetch 时切到 BMS 同步(供外部跳转)
const route = useRoute()
const activeTab = ref(route.query.ingest === 'fetch' ? 'fetch' : 'upload')
</script>

<style scoped>
.ingest-tabs :deep(.el-tabs__content) {
  padding-top: 8px;
}
</style>
