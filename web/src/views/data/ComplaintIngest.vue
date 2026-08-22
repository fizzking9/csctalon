<template>
  <div class="complaint-ingest">
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      投诉登记数据(315/卡商)支持三种接入方式，可按实际情况任选其一或并用，数据都会汇入同一投诉台账并按设备SN关联工单：
      <b>自动监测</b>(系统定时读取腾讯文档) · <b>定时推送</b>(WorkBuddy 等外部工具推送) · <b>文件导入</b>(手动上传)。
      系统按指纹去重，重复数据不会累积；建议只保留一种常用方式，避免冗余采集与日志混淆。
    </el-alert>

    <!-- 采集方式开关 -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="card-header">
          <span>采集方式开关(开启哪些接入方式)</span>
        </div>
      </template>
      <div class="mode-switches">
        <div v-for="m in modes" :key="m.mode" class="mode-switch-item">
          <div class="mode-switch-info">
            <b>{{ m.name }}</b>
            <span class="hint-text">{{ modeHint(m.mode) }}</span>
          </div>
          <el-switch
            v-model="m.enabled"
            :disabled="m.mode === 'import'"
            :loading="switching === m.mode"
            @change="(v) => onModeChange(m, v)"
          />
        </div>
      </div>
      <el-alert
        v-if="modesMonitor && modesPush"
        type="warning"
        :closable="false"
        show-icon
        class="mt-12"
        title="「自动监测」与「定时推送」同时开启：两份来源会以不同 sync_source 重复入库同一批投诉（指纹去重不会产生脏数据），建议只保留一种，避免冗余采集"
      />
    </el-card>

    <el-tabs v-model="mode" class="mode-tabs">
      <el-tab-pane label="① 自动监测(腾讯文档)" name="monitor" :disabled="!modesMonitor">
        <TencentDocSync />
      </el-tab-pane>
      <el-tab-pane label="② 定时推送(WorkBuddy/外部工具)" name="push" :disabled="!modesPush">
        <WorkBuddyPush />
      </el-tab-pane>
      <el-tab-pane label="③ 文件导入(手动上传)" name="import" :disabled="!modesImport">
        <Upload />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import TencentDocSync from './TencentDocSync.vue'
import WorkBuddyPush from './WorkBuddyPush.vue'
import Upload from './Upload.vue'
import { getIngestModes, setIngestMode } from '@/api/docSync'

const route = useRoute()
const router = useRouter()
const ALL_MODES = ['monitor', 'push', 'import']
const mode = ref(ALL_MODES.includes(route.query.mode) ? route.query.mode : 'monitor')
const modes = ref([])
const switching = ref('')

const modeMap = computed(() => Object.fromEntries(modes.value.map((m) => [m.mode, m.enabled])))
const modesMonitor = computed(() => modeMap.value.monitor !== false)
const modesPush = computed(() => modeMap.value.push !== false)
const modesImport = computed(() => modeMap.value.import !== false)

function modeHint(m) {
  return {
    monitor: '系统定时读取共享文档',
    push: '外部工具定时推送',
    import: '手动操作, 随时可用',
  }[m]
}

async function onModeChange(m, v) {
  switching.value = m.mode
  try {
    await setIngestMode(m.mode, v)
    ElMessage.success(`${m.name}已${v ? '开启' : '关闭'}`)
    // 若关闭了当前所在 tab, 切回仍开启的模式
    if (!v && !modeMap.value[mode.value]) {
      const first = ALL_MODES.find((x) => x !== m.mode && modeMap.value[x] !== false)
      if (first) mode.value = first
    }
  } catch (e) {
    m.enabled = !v
    ElMessage.error(e?.response?.data?.detail || '设置失败')
  } finally {
    switching.value = ''
  }
}

onMounted(async () => {
  try {
    const res = await getIngestModes()
    modes.value = res.data || []
    if (modeMap.value[mode.value] === false) {
      const first = ALL_MODES.find((x) => modeMap.value[x] !== false)
      if (first) mode.value = first
    }
  } catch (e) {
    /* 静默 */
  }
})

// 模式切换同步 URL, 支持深链(?mode=push)直达
watch(mode, (v) => {
  router.replace({ query: { ...route.query, mode: v } })
})
</script>

<style scoped>
.complaint-ingest {
  max-width: 1080px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
.mode-switches {
  display: flex;
  gap: 32px;
  flex-wrap: wrap;
}
.mode-switch-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}
.mode-switch-info {
  display: flex;
  flex-direction: column;
}
.hint-text {
  font-size: 12px;
  color: #909399;
}
.mode-tabs :deep(.el-tabs__content) {
  padding-top: 8px;
}
.mb-16 {
  margin-bottom: 16px;
}
.mt-12 {
  margin-top: 12px;
}
</style>
