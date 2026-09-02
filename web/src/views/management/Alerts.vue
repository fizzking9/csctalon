<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">预警中心</div>
        <div class="page-sub">指标阈值化监测 · 四级水位(优秀/达标/预警/告警) · 站内通知</div>
      </div>
      <div class="flex gap-8">
        <el-button type="primary" :icon="Search" @click="handleScan">扫描预警</el-button>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <!-- ========== 指标水位 ========== -->
      <el-tab-pane label="指标水位" name="metrics">
        <el-row :gutter="12">
          <el-col v-for="m in metricList" :key="m.code" :xs="24" :sm="12" :md="8" :lg="6">
            <el-card class="mb-12 metric-card" shadow="hover">
              <div class="flex-between">
                <span class="metric-name">{{ m.name_cn }}</span>
                <el-tag size="small" :type="levelType(m.level)" effect="dark">{{ levelLabel(m.level) }}</el-tag>
              </div>
              <div class="metric-value-row">
                <span class="metric-value">{{ m.value == null ? '—' : formatNum(m.value) }}</span>
                <span class="metric-unit">{{ m.unit }}</span>
              </div>
              <div v-if="!m.data_ok" class="metric-data-warn">数据不完整, 可能未同步</div>
              <div class="metric-thr">
                优 {{ fmtThr(m.threshold?.excellent_value) }} · 警 {{ fmtThr(m.threshold?.warning_value) }} · 告 {{ fmtThr(m.threshold?.critical_value) }}
              </div>
              <div class="flex-between mt-8">
                <span class="metric-dir">{{ m.direction === 'higher_better' ? '越高越好' : '越低越好' }}</span>
                <el-button size="small" text type="primary" @click="openThreshold(m)">配置阈值</el-button>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ========== 预警规则 ========== -->
      <el-tab-pane label="预警规则" name="rules">
        <div class="mb-12">
          <el-button v-if="authStore.isAdmin" type="primary" @click="openRule">新建规则</el-button>
        </div>
        <el-table :data="rules" border stripe size="small">
          <el-table-column prop="name" label="规则名" show-overflow-tooltip />
          <el-table-column label="类型" width="130">
            <template #default="{ row }">
              <el-tag v-if="row.metric_code" type="primary" size="small">指标:{{ metricName(row.metric_code) }}</el-tag>
              <el-tag v-else size="small">{{ ruleTypeLabel(row.type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="阈值" width="180">
            <template #default="{ row }">
              <span v-if="row.metric_code">见指标水位</span>
              <span v-else>{{ row.threshold ?? '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="通道" width="110">
            <template #default="{ row }">{{ (row.channels || ['inapp']).join(', ') }}</template>
          </el-table-column>
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '开' : '关' }}</el-tag></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ========== 预警记录 ========== -->
      <el-tab-pane label="预警记录" name="alerts">
        <el-table v-loading="loading" :data="alerts" border stripe size="small">
          <el-table-column label="级别" width="80">
            <template #default="{ row }"><el-tag :type="levelType(row.level)" size="small">{{ levelLabel(row.level) }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="title" label="标题" show-overflow-tooltip />
          <el-table-column label="指标值" width="90" align="right">
            <template #default="{ row }">{{ row.metric_value != null ? formatNum(row.metric_value) : '-' }}</template>
          </el-table-column>
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
      </el-tab-pane>

      <!-- ========== 通知通道 ========== -->
      <el-tab-pane label="通知通道" name="channels">
        <div class="mb-12">
          <el-button v-if="authStore.isAdmin" type="primary" @click="openChannel">新建通道</el-button>
        </div>
        <el-table :data="channels" border stripe size="small">
          <el-table-column prop="name_cn" label="通道名" width="140" />
          <el-table-column prop="code" label="code" width="140" />
          <el-table-column prop="channel_type" label="类型" width="100">
            <template #default="{ row }">
              <el-tag size="small">{{ { inapp: '站内信', webhook: '群机器人', email: '邮件' }[row.channel_type] || row.channel_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '开' : '关' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="操作" width="160" align="center">
            <template #default="{ row }">
              <el-button link type="primary" @click="handleTestChannel(row)">测试</el-button>
              <el-button v-if="row.code !== 'inapp'" link type="danger" @click="handleDeleteChannel(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 新建规则(指标模式/旧模式) -->
    <el-dialog v-model="ruleVisible" title="新建预警规则" width="520px">
      <el-form :model="ruleForm" label-width="100px">
        <el-form-item label="规则名" required><el-input v-model="ruleForm.name" /></el-form-item>
        <el-form-item label="模式">
          <el-radio-group v-model="ruleForm.mode">
            <el-radio-button label="metric">指标模式(推荐)</el-radio-button>
            <el-radio-button label="legacy">旧模式(激增类)</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="ruleForm.mode === 'metric'">
          <el-form-item label="指标" required>
            <el-select v-model="ruleForm.metric_code" style="width: 100%" filterable>
              <el-option v-for="m in metricList" :key="m.code" :label="`${m.name_cn}(${m.code})`" :value="m.code" />
            </el-select>
          </el-form-item>
          <el-form-item label="阈值">
            <div class="thr-hint">优秀/预警/告警线在「指标水位」页配置, 规则自动引用(当前: 优{{ fmtThr(metricThreshold(ruleForm.metric_code)?.excellent_value) }} / 警{{ fmtThr(metricThreshold(ruleForm.metric_code)?.warning_value) }} / 告{{ fmtThr(metricThreshold(ruleForm.metric_code)?.critical_value) }})</div>
          </el-form-item>
          <el-form-item label="通知角色">
            <el-select v-model="ruleForm.recipient_roles" multiple style="width: 100%">
              <el-option label="管理员" value="admin" />
              <el-option label="主管" value="supervisor" />
              <el-option label="客服" value="customer_service" />
            </el-select>
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="类型">
            <el-select v-model="ruleForm.type" style="width: 100%">
              <el-option label="退款激增" value="refund_surge" />
              <el-option label="工单积压" value="ticket_backlog" />
              <el-option label="影响事件" value="incident_surge" />
            </el-select>
          </el-form-item>
          <el-form-item label="阈值"><el-input-number v-model="ruleForm.threshold" :min="1" style="width: 100%" /></el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="ruleVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreateRule">创建</el-button>
      </template>
    </el-dialog>

    <!-- 配置阈值 -->
    <el-dialog v-model="thresholdVisible" :title="`配置阈值: ${thresholdForm.name}`" width="480px">
      <el-form :model="thresholdForm" label-width="90px">
        <el-form-item label="优秀线"><el-input-number v-model="thresholdForm.excellent_value" :step="1" style="width: 100%" /></el-form-item>
        <el-form-item label="预警线" required><el-input-number v-model="thresholdForm.warning_value" :step="1" style="width: 100%" /></el-form-item>
        <el-form-item label="告警线" required><el-input-number v-model="thresholdForm.critical_value" :step="1" style="width: 100%" /></el-form-item>
        <el-form-item label="说明">
          <div class="thr-hint">越高越好型: 值 ≥ 优秀线=优秀, &lt; 预警线=预警, &lt; 告警线=告警; 越低越好型相反。修改留痕。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="thresholdVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSaveThreshold">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新建通道 -->
    <el-dialog v-model="channelVisible" title="新建通知通道" width="480px">
      <el-form :model="channelForm" label-width="90px">
        <el-form-item label="code" required><el-input v-model="channelForm.code" placeholder="如 wecom_robot / dingtalk_robot / feishu_robot" /></el-form-item>
        <el-form-item label="通道名" required><el-input v-model="channelForm.name_cn" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="channelForm.channel_type" style="width: 100%">
            <el-option label="群机器人 webhook" value="webhook" />
            <el-option label="邮件" value="email" />
          </el-select>
        </el-form-item>
        <el-form-item label="Webhook URL">
          <el-input v-model="channelForm.webhook_url" placeholder="企业微信/钉钉/飞书群机器人 webhook 地址" />
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="channelForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="channelVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreateChannel">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { listRules, createRule, listAlerts, setAlertStatus, scanAlerts } from '@/api/alert'
import { listMetrics, updateThresholds } from '@/api/metric'
import { listChannels, createChannel, deleteChannel, testChannel } from '@/api/channel'
import dayjs from 'dayjs'

const authStore = useAuthStore()
const tab = ref('metrics')
const rules = ref([])
const alerts = ref([])
const channels = ref([])
const metricList = ref([])
const loading = ref(false)
const submitting = ref(false)

const ruleVisible = ref(false)
const ruleForm = reactive({ name: '', mode: 'metric', metric_code: '', excellent_value: undefined, warning_value: undefined, critical_value: undefined, recipient_roles: ['admin', 'supervisor'], type: 'refund_surge', threshold: 100 })

const thresholdVisible = ref(false)
const thresholdForm = reactive({ code: '', name: '', excellent_value: undefined, warning_value: undefined, critical_value: undefined })

const channelVisible = ref(false)
const channelForm = reactive({ code: '', name_cn: '', channel_type: 'webhook', webhook_url: '', description: '' })

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const formatNum = (v) => (v == null ? '-' : Number(v).toLocaleString('zh-CN'))
const fmtThr = (v) => (v == null ? '-' : v)
const metricName = (code) => (metricList.value.find((m) => m.code === code)?.name_cn || code)
const metricThreshold = (code) => metricList.value.find((m) => m.code === code)?.threshold || {}
const ruleTypeLabel = (t) => ({ refund_surge: '退款激增', ticket_backlog: '工单积压', incident_surge: '影响事件' }[t] || t)

// 四级水位 + 旧模式级别
const levelType = (l) => ({ excellent: 'primary', green: 'success', warning: 'warning', critical: 'danger', high: 'danger', medium: 'warning', low: 'info', data_missing: 'info' }[l] || 'info')
const levelLabel = (l) => ({ excellent: '优秀', green: '达标', warning: '预警', critical: '告警', high: '高', medium: '中', low: '低', data_missing: '数据缺失' }[l] || l)
const alertStatusType = (s) => ({ pending: 'warning', confirmed: 'success', ignored: 'info', recovered: 'info' }[s] || 'info')
const alertStatusLabel = (s) => ({ pending: '待确认', confirmed: '已确认', ignored: '已忽略', recovered: '已恢复' }[s] || s)

async function loadMetrics() {
  const res = await listMetrics()
  metricList.value = res.data?.items || []
}
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
async function loadChannels() {
  const res = await listChannels()
  channels.value = res.data?.items || []
}

function openRule() {
  Object.assign(ruleForm, { name: '', mode: 'metric', metric_code: '', excellent_value: undefined, warning_value: undefined, critical_value: undefined, recipient_roles: ['admin', 'supervisor'], type: 'refund_surge', threshold: 100 })
  ruleVisible.value = true
}

async function handleCreateRule() {
  if (!ruleForm.name.trim()) {
    ElMessage.warning('请输入规则名')
    return
  }
  submitting.value = true
  try {
    if (ruleForm.mode === 'metric') {
      if (!ruleForm.metric_code) {
        ElMessage.warning('请选择指标')
        return
      }
      await createRule({
        name: ruleForm.name,
        type: 'metric',
        metric_code: ruleForm.metric_code,
        recipient_roles: ruleForm.recipient_roles,
        channels: ['inapp'],
      })
    } else {
      await createRule({ name: ruleForm.name, type: ruleForm.type, threshold: ruleForm.threshold })
    }
    ElMessage.success('已创建')
    ruleVisible.value = false
    loadRules()
  } finally {
    submitting.value = false
  }
}

function openThreshold(m) {
  Object.assign(thresholdForm, {
    code: m.code,
    name: m.name_cn,
    excellent_value: m.threshold?.excellent_value,
    warning_value: m.threshold?.warning_value,
    critical_value: m.threshold?.critical_value,
  })
  thresholdVisible.value = true
}

async function handleSaveThreshold() {
  if (thresholdForm.warning_value == null || thresholdForm.critical_value == null) {
    ElMessage.warning('预警线/告警线必填')
    return
  }
  submitting.value = true
  try {
    await updateThresholds(thresholdForm.code, {
      excellent_value: thresholdForm.excellent_value,
      warning_value: thresholdForm.warning_value,
      critical_value: thresholdForm.critical_value,
    })
    ElMessage.success('阈值已更新')
    thresholdVisible.value = false
    loadMetrics()
  } finally {
    submitting.value = false
  }
}

function openChannel() {
  Object.assign(channelForm, { code: '', name_cn: '', channel_type: 'webhook', webhook_url: '', description: '' })
  channelVisible.value = true
}

async function handleCreateChannel() {
  if (!channelForm.code.trim() || !channelForm.name_cn.trim()) {
    ElMessage.warning('code 与通道名必填')
    return
  }
  submitting.value = true
  try {
    await createChannel({
      code: channelForm.code,
      name_cn: channelForm.name_cn,
      channel_type: channelForm.channel_type,
      config: { webhook_url: channelForm.webhook_url },
      description: channelForm.description,
    })
    ElMessage.success('已创建')
    channelVisible.value = false
    loadChannels()
  } finally {
    submitting.value = false
  }
}

async function handleTestChannel(row) {
  const res = await testChannel(row.code, { title: `测试通知(${row.name_cn})` })
  if (res.data?.ok) {
    ElMessage.success(`测试发送成功: ${res.data.detail}`)
  } else {
    ElMessage.warning(`发送失败: ${res.data?.detail || ''}`)
  }
}

async function handleDeleteChannel(row) {
  await ElMessageBox.confirm(`确认删除通道「${row.name_cn}」?`, '提示', { type: 'warning' })
  await deleteChannel(row.id)
  ElMessage.success('已删除')
  loadChannels()
}

async function setStatus(row, status) {
  await setAlertStatus(row.id, { status })
  ElMessage.success('已更新')
  loadAlertsData()
}

async function handleScan() {
  const res = await scanAlerts()
  ElMessage.success(`扫描完成, 生成 ${res.data.created} 条预警`)
  loadAll()
}

function loadAll() {
  loadMetrics()
  loadRules()
  loadAlertsData()
  loadChannels()
}

onMounted(loadAll)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.mb-12 { margin-bottom: 12px; }
.mt-8 { margin-top: 8px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.metric-card { min-height: 148px; }
.metric-name { font-weight: 600; font-size: 14px; }
.metric-value-row { display: flex; align-items: baseline; gap: 4px; margin: 8px 0 4px; }
.metric-value { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }
.metric-unit { color: #909399; font-size: 12px; }
.metric-data-warn { color: #e6a23c; font-size: 11px; }
.metric-thr { color: #6b7280; font-size: 12px; }
.metric-dir { color: #9ca3af; font-size: 11px; }
.thr-hint { color: #909399; font-size: 12px; line-height: 1.6; }
</style>
