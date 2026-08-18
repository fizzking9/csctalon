<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">跨部门协作</div>
        <div class="page-sub">客服给上游打分(响应/解决率) · 推动上游改进</div>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">派发问题</el-button>
    </div>

    <el-card class="mb-16">
      <template #header><span style="font-weight: 600">上游部门响应/解决率</span></template>
      <el-table :data="stats" border stripe size="small">
        <el-table-column prop="dept" label="上游部门" />
        <el-table-column prop="total" label="派发数" width="90" align="right" />
        <el-table-column prop="responded" label="已响应" width="90" align="right" />
        <el-table-column prop="resolved" label="已解决" width="90" align="right" />
        <el-table-column label="响应率" width="100" align="right">
          <template #default="{ row }">{{ row.response_rate }}%</template>
        </el-table-column>
        <el-table-column label="解决率" width="100" align="right">
          <template #default="{ row }"><span style="color: #409eff; font-weight: 600">{{ row.resolve_rate }}%</span></template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="upstream_dept" label="上游部门" width="130" />
        <el-table-column prop="issue" label="问题描述" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="响应时间" width="150">
          <template #default="{ row }">{{ formatTime(row.responded_at) }}</template>
        </el-table-column>
        <el-table-column label="解决时间" width="150">
          <template #default="{ row }">{{ formatTime(row.resolved_at) }}</template>
        </el-table-column>
        <el-table-column label="打分" width="80" align="center">
          <template #default="{ row }"><el-rate v-model="row.score" disabled size="small" /></template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center">
          <template #default="{ row }">
            <el-button v-if="row.status === '待响应'" link type="primary" @click="setStatus(row, '处理中')">已响应</el-button>
            <el-button v-if="['待响应', '处理中'].includes(row.status)" link type="success" @click="setStatus(row, '已解决')">已解决</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" title="派发上游问题" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="上游部门" required>
          <el-select v-model="form.upstream_dept" allow-create filterable style="width: 100%" placeholder="如 生产/运营/商务部/系统平台">
            <el-option v-for="d in ['生产', '运营', '商务部', '店铺', '系统平台开发', '其他服务商']" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="问题描述" required>
          <el-input v-model="form.issue" type="textarea" :rows="4" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">派发</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { listUpstreamTickets, createUpstreamTicket, updateUpstreamTicket, deleteUpstreamTicket, upstreamStats } from '@/api/upstreamTicket'
import dayjs from 'dayjs'

const list = ref([])
const stats = ref([])
const loading = ref(false)
const submitting = ref(false)
const dialogVisible = ref(false)
const form = reactive({ upstream_dept: '', issue: '' })
const formatTime = (t) => (t ? dayjs(t).format('MM-DD HH:mm') : '-')
const statusType = (s) => ({ 待响应: 'warning', 处理中: 'primary', 已解决: 'success', 挂起: 'info' }[s] || 'info')

async function loadData() {
  loading.value = true
  try {
    const res = await listUpstreamTickets({ page: 1, page_size: 100 })
    list.value = res.data.items || []
    const sr = await upstreamStats()
    stats.value = sr.data || []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.upstream_dept = ''
  form.issue = ''
  dialogVisible.value = true
}

async function handleCreate() {
  if (!form.upstream_dept.trim() || !form.issue.trim()) {
    ElMessage.warning('请填写部门和问题')
    return
  }
  submitting.value = true
  try {
    await createUpstreamTicket({ upstream_dept: form.upstream_dept, issue: form.issue })
    ElMessage.success('已派发')
    dialogVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function setStatus(row, status) {
  await updateUpstreamTicket(row.id, { status })
  ElMessage.success('已更新')
  loadData()
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确认删除?', '提示', { type: 'warning' })
  await deleteUpstreamTicket(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
</style>
