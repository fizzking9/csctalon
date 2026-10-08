<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">专项任务</div>
      <el-button v-if="authStore.isAdmin" type="primary" :icon="Plus" @click="openCreate">新建专项</el-button>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="loadData">
          <el-option v-for="(label, key) in statusMap" :key="key" :label="label" :value="key" />
        </el-select>
        <el-select v-model="filter.task_type" placeholder="类型" clearable style="width: 140px" @change="loadData">
          <el-option label="清退" value="清退" />
          <el-option label="涨价" value="涨价" />
          <el-option label="限速" value="限速" />
          <el-option label="其他" value="其他" />
        </el-select>
        <el-input v-model="filter.keyword" placeholder="搜索专项名称" clearable style="width: 200px" @keyup.enter="loadData" @clear="loadData" />
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe style="cursor: pointer" @row-click="goDetail">
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="typeTagType(row.task_type)">{{ row.task_type || '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="专项名称" show-overflow-tooltip min-width="180" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="设备进度" width="140">
          <template #default="{ row }">
            <span v-if="row.device_total">{{ row.device_done || 0 }} / {{ row.device_total }}</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="负责人" width="100">
          <template #default="{ row }">{{ row.owner_id ? '用户#' + row.owner_id : '-' }}</template>
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

    <!-- 新建专项对话框 -->
    <el-dialog v-model="dialogVisible" title="新建专项任务" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="专项名称" required>
          <el-input v-model="form.title" placeholder="如:7月清退专项" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.task_type" style="width: 100%" placeholder="选择类型">
            <el-option label="清退" value="清退" />
            <el-option label="涨价" value="涨价" />
            <el-option label="限速" value="限速" />
            <el-option label="其他" value="其他" />
          </el-select>
        </el-form-item>
        <el-form-item label="截止时间">
          <el-date-picker v-model="form.deadline" type="datetime" placeholder="选择截止时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="设备SN">
          <el-input
            v-model="form.sn_text"
            type="textarea"
            :rows="5"
            placeholder="批量录入设备SN,支持换行/逗号/空格分隔(可选,后续也可在详情页添加)"
          />
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
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listTasks, createTask } from '@/api/task'
import { useAuthStore } from '@/stores/auth'
import dayjs from 'dayjs'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ status: '', task_type: '', keyword: '' })
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ title: '', task_type: '', deadline: null, sn_text: '', description: '' })

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
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const isOverdue = (row) => row.deadline && row.status !== 'done' && row.status !== 'archived' && dayjs(row.deadline).isBefore(dayjs())

async function loadData() {
  loading.value = true
  try {
    const res = await listTasks({ page: page.value, page_size: 20, ...filter })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function goDetail(row) {
  router.push(`/tasks/${row.id}`)
}

function openCreate() {
  Object.assign(form, { title: '', task_type: '', deadline: null, sn_text: '', description: '' })
  dialogVisible.value = true
}

// #253 B3-6 接待分析闭环入口: /tasks?create=1&title=...&context=...
// 若 url 带 create=1 → 自动打开新建对话框,并把 title/context 预填进表单。
// context 是跳转页塞来的业务上下文(时间窗/指标值等),落到 description 里便于追溯。
function openCreateFromQuery() {
  const q = route.query
  if (q.create !== '1') return
  Object.assign(form, {
    title: q.title || '',
    task_type: '',
    deadline: null,
    sn_text: '',
    description: q.context || '',
  })
  dialogVisible.value = true
}

async function handleCreate() {
  if (!form.title.trim()) {
    ElMessage.warning('请输入专项名称')
    return
  }
  submitting.value = true
  try {
    await createTask({
      title: form.title,
      task_type: form.task_type || null,
      deadline: form.deadline ? dayjs(form.deadline).toISOString() : null,
      sn_text: form.sn_text || null,
      description: form.description || null,
    })
    ElMessage.success('创建成功')
    dialogVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadData()
  openCreateFromQuery()
})
</script>

<style scoped>
.text-muted {
  color: #c0c4cc;
}
</style>
