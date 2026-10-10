<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">{{ todo.title }}</div>
      <el-button :icon="ArrowLeft" @click="$router.back()">返回</el-button>
    </div>

    <el-card v-loading="loading" class="mb-16">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="状态">
          <el-tag :type="statusType(todo.status)">{{ statusLabel(todo.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="优先级">
          <el-tag :type="priorityType(todo.priority)">{{ priorityLabel(todo.priority) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="创建人">{{ todo.created_by }}</el-descriptions-item>
        <el-descriptions-item label="截止时间">{{ formatTime(todo.deadline) }}</el-descriptions-item>
        <el-descriptions-item label="关联设备">{{ todo.related_device_sn || '-' }}</el-descriptions-item>
        <el-descriptions-item label="关联退费单">{{ todo.related_refund_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间" :span="2">{{ formatTime(todo.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ todo.description || '-' }}</el-descriptions-item>
      </el-descriptions>

      <div class="mt-16 flex gap-8">
        <el-select v-model="newStatus" placeholder="变更状态" style="width: 160px">
          <el-option label="待办" value="pending" />
          <el-option label="处理中" value="processing" />
          <el-option label="待确认" value="confirming" />
          <el-option label="已完成" value="done" />
        </el-select>
        <el-button type="primary" @click="changeStatus">更新状态</el-button>
        <el-button type="danger" :icon="Delete" @click="handleDelete">删除</el-button>
      </div>
    </el-card>

    <el-card header="跟进记录(时间线)">
      <el-timeline v-if="followups.length">
        <el-timeline-item
          v-for="f in followups"
          :key="f.id"
          :timestamp="formatTime(f.created_at)"
          placement="top"
        >
          <el-card>
            <div class="flex-between mb-16">
              <strong>{{ f.author_name || '匿名' }}</strong>
              <el-tag v-if="f.status_change" size="small">{{ f.status_change }}</el-tag>
            </div>
            <p>{{ f.content }}</p>
          </el-card>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无跟进记录" />

      <el-divider />

      <el-input v-model="followupContent" type="textarea" :rows="3" placeholder="输入跟进内容..." />
      <div class="mt-16">
        <el-button type="primary" :loading="submitting" @click="addFollowupFn">添加跟进</el-button>
        <el-checkbox v-model="withStatusChange" style="margin-left: 16px">同时变更状态</el-checkbox>
        <el-select v-if="withStatusChange" v-model="followupStatus" style="width: 140px; margin-left: 8px">
          <el-option label="待办" value="pending" />
          <el-option label="处理中" value="processing" />
          <el-option label="待确认" value="confirming" />
          <el-option label="已完成" value="done" />
        </el-select>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Delete } from '@element-plus/icons-vue'
import { getTodo, updateTodo, deleteTodo, addFollowup } from '@/api/todo'
import dayjs from 'dayjs'

const route = useRoute()
const router = useRouter()
const todo = ref({})
const followups = ref([])
const loading = ref(false)
const newStatus = ref('')
const followupContent = ref('')
const withStatusChange = ref(false)
const followupStatus = ref('processing')
const submitting = ref(false)

const statusMap = { pending: '待办', processing: '处理中', confirming: '待确认', done: '已完成' }
const statusLabel = (s) => statusMap[s] || s
const statusType = (s) => ({ pending: 'info', processing: 'warning', confirming: 'warning', done: 'success' }[s] || 'info')
const priorityMap = { urgent: '紧急', high: '高', medium: '中', low: '低' }
const priorityLabel = (p) => priorityMap[p] || p
const priorityType = (p) => ({ urgent: 'danger', high: 'warning', medium: '', low: 'info' }[p] || '')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

async function loadDetail() {
  loading.value = true
  try {
    const res = await getTodo(route.params.id)
    todo.value = res.data
    followups.value = res.data.followups || []
    newStatus.value = todo.value.status
  } finally {
    loading.value = false
  }
}

async function changeStatus() {
  if (newStatus.value === todo.value.status) {
    ElMessage.info('状态未变化')
    return
  }
  await updateTodo(route.params.id, { status: newStatus.value })
  ElMessage.success('状态已更新')
  loadDetail()
}

async function addFollowupFn() {
  if (!followupContent.value.trim()) {
    ElMessage.warning('请输入跟进内容')
    return
  }
  submitting.value = true
  try {
    await addFollowup(route.params.id, {
      content: followupContent.value,
      new_status: withStatusChange.value ? followupStatus.value : null,
    })
    ElMessage.success('跟进已添加')
    followupContent.value = ''
    loadDetail()
  } finally {
    submitting.value = false
  }
}

async function handleDelete() {
  await ElMessageBox.confirm('确认删除该待办及其跟进记录?', '提示', { type: 'warning' })
  await deleteTodo(route.params.id)
  ElMessage.success('已删除')
  router.push('/todos')
}

onMounted(loadDetail)
</script>
