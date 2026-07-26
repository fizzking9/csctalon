<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">分析视图</div>
      <div>
        <el-radio-group v-model="filterPreset" size="small" @change="loadData">
          <el-radio-button :label="null">全部</el-radio-button>
          <el-radio-button :label="true">预设</el-radio-button>
          <el-radio-button :label="false">自定义</el-radio-button>
        </el-radio-group>
        <el-button type="primary" :icon="Plus" style="margin-left: 12px" @click="goBuilder">新建视图</el-button>
      </div>
    </div>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag :type="chartTagType(row.chart_type)" size="small">{{ chartLabel(row.chart_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_preset ? 'warning' : 'success'" size="small">
              {{ row.is_preset ? '预设' : '自定义' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="片段数" width="80">
          <template #default="{ row }">
            <span>{{ row.segments?.length || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
              {{ row.status === 'active' ? '启用' : '归档' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="goRender(row)">查看</el-button>
            <el-button text type="primary" size="small" @click="goEdit(row)">编辑</el-button>
            <el-button v-if="!row.is_preset" text type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination
          v-model:current-page="page"
          :page-size="20"
          :total="total"
          layout="prev, pager, next"
          @current-change="loadData"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { listAnalysisViews, deleteAnalysisView } from '@/api/analysis-view'

const router = useRouter()
const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filterPreset = ref(null)

const chartLabels = { line: '折线', bar: '柱状', pie: '饼图', table: '表格' }
const chartLabel = (c) => chartLabels[c] || c
const chartTagType = (c) => ({ line: 'primary', bar: 'warning', pie: 'success', table: 'info' }[c] || '')

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: 20 }
    if (filterPreset.value !== null) params.preset = filterPreset.value
    const res = await listAnalysisViews(params)
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function goRender(row) {
  router.push(`/analytics/views/${row.id}`)
}

function goBuilder() {
  router.push('/analytics/builder')
}

function goEdit(row) {
  router.push(`/analytics/builder?edit=${row.id}`)
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除视图「${row.name}」?`, '提示', { type: 'warning' })
  await deleteAnalysisView(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(loadData)
</script>
