<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">看板模板</div>
        <div class="page-sub">维护周报/专项报告/审计报告的可编辑看板框架 · 每种类型一个默认模板(一键生成用) · 预设模板不可删</div>
      </div>
      <div class="flex gap-8">
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建模板</el-button>
      </div>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.report_type" placeholder="类型" clearable style="width: 160px" @change="resetAndLoad">
          <el-option v-for="(label, k) in typeMap" :key="k" :label="label" :value="k" />
        </el-select>
        <el-input v-model="filter.keyword" placeholder="名称搜索" clearable style="width: 220px" @keyup.enter="resetAndLoad" @clear="resetAndLoad" />
        <el-button :icon="Search" @click="resetAndLoad">搜索</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="name" label="模板名" show-overflow-tooltip min-width="180" />
        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ typeMap[row.report_type] || row.report_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分项数" width="80" align="center">
          <template #default="{ row }">{{ (row.framework || []).length }}</template>
        </el-table-column>
        <el-table-column label="标识" width="160">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" size="small" type="success">默认</el-tag>
            <el-tag v-if="row.is_preset" size="small" type="warning" style="margin-left: 4px">预设</el-tag>
            <el-tag v-if="row.status === 'archived'" size="small" type="info" style="margin-left: 4px">已归档</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="说明" show-overflow-tooltip min-width="180" />
        <el-table-column prop="created_by" label="创建人" width="100" />
        <el-table-column label="操作" width="280" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="handleCopy(row)">复制</el-button>
            <el-button v-if="!row.is_default && row.status === 'active'" link type="success" @click="handleSetDefault(row)">设为默认</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 编辑/新建对话框 -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="900px" top="5vh" :close-on-click-modal="false">
      <el-form :model="form" label-width="100px">
        <el-form-item label="模板名" required>
          <el-input v-model="form.name" placeholder="如 周报默认模板" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="form.report_type" style="width: 240px" :disabled="editingId !== null && form.is_preset">
            <el-option v-for="(label, k) in typeMap" :key="k" :label="label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="模板用途说明" />
        </el-form-item>
        <el-form-item label="可见性">
          <el-select v-model="form.visibility" style="width: 240px">
            <el-option label="管理层(manager)" value="manager" />
            <el-option label="全员(public)" value="public" />
            <el-option label="管理员(admin)" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="分项框架">
          <FrameworkEditor
            v-model="form.framework"
            mode="template"
            :view-options="viewSelectOptions"
            :user-options="userSelectOptions"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import {
  listReportTemplates,
  createReportTemplate,
  updateReportTemplate,
  deleteReportTemplate,
  copyReportTemplate,
  setDefaultTemplate,
} from '@/api/report-template'
import { listAnalysisViews } from '@/api/analysis-view'
import { listUsers } from '@/api/user'
import FrameworkEditor from '@/components/FrameworkEditor.vue'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ report_type: '', keyword: '' })
const viewOptions = ref([])
const userOptions = ref([])

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const form = reactive({
  name: '',
  report_type: 'weekly',
  description: '',
  visibility: 'manager',
  framework: [],
  is_preset: false,
})

const typeMap = { weekly: '周报', special_audit: '专项审计', custom: '自定义' }
const dialogTitle = computed(() => (editingId.value ? '编辑模板' : '新建模板'))

const viewSelectOptions = computed(() => viewOptions.value.map((v) => ({ value: v.id, label: v.name })))
const userSelectOptions = computed(() => userOptions.value.map((u) => ({ value: u.id, label: u.display_name })))

async function loadData() {
  loading.value = true
  try {
    const res = await listReportTemplates({
      page: page.value,
      page_size: 20,
      report_type: filter.report_type || undefined,
      keyword: filter.keyword || undefined,
    })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  const [viewRes, userRes] = await Promise.all([
    listAnalysisViews({ page: 1, page_size: 100 }),
    listUsers({ page: 1, page_size: 100 }),
  ])
  viewOptions.value = viewRes.data.items || []
  userOptions.value = userRes.data.items || []
}

function resetAndLoad() {
  page.value = 1
  loadData()
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    name: '',
    report_type: 'weekly',
    description: '',
    visibility: 'manager',
    framework: [],
    is_preset: false,
  })
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    report_type: row.report_type,
    description: row.description || '',
    visibility: row.visibility || 'manager',
    framework: JSON.parse(JSON.stringify(row.framework || [])),
    is_preset: row.is_preset,
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入模板名')
    return
  }
  if (!form.framework.length) {
    ElMessage.warning('框架至少含一个分项')
    return
  }
  submitting.value = true
  try {
    const payload = {
      name: form.name,
      report_type: form.report_type,
      description: form.description || null,
      visibility: form.visibility,
      framework: form.framework,
    }
    if (editingId.value) {
      await updateReportTemplate(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      await createReportTemplate(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function handleCopy(row) {
  try {
    await ElMessageBox.confirm(`复制模板[${row.name}]为自定义模板?`, '复制模板', { type: 'info' })
  } catch {
    return
  }
  try {
    await copyReportTemplate(row.id, { name: `${row.name} 副本` })
    ElMessage.success('已复制')
    loadData()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '复制失败')
  }
}

async function handleSetDefault(row) {
  try {
    await ElMessageBox.confirm(`将[${row.name}]设为${typeMap[row.report_type]}类型的默认模板?(同类型其他模板取消默认)`, '设为默认', { type: 'info' })
  } catch {
    return
  }
  try {
    await setDefaultTemplate(row.id)
    ElMessage.success('已设为默认')
    loadData()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '设置失败')
  }
}

async function handleDelete(row) {
  const action = row.is_preset ? '归档' : '删除'
  try {
    await ElMessageBox.confirm(`确认${action}模板[${row.name}]?${row.is_preset ? '(预设模板仅归档不硬删)' : ''}`, action, { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteReportTemplate(row.id)
    ElMessage.success(`已${action}`)
    loadData()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || `${action}失败`)
  }
}

onMounted(() => {
  loadData()
  loadOptions()
})
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
