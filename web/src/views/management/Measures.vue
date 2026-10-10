<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">管理措施</div>
        <div class="page-sub">审计/周报输出物 · 带「预期目标+验证时间+验证方式」· 到期回看形成 PDCA 闭环</div>
      </div>
      <div class="flex gap-8">
        <el-button type="warning" :icon="AlarmClock" @click="goDueReview">到期待回看 ({{ dueCount }})</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建措施</el-button>
      </div>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 150px" @change="resetAndLoad">
          <el-option v-for="(label, k) in statusMap" :key="k" :label="label" :value="k" />
        </el-select>
        <el-select v-model="filter.source" placeholder="来源" clearable style="width: 140px" @change="resetAndLoad">
          <el-option v-for="(label, k) in sourceMap" :key="k" :label="label" :value="k" />
        </el-select>
        <el-select v-model="filter.measure_type" placeholder="类型" clearable style="width: 140px" @change="resetAndLoad">
          <el-option label="内部管理" value="internal" />
          <el-option label="外部协调" value="external" />
        </el-select>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe>
        <el-table-column prop="title" label="措施标题" show-overflow-tooltip min-width="180" />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ typeMap[row.measure_type] || row.measure_type }}</template>
        </el-table-column>
        <el-table-column label="来源" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ sourceMap[row.source] || row.source }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusMap[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="到期日" width="130">
          <template #default="{ row }">
            <span :class="{ 'text-danger': isOverdue(row) }">{{ formatDate(row.verify_time) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="回看结论" width="100">
          <template #default="{ row }">{{ row.review_result ? reviewResultMap[row.review_result] || row.review_result : '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'executing' || row.status === 'pending_review'"
              link
              type="primary"
              @click="openReview(row)"
            >到期回看</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 新建/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑措施' : '新建管理措施'" width="680px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="措施标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="类型">
              <el-select v-model="form.measure_type" style="width: 100%">
                <el-option label="内部管理" value="internal" />
                <el-option label="外部协调" value="external" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="来源">
              <el-select v-model="form.source" style="width: 100%">
                <el-option v-for="(label, k) in sourceMap" :key="k" :label="label" :value="k" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="预期目标">
          <el-input v-model="form.expected_goal" type="textarea" :rows="2" placeholder="量化或定性的预期目标" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="验证时间">
              <el-date-picker v-model="form.verify_time" type="datetime" placeholder="到期回看时间" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-select v-model="form.status" style="width: 100%">
                <el-option v-for="(label, k) in statusMap" :key="k" :label="label" :value="k" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="验证方式">
          <el-input v-model="form.verify_method" type="textarea" :rows="2" placeholder="看哪个视图/指标/抽查" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="生效起始">
              <el-date-picker v-model="form.valid_from" type="datetime" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="生效结束">
              <el-date-picker v-model="form.valid_to" type="datetime" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">{{ editing ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <!-- 到期回看对话框 -->
    <el-dialog v-model="reviewVisible" title="措施到期回看" width="560px">
      <el-descriptions v-if="current" :column="1" border class="mb-16">
        <el-descriptions-item label="措施">{{ current.title }}</el-descriptions-item>
        <el-descriptions-item label="预期目标">{{ current.expected_goal || '-' }}</el-descriptions-item>
        <el-descriptions-item label="验证方式">{{ current.verify_method || '-' }}</el-descriptions-item>
        <el-descriptions-item label="到期日">{{ formatDate(current.verify_time) }}</el-descriptions-item>
      </el-descriptions>
      <el-form :model="reviewForm" label-width="100px">
        <el-form-item label="回看结论" required>
          <el-radio-group v-model="reviewForm.review_result">
            <el-radio value="effective">生效</el-radio>
            <el-radio value="partial">部分生效</el-radio>
            <el-radio value="ineffective">未生效</el-radio>
            <el-radio value="adjust">需调整</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="回看备注">
          <el-input v-model="reviewForm.review_note" type="textarea" :rows="4" placeholder="回看结论说明/后续动作" />
        </el-form-item>
      </el-form>
      <div class="page-sub mb-16">
        生效→已生效 · 部分生效→待回看(继续观察) · 未生效/需调整→未生效(转新措施或升级)
      </div>
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleReview">提交回看</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, AlarmClock } from '@element-plus/icons-vue'
import { listMeasures, listDueReviewMeasures, createMeasure, updateMeasure, reviewMeasure, deleteMeasure } from '@/api/measure'
import dayjs from 'dayjs'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const dueCount = ref(0)
const filter = reactive({ status: '', source: '', measure_type: '' })
const dialogVisible = ref(false)
const reviewVisible = ref(false)
const submitting = ref(false)
const editing = ref(false)
const current = ref(null)
const form = reactive(emptyForm())
const reviewForm = reactive({ review_result: 'effective', review_note: '' })

const statusMap = {
  draft: '草稿',
  executing: '执行中',
  pending_review: '待回看',
  effective: '已生效',
  ineffective: '未生效',
  archived: '已归档',
}
const statusType = (s) => ({ draft: 'info', executing: 'warning', pending_review: 'danger', effective: 'success', ineffective: 'info', archived: 'info' }[s] || 'info')
const sourceMap = { audit: '审计', weekly: '周报', incident: '影响事件', manual: '手工' }
const typeMap = { internal: '内部管理', external: '外部协调' }
const reviewResultMap = { effective: '生效', partial: '部分生效', ineffective: '未生效', adjust: '需调整' }
const formatDate = (t) => (t ? dayjs(t).format('YY/MM/DD') : '-')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const isOverdue = (row) => row.verify_time && row.status !== 'effective' && row.status !== 'archived' && dayjs(row.verify_time).isBefore(dayjs())

function emptyForm() {
  return { title: '', measure_type: 'internal', source: 'manual', expected_goal: '', verify_time: null, verify_method: '', status: 'draft', valid_from: null, valid_to: null }
}

async function loadData() {
  loading.value = true
  try {
    const res = await listMeasures({
      page: page.value,
      page_size: 20,
      status: filter.status || undefined,
      source: filter.source || undefined,
      measure_type: filter.measure_type || undefined,
    })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function loadDueCount() {
  try {
    const res = await listDueReviewMeasures({ page: 1, page_size: 1 })
    dueCount.value = res.data.total
  } catch (e) {
    dueCount.value = 0
  }
}

function resetAndLoad() {
  page.value = 1
  loadData()
}

function goDueReview() {
  filter.status = 'executing'
  resetAndLoad()
  ElMessage.info(`共 ${dueCount.value} 条到期待回看,已筛选执行中`)
}

function openCreate() {
  editing.value = false
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = true
  current.value = row
  Object.assign(form, {
    title: row.title,
    measure_type: row.measure_type,
    source: row.source,
    expected_goal: row.expected_goal || '',
    verify_time: row.verify_time,
    verify_method: row.verify_method || '',
    status: row.status,
    valid_from: row.valid_from,
    valid_to: row.valid_to,
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!form.title.trim()) {
    ElMessage.warning('请输入措施标题')
    return
  }
  submitting.value = true
  try {
    const payload = {
      title: form.title,
      measure_type: form.measure_type,
      source: form.source,
      expected_goal: form.expected_goal || null,
      verify_time: form.verify_time ? dayjs(form.verify_time).toISOString() : null,
      verify_method: form.verify_method || null,
      status: form.status,
      valid_from: form.valid_from ? dayjs(form.valid_from).toISOString() : null,
      valid_to: form.valid_to ? dayjs(form.valid_to).toISOString() : null,
    }
    if (editing.value) {
      await updateMeasure(current.value.id, payload)
      ElMessage.success('已更新')
    } else {
      await createMeasure(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    loadData()
    loadDueCount()
  } finally {
    submitting.value = false
  }
}

function openReview(row) {
  current.value = row
  reviewForm.review_result = 'effective'
  reviewForm.review_note = ''
  reviewVisible.value = true
}

async function handleReview() {
  submitting.value = true
  try {
    await reviewMeasure(current.value.id, {
      review_result: reviewForm.review_result,
      review_note: reviewForm.review_note || null,
    })
    ElMessage.success('回看已记录')
    reviewVisible.value = false
    loadData()
    loadDueCount()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确认删除该措施?', '提示', { type: 'warning' })
  await deleteMeasure(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(() => {
  loadData()
  loadDueCount()
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
