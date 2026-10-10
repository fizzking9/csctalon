<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">分类规则管理</div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建规则</el-button>
    </div>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="规则名称" min-width="160" />
        <el-table-column label="标签数" width="80">
          <template #default="{ row }">{{ row.rules?.length || 0 }}</template>
        </el-table-column>
        <el-table-column prop="default_label" label="默认标签" width="100" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button text type="warning" size="small" @click="openPreview(row)">预览</el-button>
            <el-button text type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑规则' : '新建规则'" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="90px">
        <el-form-item label="规则名称" required>
          <el-input v-model="form.name" placeholder="如:退款专项分类" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="规则说明" />
        </el-form-item>
        <el-form-item label="默认标签">
          <el-input v-model="form.default_label" placeholder="不匹配任何规则时的标签,如:其他" style="width: 200px" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" />
        </el-form-item>

        <el-divider>规则定义</el-divider>

        <div v-for="(rule, ri) in form.rules" :key="ri" class="rule-block">
          <div class="rule-header">
            <span class="rule-label">标签:</span>
            <el-input v-model="rule.label" size="small" placeholder="如:清退专项" style="width: 160px" />
            <span class="rule-label">匹配:</span>
            <el-select v-model="rule.match" size="small" style="width: 100px">
              <el-option label="任一(OR)" value="any" />
              <el-option label="全部(AND)" value="all" />
            </el-select>
            <el-button text type="danger" size="small" :icon="Delete" @click="form.rules.splice(ri, 1)">删除标签</el-button>
          </div>

          <div v-for="(cond, ci) in rule.conditions" :key="ci" class="cond-row">
            <el-input v-model="cond.field" size="small" placeholder="字段名" style="width: 140px" />
            <el-select v-model="cond.op" size="small" style="width: 120px">
              <el-option label="等于" value="eq" />
              <el-option label="不等于" value="ne" />
              <el-option label="属于" value="in" />
              <el-option label="不属于" value="notin" />
              <el-option label="包含" value="contains" />
              <el-option label="不包含" value="not_contains" />
              <el-option label="大于" value="gt" />
              <el-option label="小于" value="lt" />
              <el-option label="为空" value="is_null" />
              <el-option label="非空" value="is_not_null" />
            </el-select>
            <el-input
              v-if="!['is_null', 'is_not_null'].includes(cond.op)"
              v-model="cond.valueModel"
              size="small"
              placeholder="值(in/notin 用逗号分隔)"
              style="flex: 1"
              @input="onCondValueInput(cond)"
            />
            <el-button text type="danger" size="small" :icon="Close" @click="rule.conditions.splice(ci, 1)" />
          </div>
          <el-button text type="primary" size="small" :icon="Plus" @click="addCondition(rule)">添加条件</el-button>
        </div>

        <el-button type="primary" plain :icon="Plus" @click="addRule">添加标签规则</el-button>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 预览对话框 -->
    <el-dialog v-model="previewVisible" title="规则预览(各维度值 → 标签映射)" width="600px">
      <div v-loading="previewing">
        <el-form :inline="true" size="small">
          <el-form-item label="数据源">
            <el-select v-model="previewForm.source_code" style="width: 160px">
              <el-option v-for="ds in dataSources" :key="ds.code" :label="ds.name_cn" :value="ds.code" />
            </el-select>
          </el-form-item>
          <el-form-item label="目标字段">
            <el-input v-model="previewForm.target_field" placeholder="如 operator" style="width: 140px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="doPreview">预览</el-button>
          </el-form-item>
        </el-form>
        <el-table v-if="previewItems.length" :data="previewItems" border size="small" max-height="400">
          <el-table-column prop="value" label="原始值" />
          <el-table-column prop="label" label="分类标签">
            <template #default="{ row }">
              <el-tag size="small">{{ row.label }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="填写数据源和字段后点击预览" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Close, Delete } from '@element-plus/icons-vue'
