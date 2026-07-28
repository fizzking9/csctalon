<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">看板配置</div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建看板</el-button>
    </div>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="看板名称" min-width="140" />
        <el-table-column label="代码" min-width="180">
          <template #default="{ row }">
            <el-tag v-if="row.code" size="small">{{ row.code }}</el-tag>
            <span v-else class="muted">自定义</span>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="90" />
        <el-table-column label="视图数" width="70">
          <template #default="{ row }">{{ row.items?.length || 0 }}</template>
        </el-table-column>
        <el-table-column label="可见性" width="90">
          <template #default="{ row }">{{ visibilityLabel(row.visibility) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="70">
          <template #default="{ row }">
            <el-tag :type="row.is_preset ? 'warning' : 'success'" size="small">
              {{ row.is_preset ? '预设' : '自定义' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button text type="danger" size="small" :disabled="row.is_preset" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 个</span>
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑看板' : '新建看板'" width="960px" :close-on-click-modal="false">
      <el-form :model="form" label-width="90px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="名称" required>
              <el-input v-model="form.name" placeholder="如:退款专项跟进看板" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="代码">
              <el-input v-model="form.code" placeholder="自定义看板可空" :disabled="isPreset" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="分类">
              <el-select v-model="form.category" :disabled="isPreset" style="width: 100%">
                <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="可见性">
              <el-radio-group v-model="form.visibility">
                <el-radio-button label="public">全员</el-radio-button>
                <el-radio-button label="manager">管理层</el-radio-button>
                <el-radio-button label="admin">管理员</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="默认天数">
              <el-input-number v-model="form.time_range_days" :min="1" :max="365" style="width: 140px" />
              <span class="hint">空=各视图自带</span>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="看板说明" />
        </el-form-item>

        <el-divider>视图组合</el-divider>

        <el-table :data="form.items" size="small" border>
          <el-table-column label="顺序" width="90">
            <template #default="{ $index }">
              <el-button text :icon="Top" size="small" :disabled="$index === 0" @click="move($index, -1)" />
              <el-button text :icon="Bottom" size="small" :disabled="$index === form.items.length - 1" @click="move($index, 1)" />
            </template>
          </el-table-column>
          <el-table-column label="视图" min-width="200">
            <template #default="{ row }">
              <el-tag size="small">{{ row.view_code }}</el-tag>
              <span style="margin-left: 6px">{{ row.view_name || row.title }}</span>
            </template>
          </el-table-column>
          <el-table-column label="标题覆盖" width="200">
            <template #default="{ row }">
              <el-input v-model="row.title" size="small" placeholder="空=用视图名" />
            </template>
          </el-table-column>
          <el-table-column label="宽度span" width="130">
            <template #default="{ row }">
              <el-input-number v-model="row.span" :min="1" :max="24" size="small" controls-position="right" style="width: 110px" />
            </template>
          </el-table-column>
          <el-table-column label="可见" width="70">
            <template #default="{ row }">
              <el-switch v-model="row.visible" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="70">
            <template #default="{ $index }">
              <el-button text type="danger" size="small" :icon="Delete" @click="form.items.splice($index, 1)" />
            </template>
          </el-table-column>
        </el-table>

        <el-button type="primary" plain :icon="Plus" class="mt-16" @click="openPicker">添加视图</el-button>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 视图选择对话框 -->
    <el-dialog v-model="pickerVisible" title="选择分析视图" width="720px">
      <el-table
        ref="pickerTable"
        :data="availableViews"
        size="small"
        max-height="420"
        @selection-change="onPickChange"
      >
        <el-table-column type="selection" width="45" />
        <el-table-column prop="code" label="代码" width="200" />
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="chart_type" label="类型" width="80" />
        <el-table-column label="预设" width="60">
          <template #default="{ row }">
            <el-tag v-if="row.is_preset" type="warning" size="small">是</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="pickerVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmPick">加入看板</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Top, Bottom, Delete } from '@element-plus/icons-vue'
import { listDashboards, getDashboard, createDashboard, updateDashboard, deleteDashboard } from '@/api/dashboard'
import { listAnalysisViews } from '@/api/analysis-view'

const CATEGORIES = ['退款', '投诉', '接待', '绩效', '专项', '自定义']

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)

