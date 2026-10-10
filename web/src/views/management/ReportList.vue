<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">报告索引</div>
        <div class="page-sub">报告是审计的前提(只有报告才能审计) · 确认后固化快照 · 支持审计附注与持续批注</div>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建报告</el-button>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.report_type" placeholder="类型" clearable style="width: 150px" @change="resetAndLoad">
          <el-option v-for="(label, k) in typeMap" :key="k" :label="label" :value="k" />
        </el-select>
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="resetAndLoad">
          <el-option v-for="(label, k) in statusMap" :key="k" :label="label" :value="k" />
        </el-select>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe style="cursor: pointer" @row-click="goDetail">
        <el-table-column prop="name" label="报告名" show-overflow-tooltip min-width="220" />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ typeMap[row.report_type] || row.report_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusMap[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间范围" width="220">
          <template #default="{ row }">{{ formatTime(row.time_start) }} ~ {{ formatTime(row.time_end) }}</template>
        </el-table-column>
        <el-table-column label="审计附注" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.audit_note_count ? 'warning' : 'info'" size="small">{{ row.audit_note_count || 0 }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="快照" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.has_snapshot ? 'success' : 'info'" size="small">{{ row.has_snapshot ? '已固化' : '未固化' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="confirmed_by" label="确认人" width="100" />
        <el-table-column label="确认时间" width="160">
          <template #default="{ row }">{{ formatTime(row.confirmed_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="goDetail(row)">查看</el-button>
            <el-button v-if="row.status === 'draft'" link type="danger" @click.stop="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 新建报告对话框 -->
    <el-dialog v-model="dialogVisible" title="新建报告(草稿)" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="报告名" required>
          <el-input v-model="form.name" placeholder="如 2026-W30 退款专项审计报告" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.report_type" style="width: 100%">
            <el-option v-for="(label, k) in typeMap" :key="k" :label="label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="form.range"
            type="datetimerange"
            range-separator="至"
            start-placeholder="锁定起"
            end-placeholder="锁定止"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="筛选条件">
          <el-input v-model="form.filtersText" type="textarea" :rows="2" placeholder="锁定的筛选条件(JSON,可选)" />
        </el-form-item>
        <el-form-item label="视图引用">
          <el-input v-model="form.viewIdsText" type="textarea" :rows="2" placeholder="包含的视图引用 [{view_id,title},...](JSON,可选)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listReports, createReport, deleteReport } from '@/api/report'
import dayjs from 'dayjs'

const router = useRouter()
const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ report_type: '', status: '' })
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ name: '', report_type: 'custom', range: null, filtersText: '', viewIdsText: '' })

const typeMap = { weekly: '周报', special_audit: '专项审计', custom: '自定义' }
const statusMap = { draft: '草稿', confirmed: '已确认', archived: '已归档' }
const statusType = (s) => ({ draft: 'info', confirmed: 'success', archived: 'warning' }[s] || 'info')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

async function loadData() {
  loading.value = true
  try {
    const res = await listReports({
      page: page.value,
      page_size: 20,
      report_type: filter.report_type || undefined,
      status: filter.status || undefined,
    })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function resetAndLoad() {
  page.value = 1
  loadData()
}

function goDetail(row) {
  router.push(`/management/reports/${row.id}`)
}

function openCreate() {
  Object.assign(form, { name: '', report_type: 'custom', range: null, filtersText: '', viewIdsText: '' })
  dialogVisible.value = true
}

async function handleCreate() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入报告名')
    return
  }
  let filters = null
  let viewIds = null
  try {
    if (form.filtersText.trim()) filters = JSON.parse(form.filtersText)
    if (form.viewIdsText.trim()) viewIds = JSON.parse(form.viewIdsText)
  } catch (e) {
    ElMessage.error('筛选条件/视图引用 JSON 格式错误')
    return
  }
  submitting.value = true
  try {
    await createReport({
      name: form.name,
      report_type: form.report_type,
      time_start: form.range && form.range[0] ? form.range[0] : null,
      time_end: form.range && form.range[1] ? form.range[1] : null,
      filters,
      view_ids: viewIds,
    })
    ElMessage.success('创建成功')
    dialogVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确认删除该报告?(关联的审计附注将一并删除,管理措施保留仅断开关联)', '提示', { type: 'warning' })
  await deleteReport(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.mt-16 {
  margin-top: 16px;
}
.mb-16 {
  margin-bottom: 16px;
}
</style>
