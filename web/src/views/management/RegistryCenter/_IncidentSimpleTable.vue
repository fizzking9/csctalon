<template>
  <div class="incident-simple-table">
    <el-alert type="info" :closable="false" class="mb-16">
      <template #title>
        快速预览 · 完整列表 / 转预期 / 复盘批注见「管理 → 影响事件」。转预期动作会写操作日志:「影响事件.转预期事项」。
      </template>
    </el-alert>
    <el-table :data="list" v-loading="loading" border stripe @row-click="(row) => $emit('view', row)" style="cursor: pointer">
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small" effect="plain">{{ statusMap[row.status] || row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="事项名" show-overflow-tooltip />
      <el-table-column prop="subject" label="主体" width="140" show-overflow-tooltip />
      <el-table-column label="发生时间" width="160">
        <template #default="{ row }">{{ fmt(row.occurred_at) }}</template>
      </el-table-column>
      <el-table-column label="持续" width="80">
        <template #default="{ row }">{{ row.duration_minutes ? row.duration_minutes + '分' : '-' }}</template>
      </el-table-column>
      <el-table-column label="转预期" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.converted_to_expected" type="success" size="small">已转</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_by" label="登记人" width="100" />
    </el-table>
    <div class="mt-16 flex-between">
      <span>共 {{ total }} 条</span>
      <el-pagination
        layout="total, prev, pager, next"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        @current-change="onPage"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import dayjs from 'dayjs'
import { listIncidents } from '@/api/incident'

const props = defineProps({ filter: { type: Object, required: true } })
defineEmits(['view'])

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(props.filter.page || 1)
const pageSize = ref(props.filter.page_size || 20)

const statusMap = { registered: '登记', processing: '处理中', calmed: '已平息', reviewed: '已复盘' }
function statusType(s) {
  return ({ registered: 'info', processing: 'warning', calmed: '', reviewed: 'success' })[s] || 'info'
}
function fmt(t) { return t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-' }
function onPage(p) {
  page.value = p
  props.filter.page = p
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, status: props.filter.status || undefined }
    if ((props.filter.range || []).length === 2) {
      params.start = props.filter.range[0]
      params.end = props.filter.range[1]
    }
    const res = await listIncidents(params)
    const d = res.data || {}
    total.value = d.total || 0
    list.value = d.items || []
  } finally {
    loading.value = false
  }
}

watch(
  [
    () => [props.filter.status, (props.filter.range || []).join('|'), props.filter._t],
    () => page.value, () => pageSize.value,
  ],
  loadData,
  { immediate: true, deep: true },
)
</script>

<style scoped>
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.flex-between { display: flex; align-items: center; justify-content: space-between; }
</style>
