<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">事项跟进</div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建待办</el-button>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="loadData">
          <el-option label="待办" value="pending" />
          <el-option label="处理中" value="processing" />
          <el-option label="待确认" value="confirming" />
          <el-option label="已完成" value="done" />
        </el-select>
        <el-select v-model="filter.priority" placeholder="优先级" clearable style="width: 140px" @change="loadData">
          <el-option label="紧急" value="urgent" />
          <el-option label="高" value="high" />
          <el-option label="中" value="medium" />
          <el-option label="低" value="low" />
        </el-select>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe style="cursor: pointer" @row-click="goDetail">
        <el-table-column label="优先级" width="90">
          <template #default="{ row }">
            <el-tag :type="priorityType(row.priority)" size="small">{{ priorityLabel(row.priority) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="assignee_id" label="负责人" width="100">
          <template #default="{ row }">{{ row.assignee_id ? '用户#' + row.assignee_id : '-' }}</template>
        </el-table-column>
        <el-table-column label="截止时间" width="160">
          <template #default="{ row }">
            <span :class="{ 'text-danger': isOverdue(row) }">{{ formatTime(row.deadline) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" width="100" />
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 新建待办对话框 -->
    <el-dialog v-model="dialogVisible" title="新建待办" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" style="width: 100%">
            <el-option label="紧急" value="urgent" />
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
        </el-form-item>
        <el-form-item label="截止时间">
          <el-date-picker v-model="form.deadline" type="datetime" placeholder="选择截止时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="关联设备SN">
          <el-input v-model="form.related_device_sn" placeholder="可选" />
        </el-form-item>
        <el-form-item label="关联退费单号">
          <el-input v-model="form.related_refund_no" placeholder="可选" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" />
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
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listTodos, createTodo } from '@/api/todo'
import dayjs from 'dayjs'

const router = useRouter()
const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ status: '', priority: '' })
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ title: '', priority: 'medium', deadline: null, related_device_sn: '', related_refund_no: '', description: '' })

const statusMap = { pending: '待办', processing: '处理中', confirming: '待确认', done: '已完成' }
const statusLabel = (s) => statusMap[s] || s
const statusType = (s) => ({ pending: 'info', processing: 'warning', confirming: 'warning', done: 'success' }[s] || 'info')
const priorityMap = { urgent: '紧急', high: '高', medium: '中', low: '低' }
const priorityLabel = (p) => priorityMap[p] || p
const priorityType = (p) => ({ urgent: 'danger', high: 'warning', medium: '', low: 'info' }[p] || '')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const isOverdue = (row) => row.deadline && row.status !== 'done' && dayjs(row.deadline).isBefore(dayjs())

async function loadData() {
  loading.value = true
  try {
    const res = await listTodos({ page: page.value, page_size: 20, status: filter.status, priority: filter.priority })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function goDetail(row) {
  router.push(`/todos/${row.id}`)
}

function openCreate() {
  Object.assign(form, { title: '', priority: 'medium', deadline: null, related_device_sn: '', related_refund_no: '', description: '' })
  dialogVisible.value = true
}

async function handleCreate() {
  if (!form.title.trim()) {
    ElMessage.warning('请输入标题')
    return
  }
  submitting.value = true
  try {
    await createTodo({
      title: form.title,
      priority: form.priority,
      deadline: form.deadline ? dayjs(form.deadline).toISOString() : null,
      related_device_sn: form.related_device_sn || null,
      related_refund_no: form.related_refund_no || null,
      description: form.description || null,
    })
    ElMessage.success('创建成功')
    dialogVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

onMounted(loadData)
</script>
