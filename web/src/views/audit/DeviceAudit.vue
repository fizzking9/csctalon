<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">设备审计时序</div>
      <div class="flex" style="gap: 8px; align-items: center">
        <el-select
          v-model="sn"
          filterable
          remote
          clearable
          placeholder="输入设备 SN 搜索"
          :remote-method="searchDevices"
          :loading="searching"
          style="width: 280px"
          @change="onSnChange"
        >
          <el-option
            v-for="d in deviceOptions"
            :key="d.sn"
            :label="`${d.sn}${d.device_type ? ' · ' + d.device_type : ''}`"
            :value="d.sn"
          />
        </el-select>
        <el-select v-model="days" style="width: 110px" @change="loadTimeline">
          <el-option label="近 30 天" :value="30" />
          <el-option label="近 90 天" :value="90" />
          <el-option label="近 180 天" :value="180" />
          <el-option label="近 365 天" :value="365" />
          <el-option label="不限" :value="0" />
        </el-select>
        <el-select v-model="limit" style="width: 100px" @change="loadTimeline">
          <el-option :value="50" label="50 条" />
          <el-option :value="100" label="100 条" />
          <el-option :value="200" label="200 条" />
          <el-option :value="500" label="500 条" />
        </el-select>
        <el-button type="primary" :icon="Refresh" @click="loadTimeline" :disabled="!sn">刷新</el-button>
      </div>
    </div>

    <!-- 设备基础信息卡片 -->
    <el-card v-if="subject" class="mb-16" shadow="never">
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="设备 SN">{{ subject.sn }}</el-descriptions-item>
        <el-descriptions-item label="设备类型">{{ subject.device_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="类型备注">{{ subject.device_type_remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="商户">{{ subject.merchant_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ subject.status || '-' }}</el-descriptions-item>
        <el-descriptions-item label="激活时间">{{ fmtDate(subject.active_time) }}</el-descriptions-item>
        <el-descriptions-item label="标签">
          <el-tag
            v-for="t in subject.tags || []"
            :key="t.id"
            size="small"
            :color="t.color"
            style="margin-right: 4px; color: #fff"
          >
            {{ t.name }}
          </el-tag>
          <span v-if="!(subject.tags || []).length">-</span>
        </el-descriptions-item>
        <el-descriptions-item label="记录总数">{{ total }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 空状态 -->
    <el-empty v-if="!sn" description="请选择设备 SN 查看审计时序" />

    <!-- 时序流 + 明细表双视图 -->
    <AuditTimeline
      v-else
      :items="items"
      :type-meta="typeMeta"
      :loading="loading"
    />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import AuditTimeline from '@/components/AuditTimeline.vue'
import { getDeviceTimeline, searchDevices as searchDevicesApi } from '@/api/audit'

const route = useRoute()
const sn = ref('')
const days = ref(90)
const limit = ref(100)
const items = ref([])
const typeMeta = ref({})
const subject = ref(null)
const total = ref(0)
const loading = ref(false)

// 设备搜索下拉
const deviceOptions = ref([])
const searching = ref(false)

async function searchDevices(query) {
  if (!query) {
    deviceOptions.value = []
    return
  }
  searching.value = true
  try {
    const res = await searchDevicesApi({ keyword: query, page: 1, page_size: 30 })
    deviceOptions.value = res.data?.items || []
  } finally {
    searching.value = false
  }
}

function onSnChange(val) {
  if (val) loadTimeline()
}

async function loadTimeline() {
  if (!sn.value) return
  loading.value = true
  try {
    const res = await getDeviceTimeline(sn.value, { days: days.value, limit: limit.value })
    const data = res.data || {}
    items.value = data.items || []
    typeMeta.value = data.type_meta || {}
    subject.value = data.subject || null
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function fmtDate(t) {
  if (!t) return '-'
  return dayjs(t).format('YYYY-MM-DD HH:mm')
}

onMounted(() => {
  // 支持从 query 预填 sn(从其他页跳转)
  const q = route.query.sn
  if (q) {
    sn.value = String(q)
    deviceOptions.value = [{ sn: String(q) }]
    loadTimeline()
  }
})
</script>

<style scoped>
.flex {
  display: flex;
}
</style>
