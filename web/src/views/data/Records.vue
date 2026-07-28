<template>
  <div class="page-container">
    <div class="page-title">导入记录</div>
    <el-card>
      <div class="flex-between mb-16">
        <div class="flex gap-8">
          <el-select v-model="filter.data_type" placeholder="数据类型" clearable style="width: 180px" @change="loadData">
            <el-option v-for="(l, v) in typeLabels" :key="v" :label="l" :value="v" />
          </el-select>
          <el-select v-model="filter.source" placeholder="来源" clearable style="width: 140px" @change="loadData">
            <el-option label="手动上传" value="manual" />
            <el-option label="BMS同步" value="bms" />
          </el-select>
        </div>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column label="数据类型" width="160">
          <template #default="{ row }">{{ typeLabels[row.data_type] || row.data_type }}</template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <el-tag :type="sourceType(row.source)" size="small">{{ sourceLabel(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="filename" label="文件名" show-overflow-tooltip />
        <el-table-column prop="sheet_name" label="Sheet" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="统计" width="280">
          <template #default="{ row }">
            <span>总{{ row.total_rows }} / 新增{{ row.inserted_rows }} / 更新{{ row.updated_rows }} / 失败{{ row.failed_rows }}</span>
          </template>
        </el-table-column>
        <el-table-column label="关联" width="100">
          <template #default="{ row }">{{ row.matched_rows }}</template>
        </el-table-column>
        <el-table-column prop="uploaded_by" label="操作人" width="100" />
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          @current-change="loadData"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { listUploadRecords } from '@/api/upload'
import { TYPE_LABELS } from './templates'
import dayjs from 'dayjs'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const filter = reactive({ data_type: '', source: '' })
const typeLabels = TYPE_LABELS

const statusMap = { pending: '待处理', parsing: '解析中', imported: '已导入', failed: '失败' }
const statusLabel = (s) => statusMap[s] || s
const statusType = (s) => ({ imported: 'success', failed: 'danger', parsing: 'warning', pending: 'info' }[s] || 'info')

// 来源:manual=手动上传, bms=BMS同步, null=历史(迁移前记录)
const sourceLabel = (s) => ({ manual: '手动上传', bms: 'BMS同步' }[s] || '历史')
const sourceType = (s) => ({ manual: 'info', bms: 'success' }[s] || 'info')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '')

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filter.data_type) params.data_type = filter.data_type
    if (filter.source) params.source = filter.source
    const res = await listUploadRecords(params)
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>
