<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">{{ task.title }}</div>
      <el-button :icon="ArrowLeft" @click="$router.back()">返回</el-button>
    </div>

    <!-- 基本信息 -->
    <el-card v-loading="loading" class="mb-16">
      <el-descriptions :column="3" border>
        <el-descriptions-item label="状态">
          <el-tag :type="statusType(task.status)">{{ statusLabel(task.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="类型">
          <el-tag size="small" :type="typeTagType(task.task_type)">{{ task.task_type || '-' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="负责人">
          {{ task.owner_id ? '用户#' + task.owner_id : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="截止时间">
          <span :class="{ 'text-danger': isOverdue }">{{ formatTime(task.deadline) }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="创建人">{{ task.created_by }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatTime(task.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="3">{{ task.description || '-' }}</el-descriptions-item>
      </el-descriptions>

      <div v-if="authStore.isAdmin" class="mt-16 flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="newStatus" placeholder="变更状态" style="width: 160px">
          <el-option v-for="(label, key) in statusMap" :key="key" :label="label" :value="key" />
        </el-select>
        <el-button type="primary" @click="changeStatus">更新状态</el-button>
        <el-button type="danger" :icon="Delete" @click="handleDelete">删除专项</el-button>
      </div>
    </el-card>

    <!-- 设备批次 -->
    <el-card class="mb-16">
      <template #header>
        <div class="flex-between">
          <div class="flex gap-16" style="align-items: center">
            <span>设备批次</span>
            <el-tag v-if="task.device_total" size="small">
              共 {{ task.device_total }} · 待执行 {{ task.device_pending || 0 }} · 执行中 {{ task.device_executing || 0 }} · 已完成 {{ task.device_done || 0 }} · 异常 {{ task.device_abnormal || 0 }}
            </el-tag>
          </div>
          <el-button v-if="authStore.isAdmin" type="primary" size="small" :icon="Plus" @click="deviceDialogVisible = true">批量添加设备</el-button>
        </div>
      </template>

      <div class="flex gap-8 mb-16" style="flex-wrap: wrap">
        <el-select v-model="deviceFilter.device_status" placeholder="设备状态" clearable style="width: 140px" @change="loadDevices">
          <el-option label="待执行" value="pending" />
          <el-option label="执行中" value="executing" />
          <el-option label="已完成" value="done" />
          <el-option label="异常" value="abnormal" />
        </el-select>
        <el-input v-model="deviceFilter.keyword" placeholder="搜索SN" clearable style="width: 200px" @keyup.enter="loadDevices" @clear="loadDevices" />
        <el-button :icon="Refresh" @click="loadDevices">刷新</el-button>
      </div>

      <el-table v-loading="deviceLoading" :data="devices" border stripe size="small">
        <el-table-column prop="device_sn" label="设备SN" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <el-select v-if="authStore.isAdmin" v-model="row.device_status" size="small" @change="onDeviceStatusChange(row)">
              <el-option label="待执行" value="pending" />
              <el-option label="执行中" value="executing" />
              <el-option label="已完成" value="done" />
              <el-option label="异常" value="abnormal" />
            </el-select>
            <el-tag v-else :type="deviceStatusType(row.device_status)" size="small">{{ deviceStatusLabel(row.device_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="150" show-overflow-tooltip />
        <el-table-column label="添加时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column v-if="authStore.isAdmin" label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" size="small" link :icon="Delete" @click="removeDevice(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ deviceTotal }} 条</span>
        <el-pagination v-model:current-page="devicePage" :total="deviceTotal" :page-size="50" layout="prev, pager, next" @current-change="loadDevices" />
      </div>
    </el-card>

    <!-- 跟进记录 -->
    <el-card header="跟进记录(时间线)">
      <el-timeline v-if="followups.length">
        <el-timeline-item v-for="f in followups" :key="f.id" :timestamp="formatTime(f.created_at)" placement="top">
          <el-card>
            <div class="flex-between mb-16">
              <strong>{{ f.author_name || '匿名' }}</strong>
              <el-tag v-if="f.status_change" size="small" type="warning">{{ f.status_change }}</el-tag>
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
          <el-option v-for="(label, key) in statusMap" :key="key" :label="label" :value="key" />
        </el-select>
      </div>
    </el-card>

    <!-- 批量添加设备对话框 -->
    <el-dialog v-model="deviceDialogVisible" title="批量添加设备SN" width="600px">
      <el-input v-model="snText" type="textarea" :rows="8" placeholder="粘贴设备SN列表,支持换行/逗号/空格分隔,自动去重" />
      <div v-if="addResult" class="mt-16">
        <el-alert :title="`新增 ${addResult.added} 个,跳过 ${addResult.skipped} 个已存在,当前共 ${addResult.device_total} 个设备`" type="success" :closable="false" />
      </div>
      <template #footer>
        <el-button @click="deviceDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="deviceSubmitting" @click="handleAddDevices">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Delete, Plus, Refresh } from '@element-plus/icons-vue'
import { getTask, updateTask, deleteTask, listTaskDevices, addTaskDevices, updateTaskDevice, removeTaskDevice, addTaskFollowup } from '@/api/task'
import { useAuthStore } from '@/stores/auth'
import dayjs from 'dayjs'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const taskId = route.params.id

const task = ref({})
const followups = ref([])
const loading = ref(false)
const newStatus = ref('')
const isOverdue = computed(() => task.value.deadline && !['done', 'archived'].includes(task.value.status) && dayjs(task.value.deadline).isBefore(dayjs()))

// 设备批次
const devices = ref([])
const deviceTotal = ref(0)
const deviceLoading = ref(false)
const devicePage = ref(1)
const deviceFilter = reactive({ device_status: '', keyword: '' })
const deviceDialogVisible = ref(false)
const snText = ref('')
const deviceSubmitting = ref(false)
const addResult = ref(null)

// 跟进
const followupContent = ref('')
const withStatusChange = ref(false)
const followupStatus = ref('executing')
const submitting = ref(false)

const statusMap = {
  pending_start: '待启动',
  executing: '执行中',
  following: '跟进中',
  done: '已完成',
  archived: '已归档',
}
const statusLabel = (s) => statusMap[s] || s
const statusType = (s) => ({ pending_start: 'info', executing: 'warning', following: 'warning', done: 'success', archived: 'info' }[s] || 'info')
const typeTagType = (t) => ({ 清退: 'danger', 涨价: 'warning', 限速: 'warning', 其他: 'info' }[t] || '')
const deviceStatusMap = { pending: '待执行', executing: '执行中', done: '已完成', abnormal: '异常' }
const deviceStatusLabel = (s) => deviceStatusMap[s] || s
const deviceStatusType = (s) => ({ pending: 'info', executing: 'warning', done: 'success', abnormal: 'danger' }[s] || 'info')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

async function loadDetail() {
  loading.value = true
  try {
    const res = await getTask(taskId)
    task.value = res.data
    followups.value = res.data.followups || []
    newStatus.value = task.value.status
  } finally {
    loading.value = false
  }
}

async function loadDevices() {
  deviceLoading.value = true
  try {
    const res = await listTaskDevices(taskId, { page: devicePage.value, page_size: 50, ...deviceFilter })
    devices.value = res.data.items
    deviceTotal.value = res.data.total
  } finally {
    deviceLoading.value = false
  }
}

async function changeStatus() {
  if (newStatus.value === task.value.status) {
    ElMessage.info('状态未变化')
    return
  }
  await updateTask(taskId, { status: newStatus.value })
  ElMessage.success('状态已更新')
  loadDetail()
}

async function onDeviceStatusChange(row) {
  try {
    await updateTaskDevice(taskId, row.id, { device_status: row.device_status })
    ElMessage.success(`${row.device_sn} 状态已更新`)
    loadDetail()
  } catch (e) {
    loadDevices()
  }
}

async function removeDevice(row) {
  await ElMessageBox.confirm(`确认移除设备 ${row.device_sn}?`, '提示', { type: 'warning' })
  await removeTaskDevice(taskId, row.id)
  ElMessage.success('已移除')
  loadDevices()
  loadDetail()
}

async function handleAddDevices() {
  if (!snText.value.trim()) {
    ElMessage.warning('请输入设备SN')
    return
  }
  deviceSubmitting.value = true
  try {
    const res = await addTaskDevices(taskId, { sn_text: snText.value })
    addResult.value = res.data
    ElMessage.success(`新增 ${res.data.added} 个设备`)
    snText.value = ''
    loadDevices()
    loadDetail()
  } finally {
    deviceSubmitting.value = false
  }
}

async function addFollowupFn() {
  if (!followupContent.value.trim()) {
    ElMessage.warning('请输入跟进内容')
    return
  }
  submitting.value = true
  try {
    await addTaskFollowup(taskId, {
      content: followupContent.value,
      new_status: withStatusChange.value ? followupStatus.value : null,
    })
    ElMessage.success('跟进已添加')
    followupContent.value = ''
    withStatusChange.value = false
    loadDetail()
  } finally {
    submitting.value = false
  }
}

async function handleDelete() {
  await ElMessageBox.confirm('确认删除该专项任务及其设备批次与跟进记录?', '提示', { type: 'warning' })
  await deleteTask(taskId)
  ElMessage.success('已删除')
  router.push('/tasks')
}

onMounted(() => {
  loadDetail()
  loadDevices()
})
</script>

<style scoped>
.text-danger {
  color: #f56c6c;
}
</style>
