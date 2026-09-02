<template>
  <div class="data-browser">
    <div class="flex-row">
      <!-- 左侧数据源列表 -->
      <div class="source-sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">数据源</span>
          <span class="sidebar-count">{{ activeSourceCount }} 个</span>
        </div>
        <el-menu
          :default-active="currentSource?.code"
          :default-openeds="openedCategories"
          class="source-menu"
          @select="handleSourceSelect"
        >
          <el-sub-menu
            v-for="grp in groupedSources"
            :key="grp.category"
            :index="grp.category"
          >
            <template #title>
              <span class="group-title">{{ grp.category }}</span>
              <span class="group-count">{{ grp.items.length }}</span>
            </template>
            <el-menu-item
              v-for="src in grp.items"
              :key="src.code"
              :index="src.code"
            >
              <span>{{ src.name_cn }}</span>
              <el-tag
                v-if="src.status === 'placeholder'"
                size="small"
                type="info"
                effect="plain"
                class="placeholder-tag"
              >规划中</el-tag>
              <el-badge
                v-else-if="src.table_name"
                :value="getTableCount(src.code)"
                class="count-badge"
              />
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </div>

      <!-- 右侧主区域 -->
      <div v-if="currentSource" class="main-area">
        <!-- 占位数据源:规划中,暂未实现 -->
        <div v-if="isPlaceholder" class="placeholder-area">
          <el-empty :image-size="140" description="该数据源正在规划中，暂未实现">
            <template #image><el-icon size="80" color="#c0c4cc"><DataAnalysis /></el-icon></template>
            <p class="placeholder-hint">{{ currentSource.name_cn }}（{{ currentSource.code }}）尚未接入数据</p>
          </el-empty>
        </div>

        <!-- 数据汇总卡片 -->
        <div v-if="summary" class="summary-cards">
          <div class="summary-item">
            <span class="summary-label">总记录数</span>
            <span class="summary-value">{{ summary.total_count.toLocaleString() }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">时间范围</span>
            <span class="summary-value" style="font-size: 13px;">
              {{ fmtDate(summary.time_range?.min) }} ~ {{ fmtDate(summary.time_range?.max) }}
            </span>
          </div>
          <div class="summary-item">
            <span class="summary-label">设备SN数</span>
            <span class="summary-value">{{ summary.sn_stats?.unique_count?.toLocaleString() || 0 }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">导入批次</span>
            <span class="summary-value">{{ uploadRecords.length }}</span>
          </div>
        </div>

        <!-- 导入记录列表 -->
        <div v-if="uploadRecords.length" class="upload-section">
          <div class="section-header">
            <el-icon><Upload /></el-icon>
            <span class="section-title">导入记录</span>
          </div>
          <el-table :data="uploadRecords" size="small" border stripe :max-height="180">
            <el-table-column prop="filename" label="文件名" width="200" show-overflow-tooltip />
            <el-table-column prop="total_rows" label="总行数" width="80" />
            <el-table-column prop="inserted_rows" label="新增" width="70" />
            <el-table-column prop="updated_rows" label="更新" width="70" />
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.status === 'imported' ? 'success' : (row.status === 'failed' ? 'danger' : 'warning')" size="small">
                  {{ row.status === 'imported' ? '已导入' : (row.status === 'failed' ? '失败' : '处理中') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="导入时间" width="160">
              <template #default="{ row }">{{ fmtDateTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 表格区域 -->
        <div v-if="!isPlaceholder" class="table-area">
          <div class="table-header">
            <div class="header-info">
              <el-icon><Database /></el-icon>
              <span class="source-name">{{ currentSource.name_cn }}</span>
              <el-tag type="info" size="small">{{ currentSource.code }}</el-tag>
            </div>
            <div class="header-actions">
              <el-button
                type="primary"
                size="small"
                :loading="exporting"
                :disabled="!tableData.length"
                @click="handleExport"
              >
                <el-icon><Download /></el-icon> 导出 Excel
              </el-button>
              <el-button text type="primary" size="small" @click="refreshData">
                <el-icon><Refresh /></el-icon> 刷新
              </el-button>
            </div>
          </div>

          <!-- 数据表格 -->
          <div class="table-container">
            <el-table
              :data="tableData"
              :columns="tableColumns"
              border
              stripe
              size="small"
              height="100%"
              @sort-change="handleSortChange"
            >
              <el-table-column
                v-for="col in tableColumns"
                :key="col.prop"
                :prop="col.prop"
                :label="col.label"
                :width="col.width"
                :sortable="col.sortable"
                show-overflow-tooltip
              >
                <template #default="{ row }">{{ fmtCell(row[col.prop], col.dataType) }}</template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 分页 -->
          <div class="pagination-bar">
            <span class="total-info">共 {{ pagination.total }} 条记录</span>
            <el-pagination
              v-model:current-page="pagination.page"
              v-model:page-size="pagination.pageSize"
              :total="pagination.total"
              :page-sizes="[10, 20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              size="small"
              @size-change="handlePageSizeChange"
              @current-change="handlePageChange"
            />
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-else class="empty-area">
        <el-empty description="请选择一个数据源查看数据" :image-size="120">
          <template #image><el-icon size="64"><Database /></el-icon></template>
        </el-empty>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import request from '@/api'

const sources = ref([])
const currentSource = ref(null)
const currentFields = ref([])
const tableData = ref([])
const pagination = ref({ page: 1, pageSize: 20, total: 0 })
const filters = ref({})
const searchKeyword = ref('')
const sortField = ref('')
const sortDir = ref('desc')
const tableCounts = ref({})
const summary = ref(null)
const uploadRecords = ref([])
const exporting = ref(false)

// 数据源业务分组顺序(与后端 DATA_SOURCE_SEEDS 的 category 对应)
const CATEGORY_ORDER = ['现金流', '工单与客服', '投诉登记', '微信聚合客服', '基础数据']

// 按 category 分组,无 category 的归入"其他"
const groupedSources = computed(() => {
  const groups = {}
  const others = []
  for (const src of sources.value) {
    if (src.category && CATEGORY_ORDER.includes(src.category)) {
      ;(groups[src.category] ||= []).push(src)
    } else {
      others.push(src)
    }
  }
  const result = CATEGORY_ORDER
    .map((cat) => ({ category: cat, items: groups[cat] || [] }))
    .filter((g) => g.items.length)
  if (others.length) result.push({ category: '其他', items: others })
  return result
})

// 默认展开全部分组
const openedCategories = computed(() => groupedSources.value.map((g) => g.category))

// 当前是否为占位数据源(规划中,无实际表)
const isPlaceholder = computed(() => currentSource.value?.status === 'placeholder')

// 已实现数据源数量(排除占位项)
const activeSourceCount = computed(() => sources.value.filter((s) => s.status !== 'placeholder').length)

async function loadSources() {
  try {
    const res = await request.get('/data-sources', { params: { include_placeholder: true } })
    sources.value = res.data || []
    if (sources.value.length) {
      handleSourceSelect(sources.value[0].code)
    }
  } catch (e) {
    console.error('加载数据源失败', e)
  }
}

async function loadFields(code) {
  try {
    const res = await request.get(`/data-sources/${code}/fields`)
    currentFields.value = res.data || []
  } catch (e) {
    console.error('加载字段失败', e)
  }
}

async function loadSummary(code) {
  try {
    const res = await request.get(`/data-sources/${code}/summary`)
    summary.value = res.data || null
  } catch (e) {
    console.error('加载汇总失败', e)
    summary.value = null
  }
}

async function loadUploadRecords(code) {
  try {
    const res = await request.get(`/uploads/records?data_type=${code}&page=1&page_size=100`)
    uploadRecords.value = res.data?.items || []
  } catch (e) {
    console.error('加载导入记录失败', e)
    uploadRecords.value = []
  }
}

async function loadData() {
  if (!currentSource.value) return
  try {
    const params = {
      page: pagination.value.page,
      page_size: pagination.value.pageSize,
    }
    if (sortField.value) {
      params.sort_field = sortField.value
      params.sort_dir = sortDir.value
    }
    const filterObj = {}
    for (const [key, value] of Object.entries(filters.value)) {
      if (value) filterObj[key] = value
    }
    if (searchKeyword.value) {
      const searchFields = currentFields.value
        .filter((f) => f.is_filterable && f.data_type === 'string')
        .slice(0, 3)
      searchFields.forEach((f) => {
        filterObj[`${f.field_name}__contains`] = searchKeyword.value
      })
    }
    if (Object.keys(filterObj).length) {
      params.filters = JSON.stringify(filterObj)
    }
    const res = await request.get(`/data-sources/${currentSource.value.code}/records`, { params })
    tableData.value = res.data.items || []
    pagination.value.total = res.data.total || 0
    tableCounts.value[currentSource.value.code] = res.data.total || 0
  } catch (e) {
    console.error('加载数据失败', e)
  }
}

function handleSourceSelect(code) {
  const src = sources.value.find((s) => s.code === code)
  if (src) {
    currentSource.value = src
    filters.value = {}
    searchKeyword.value = ''
    sortField.value = ''
    pagination.value.page = 1
    // 占位数据源无实际表,不加载字段/汇总/记录/明细
    if (src.status === 'placeholder') return
    loadFields(code)
    loadSummary(code)
    loadUploadRecords(code)
    loadData()
  }
}

const filterableFields = computed(() => {
  return currentFields.value.filter((f) => f.is_filterable)
})

function getFieldOptions(field) {
  if (field.enum_values) {
    return field.enum_values.map((v) => ({ value: v, label: v }))
  }
  return []
}

const tableColumns = computed(() => {
  return currentFields.value.map((f) => {
    let width = null
    if (f.data_type === 'timestamp') width = 170
    if (f.data_type === 'numeric') width = 120
    if (f.semantic_role === 'sn') width = 180
    return {
      prop: f.field_name,
      label: f.field_name_cn || f.field_name,
      width,
      sortable: f.is_filterable && f.data_type !== 'jsonb',
      dataType: f.data_type,
    }
  })
})

function fmtCell(value, dataType) {
  if (value == null) return '-'
  if (dataType === 'numeric') {
    if (typeof value === 'number') return value.toFixed(2)
    return value
  }
  if (dataType === 'timestamp') {
    // 用 dayjs 解析,自动处理 UTC(带 Z/+00:00)转本地时区
    const d = dayjs(value)
    return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(value)
  }
  if (dataType === 'jsonb') {
    return JSON.stringify(value)
  }
  return value
}

function fmtDate(dateStr) {
  if (!dateStr) return '-'
  const d = dayjs(dateStr)
  return d.isValid() ? d.format('YYYY-MM-DD') : dateStr.substring(0, 10)
}

function fmtDateTime(dateStr) {
  if (!dateStr) return '-'
  const d = dayjs(dateStr)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(dateStr)
}

function getTableCount(code) {
  return tableCounts.value[code] || 0
}

function handleFilterChange() {
  pagination.value.page = 1
  loadData()
}

function handleSearch() {
  pagination.value.page = 1
  loadData()
}

function handleSortChange({ prop, order }) {
  sortField.value = prop
  sortDir.value = order === 'ascending' ? 'asc' : 'desc'
  loadData()
}

function handlePageSizeChange(size) {
  pagination.value.pageSize = size
  pagination.value.page = 1
  loadData()
}

function handlePageChange(page) {
  pagination.value.page = page
  loadData()
}

function refreshData() {
  loadSummary(currentSource.value?.code)
  loadUploadRecords(currentSource.value?.code)
  loadData()
}

async function handleExport() {
  if (!currentSource.value || isPlaceholder.value) return
  exporting.value = true
  try {
    // 复用 loadData 的筛选/排序参数(去掉分页,导出全部匹配行)
    const params = {}
    if (sortField.value) {
      params.sort_field = sortField.value
      params.sort_dir = sortDir.value
    }
    const filterObj = {}
    for (const [key, value] of Object.entries(filters.value)) {
      if (value) filterObj[key] = value
    }
    if (searchKeyword.value) {
      const searchFields = currentFields.value
        .filter((f) => f.is_filterable && f.data_type === 'string')
        .slice(0, 3)
      searchFields.forEach((f) => {
        filterObj[`${f.field_name}__contains`] = searchKeyword.value
      })
    }
    if (Object.keys(filterObj).length) {
      params.filters = JSON.stringify(filterObj)
    }

    const res = await request.get(`/data-sources/${currentSource.value.code}/export`, {
      params,
      responseType: 'blob',
      timeout: 120000,
    })
    // axios 响应拦截器对 blob 成功响应返回 response.data(即 Blob);兼容返回完整 response 的情况
    const blobData = res instanceof Blob ? res : res?.data
    const blob = new Blob([blobData], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const ts = dayjs().format('YYYYMMDD_HHmmss')
    const fname = `${currentSource.value.name_cn}_导出_${ts}.xlsx`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fname
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    ElMessage.success('已导出')
  } catch (e) {
    // 后端错误以 blob 形式返回时,读取 blob 文本提取 detail(如"超过导出上限")
    let msg = '导出失败'
    const errBlob = e?.response?.data
    if (errBlob instanceof Blob) {
      try {
        const text = await errBlob.text()
        const j = JSON.parse(text)
        msg = j?.detail || j?.message || msg
      } catch {
        // blob 非 JSON,忽略
      }
    } else {
      msg = e?.response?.data?.detail || e?.message || msg
    }
    ElMessage.error(msg)
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  loadSources()
})
</script>

<style scoped>
.data-browser {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.flex-row {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.source-sidebar {
  width: 260px;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  background: #fafafa;
}
.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e4e7ed;
}
.sidebar-title { font-size: 16px; font-weight: 600; }
.sidebar-count { font-size: 12px; color: #909399; }
.source-menu { flex: 1; border-right: none; }
.count-badge { margin-left: 8px; }
.group-title { font-size: 13px; font-weight: 600; color: #303133; }
.group-count {
  margin-left: 6px;
  font-size: 12px;
  color: #909399;
}
.source-menu :deep(.el-sub-menu__title) {
  height: 40px;
  line-height: 40px;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.summary-cards {
  display: flex;
  gap: 24px;
  padding: 8px 20px;
  background: #fafafa;
  border-bottom: 1px solid #e4e7ed;
}
.summary-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.summary-label {
  font-size: 12px;
  color: #909399;
}
.summary-value {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.upload-section {
  padding: 8px 20px 12px;
  border-bottom: 1px solid #f0f0f0;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #606266;
}
.section-title { font-size: 13px; font-weight: 500; }

.table-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.table-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  border-bottom: 1px solid #e4e7ed;
}
.header-info { display: flex; align-items: center; gap: 8px; }
.source-name { font-size: 14px; font-weight: 600; }
.table-container { flex: 1; overflow: auto; padding: 12px 20px; }
.pagination-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-top: 1px solid #e4e7ed;
}
.total-info { font-size: 13px; color: #606266; }
.empty-area { flex: 1; display: flex; align-items: center; justify-content: center; }
.placeholder-area {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
}
.placeholder-hint {
  margin-top: 12px;
  font-size: 13px;
  color: #909399;
}
.placeholder-tag { margin-left: 6px; }
</style>
