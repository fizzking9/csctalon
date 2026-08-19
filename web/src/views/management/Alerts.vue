<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">预警中心</div>
        <div class="page-sub">异常自动识别 / 工单积压 / 退款激增 · 让异常「自己浮现」</div>
      </div>
      <div class="flex gap-8">
        <el-button type="primary" :icon="Search" @click="handleScan">扫描预警</el-button>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :md="10">
        <el-card>
          <template #header>
            <div class="flex-between">
              <span style="font-weight: 600">预警规则</span>
              <el-button v-if="authStore.isAdmin" text type="primary" @click="openRule">新建规则</el-button>
            </div>
          </template>
          <el-table :data="rules" border stripe size="small">
            <el-table-column prop="name" label="规则名" show-overflow-tooltip />
            <el-table-column label="类型" width="110">
              <template #default="{ row }"><el-tag size="small">{{ ruleTypeLabel(row.type) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="阈值" width="80" align="right">
              <template #default="{ row }">{{ row.threshold ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="启用" width="70">
              <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '开' : '关' }}</el-tag></template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="14">
        <el-card>
          <template #header><span style="font-weight: 600">预警记录</span></template>
          <el-table :data="alerts" v-loading="loading" border stripe size="small">
            <el-table-column label="级别" width="70">
              <template #default="{ row }"><el-tag :type="levelType(row.level)" size="small">{{ levelLabel(row.level) }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="title" label="标题" show-overflow-tooltip />
            <el-table-column label="发生时间" width="150">
              <template #default="{ row }">{{ formatTime(row.occurred_at) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag :type="alertStatusType(row.status)" size="small">{{ alertStatusLabel(row.status) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="140" align="center">
              <template #default="{ row }">
                <el-button v-if="row.status === 'pending'" link type="primary" @click="setStatus(row, 'confirmed')">确认</el-button>
                <el-button v-if="row.status === 'pending'" link type="info" @click="setStatus(row, 'ignored')">忽略</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="ruleVisible" title="新建预警规则" width="480px">
      <el-form :model="ruleForm" label-width="90px">
        <el-form-item label="规则名" required><el-input v-model="ruleForm.name" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="ruleForm.type" style="width: 100%">
            <el-option label="退款激增" value="refund_surge" />
            <el-option label="工单积压" value="ticket_backlog" />
            <el-option label="影响事件" value="incident_surge" />
          </el-select>
        </el-form-item>
        <el-form-item label="阈值"><el-input-number v-model="ruleForm.threshold" :min="1" style="width: 100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreateRule">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { listRules, createRule, listAlerts, setAlertStatus, scanAlerts } from '@/api/alert'
import dayjs from 'dayjs'

const authStore = useAuthStore()
const rules = ref([])
const alerts = ref([])
const loading = ref(false)
const submitting = ref(false)
const ruleVisible = ref(false)
const ruleForm = reactive({ name: '', type: 'refund_surge', threshold: 100 })
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const ruleTypeLabel = (t) => ({ refund_surge: '退款激增', ticket_backlog: '工单积压', incident_surge: '影响事件' }[t] || t)
const levelType = (l) => ({ high: 'danger', medium: 'warning', low: 'info' }[l] || 'info')
const levelLabel = (l) => ({ high: '高', medium: '中', low: '低' }[l] || l)
const alertStatusType = (s) => ({ pending: 'warning', confirmed: 'success', ignored: 'info' }[s] || 'info')
const alertStatusLabel = (s) => ({ pending: '待确认', confirmed: '已确认', ignored: '已忽略' }[s] || s)

async function loadRules() {
  const res = await listRules()
  rules.value = res.data || []
}

async function loadAlertsData() {
  loading.value = true
  try {
    const res = await listAlerts({ page: 1, page_size: 50 })
    alerts.value = res.data.items || []
  } finally {
    loading.value = false
  }
}

function openRule() {
  ruleForm.name = ''
  ruleForm.type = 'refund_surge'
  ruleForm.threshold = 100
  ruleVisible.value = true
}

async function handleCreateRule() {
  if (!ruleForm.name.trim()) {
    ElMessage.warning('请输入规则名')
    return
  }
  submitting.value = true
  try {
    await createRule({ name: ruleForm.name, type: ruleForm.type, threshold: ruleForm.threshold })
    ElMessage.success('已创建')
    ruleVisible.value = false
    loadRules()
  } finally {
    submitting.value = false
  }
}

async function setStatus(row, status) {
  await setAlertStatus(row.id, { status })
  ElMessage.success('已更新')
  loadAlertsData()
}

async function handleScan() {
  const res = await scanAlerts()
  ElMessage.success(`扫描完成, 生成 ${res.data.created} 条预警`)
  loadAlertsData()
}

function loadAll() {
  loadRules()
  loadAlertsData()
}

onMounted(loadAll)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
</style>