const dialogVisible = ref(false)
const editing = ref(null)
const isPreset = ref(false)
const submitting = ref(false)
const form = reactive({
  code: '',
  name: '',
  description: '',
  category: '自定义',
  time_range_days: 30,
  visibility: 'manager',
  items: [],
})

const pickerVisible = ref(false)
const availableViews = ref([])
const pickedViews = ref([])

function visibilityLabel(v) {
  return { public: '全员', manager: '管理层', admin: '管理员' }[v] || v
}

async function loadData() {
  loading.value = true
  try {
    const res = await listDashboards({ page: page.value, page_size: 20 })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    code: '',
    name: '',
    description: '',
    category: '自定义',
    time_range_days: 30,
    visibility: 'manager',
    items: [],
  })
}

function openCreate() {
  editing.value = null
  isPreset.value = false
  resetForm()
  dialogVisible.value = true
}

async function openEdit(row) {
  editing.value = row
  isPreset.value = row.is_preset
  // 取详情拿完整 items
  const res = await getDashboard(row.id)
  const d = res.data
  Object.assign(form, {
    code: d.code || '',
    name: d.name,
    description: d.description || '',
    category: d.category,
    time_range_days: d.time_range_default?.days ?? 30,
    visibility: d.visibility,
    items: (d.items || []).map((it) => ({
      view_id: it.view_id,
      view_code: it.view_code,
      view_name: it.view_code, // 列表展示用,后续可从 availableViews 补全名称
      title: it.title || '',
      span: it.span ?? 12,
      visible: it.visible !== false,
    })),
  })
  // 用 availableViews 补全 view_name
  await loadAvailableViews()
  form.items.forEach((it) => {
    const v = availableViews.value.find((x) => x.id === it.view_id)
    if (v) it.view_name = v.name
  })
  dialogVisible.value = true
}

function move(idx, dir) {
  const arr = form.items
  const j = idx + dir
  ;[arr[idx], arr[j]] = [arr[j], arr[idx]]
}

async function loadAvailableViews() {
  const res = await listAnalysisViews({ page: 1, page_size: 100 })
  availableViews.value = res.data.items
}

async function openPicker() {
  if (!availableViews.value.length) await loadAvailableViews()
  pickedViews.value = []
  pickerVisible.value = true
}

function onPickChange(rows) {
  pickedViews.value = rows
}

function confirmPick() {
  for (const v of pickedViews.value) {
    // 去重:已存在的 view_id 不重复加
    if (form.items.some((it) => it.view_id === v.id)) continue
    form.items.push({
      view_id: v.id,
      view_code: v.code || '',
      view_name: v.name,
      title: '',
      span: 12,
      visible: true,
    })
  }
  pickerVisible.value = false
}

function buildPayload() {
  const items = form.items.map((it) => ({
    view_id: it.view_id,
    view_code: it.view_code,
    title: it.title || null,
    span: it.span,
    visible: it.visible,
  }))
  return {
    name: form.name,
    description: form.description || null,
    category: form.category,
    items,
    time_range_default: form.time_range_days ? { days: form.time_range_days } : null,
    visibility: form.visibility,
  }
}

async function handleSubmit() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写看板名称')
    return
  }
  if (!form.items.length) {
    ElMessage.warning('请至少添加一个视图')
    return
  }
  submitting.value = true
  try {
    const payload = buildPayload()
    if (editing.value) {
      await updateDashboard(editing.value.id, payload)
    } else {
      await createDashboard(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    loadData()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除看板「${row.name}」?`, '提示', { type: 'warning' })
  await deleteDashboard(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.muted {
  color: #c0c4cc;
  font-size: 12px;
}
.hint {
  font-size: 12px;
  color: #909399;
  margin-left: 8px;
}
</style>