import {
  listClassificationRules,
  createClassificationRule,
  updateClassificationRule,
  deleteClassificationRule,
  previewClassificationRule,
} from '@/api/analysis-view'
import { listDataSources } from '@/api/data-source'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const dataSources = ref([])

const dialogVisible = ref(false)
const editing = ref(null)
const submitting = ref(false)
const form = reactive({ name: '', description: '', default_label: '其他', is_active: true, rules: [] })

const previewVisible = ref(false)
const previewing = ref(false)
const previewItems = ref([])
const previewForm = reactive({ source_code: '', target_field: '' })
const currentRuleId = ref(null)

async function loadData() {
  loading.value = true
  try {
    const res = await listClassificationRules({ page: page.value, page_size: 20 })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function loadDataSources() {
  const res = await listDataSources()
  dataSources.value = res.data
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', description: '', default_label: '其他', is_active: true, rules: [] })
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    description: row.description || '',
    default_label: row.default_label,
    is_active: row.is_active,
    rules: (row.rules || []).map((r) => ({
      label: r.label,
      match: r.match || 'any',
      conditions: (r.conditions || []).map((c) => ({
        field: c.field,
        op: c.op,
        value: c.value,
        valueModel: Array.isArray(c.value) ? c.value.join(',') : (c.value ?? ''),
      })),
    })),
  })
  dialogVisible.value = true
}

function addRule() {
  form.rules.push({ label: '', match: 'any', conditions: [] })
}

function addCondition(rule) {
  rule.conditions.push({ field: '', op: 'eq', value: null, valueModel: '' })
}

function onCondValueInput(cond) {
  if (['in', 'notin'].includes(cond.op)) {
    cond.value = cond.valueModel.split(',').map((s) => s.trim()).filter(Boolean)
  } else if (['gt', 'lt', 'gte', 'lte'].includes(cond.op)) {
    cond.value = cond.valueModel === '' ? null : Number(cond.valueModel)
  } else {
    cond.value = cond.valueModel
  }
}

function buildPayload() {
  return {
    name: form.name,
    description: form.description || null,
    default_label: form.default_label || '其他',
    is_active: form.is_active,
    rules: form.rules.map((r) => ({
      label: r.label,
      match: r.match,
      conditions: r.conditions
        .filter((c) => c.field)
        .map((c) => ({ field: c.field, op: c.op, value: c.value })),
    })),
  }
}

async function handleSubmit() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写规则名称')
    return
  }
  if (!form.rules.length) {
    ElMessage.warning('请至少添加一个标签规则')
    return
  }
  submitting.value = true
  try {
    const payload = buildPayload()
    if (editing.value) {
      await updateClassificationRule(editing.value.id, payload)
    } else {
      await createClassificationRule(payload)
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
  await ElMessageBox.confirm(`确认删除规则「${row.name}」?`, '提示', { type: 'warning' })
  await deleteClassificationRule(row.id)
  ElMessage.success('已删除')
  loadData()
}

function openPreview(row) {
  currentRuleId.value = row.id
  previewForm.source_code = dataSources.value[0]?.code || ''
  previewForm.target_field = ''
  previewItems.value = []
  previewVisible.value = true
}

async function doPreview() {
  if (!previewForm.source_code || !previewForm.target_field) {
    ElMessage.warning('请填写数据源和目标字段')
    return
  }
  previewing.value = true
  try {
    const res = await previewClassificationRule({
      rule_id: currentRuleId.value,
      source_code: previewForm.source_code,
      target_field: previewForm.target_field,
      limit: 100,
    })
    previewItems.value = res.data.items
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '预览失败')
  } finally {
    previewing.value = false
  }
}

onMounted(() => {
  loadData()
  loadDataSources()
})
</script>

<style scoped>
.rule-block {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 12px;
  background: #fafafa;
}
.rule-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.rule-label {
  font-size: 13px;
  color: #606266;
}
.cond-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
</style>
