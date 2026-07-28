<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="toolbar">
        <span class="page-title" style="margin: 0; margin-right: 16px">专项跟进</span>
        <el-select
          v-model="currentId"
          placeholder="选择专项看板"
          style="width: 280px"
          @change="onSwitch"
        >
          <el-option
            v-for="d in specialDashboards"
            :key="d.id"
            :label="d.name"
            :value="d.id"
          />
        </el-select>
        <el-button :icon="Refresh" text @click="loadSpecial">刷新列表</el-button>
      </div>
      <el-button type="primary" :icon="Setting" @click="$router.push('/dashboards/designer')">
        配置专项看板
      </el-button>
    </div>

    <DashboardView v-if="currentId" :key="currentId" :dashboard-id="currentId" />
    <el-empty v-else description="暂无专项看板,可在数据中心-看板配置中创建(分类选「专项」)" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Setting, Refresh } from '@element-plus/icons-vue'
import DashboardView from './DashboardView.vue'
import { listDashboards } from '@/api/dashboard'

const specialDashboards = ref([])
const currentId = ref(null)

async function loadSpecial() {
  const res = await listDashboards({ category: '专项', page: 1, page_size: 100 })
  specialDashboards.value = res.data.items
  // 默认选第一个
  if (!currentId.value && specialDashboards.value.length) {
    currentId.value = specialDashboards.value[0].id
  }
}

function onSwitch() {
  // currentId 变化 → :key 重建 DashboardView
}

onMounted(loadSpecial)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
