<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">数据源管理</div>
      <el-button type="primary" :icon="Refresh" :loading="refreshing" @click="handleRefresh">重建元数据</el-button>
    </div>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="code" label="代码" width="140" />
        <el-table-column prop="name_cn" label="中文名" width="140" />
        <el-table-column prop="table_name" label="物理表名" width="180" />
        <el-table-column label="主键策略" width="120">
          <template #default="{ row }">
            <el-tag :type="row.pk_strategy === 'primary_key' ? 'success' : 'info'" size="small">
              {{ row.pk_strategy === 'primary_key' ? row.pk_field : '无主键' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="time_field" label="时间字段" width="140" />
        <el-table-column prop="sn_field" label="SN字段" width="120" />
        <el-table-column prop="field_count" label="字段数" width="80" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
              {{ row.status === 'active' ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="openDetail(row)">查看字段</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 字段详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="`${currentSource?.name_cn} - 字段元数据`" size="70%">
      <el-table v-if="fields.length" :data="fields" size="small" border max-height="600">
        <el-table-column prop="sort_order" label="#" width="50" />
        <el-table-column prop="field_name" label="字段名" width="180" />
        <el-table-column prop="field_name_cn" label="中文名" width="140" show-overflow-tooltip />
        <el-table-column prop="data_type" label="类型" width="100" />
        <el-table-column label="语义角色" width="100">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.semantic_role)" size="small">{{ roleLabel(row.semantic_role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="agg_func" label="聚合" width="80" />
        <el-table-column label="PK" width="50">
          <template #default="{ row }">
            <el-icon v-if="row.is_pk" color="#f56c6c"><Star /></el-icon>
          </template>
        </el-table-column>
        <el-table-column label="可筛选" width="70">
          <template #default="{ row }">
            <el-icon v-if="row.is_filterable" color="#67c23a"><Check /></el-icon>
          </template>
        </el-table-column>
        <el-table-column label="枚举值" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.enum_values">{{ row.enum_values.join(' / ') }}</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无字段数据" />
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Star, Check } from '@element-plus/icons-vue'
import { listDataSources, listDataSourceFields, refreshDataSources } from '@/api/data-source'

const list = ref([])
const loading = ref(false)
const refreshing = ref(false)
const detailVisible = ref(false)
const currentSource = ref(null)
const fields = ref([])

const roleLabels = {
  dimension: '维度',
  measure: '度量',
  amount: '金额',
  time: '时间',
  person: '人员',
  sn: 'SN',
  category: '类别',
}
const roleLabel = (r) => roleLabels[r] || r
const roleTagType = (r) => ({
  amount: 'danger',
  measure: 'warning',
  time: 'primary',
  person: 'success',
  sn: 'info',
  category: '',
  dimension: 'info',
}[r] || '')

async function loadData() {
  loading.value = true
  try {
    const res = await listDataSources()
    list.value = res.data
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  currentSource.value = row
  detailVisible.value = true
  fields.value = []
  const res = await listDataSourceFields(row.code)
  fields.value = res.data
}

async function handleRefresh() {
  refreshing.value = true
  try {
    const res = await refreshDataSources()
    ElMessage.success(`重建完成: ${res.data.sources} 个数据源, ${res.data.fields} 个字段`)
    loadData()
  } finally {
    refreshing.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.text-muted {
  color: #c0c4cc;
}
</style>
