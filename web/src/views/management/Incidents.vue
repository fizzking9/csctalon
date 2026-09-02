<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">影响事件登记</div>
        <div class="page-sub">未通知的非预期事项导致客服负荷激增 · 登记用于改进不用于问责 · 复盘后转预期事项提前部署</div>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">登记异常</el-button>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="resetAndLoad">
          <el-option v-for="(label, k) in statusMap" :key="k" :label="label" :value="k" />
        </el-select>
        <el-date-picker
          v-model="filter.range"
          type="daterange"
          range-separator="至"
          start-placeholder="发生起"
          end-placeholder="发生止"
          value-format="YYYY-MM-DD"
          style="width: 280px"
          @change="resetAndLoad"
        />
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe style="cursor: pointer" @row-click="openDetail">
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">{{ statusMap[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="事项名" show-overflow-tooltip />
        <el-table-column prop="subject" label="主体" width="140" show-overflow-tooltip />
        <el-table-column label="发生时间" width="160">
          <template #default="{ row }">{{ formatTime(row.occurred_at) }}</template>
        </el-table-column>
        <el-table-column label="持续" width="90">
          <template #default="{ row }">{{ row.duration_minutes ? row.duration_minutes + '分' : '-' }}</template>
        </el-table-column>
        <el-table-column label="转预期" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.converted_to_expected" type="success" size="small">已转</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="登记人" width="100" />
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button v-if="!row.converted_to_expected" link type="primary" @click.stop="openConvert(row)">转预期</el-button>
            <el-button link type="danger" @click.stop="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 登记异常对话框 -->
    <el-dialog v-model="dialogVisible" title="登记影响事件" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="事项名" required>
          <el-input v-model="form.title" placeholder="如 XX系统异常导致工单激增" />
        </el-form-item>
        <el-form-item label="主体">
          <el-input v-model="form.subject" placeholder="涉及的业务/设备/系统" />
        </el-form-item>
        <el-form-item label="发生时间">
          <el-date-picker v-model="form.occurred_at" type="datetime" placeholder="选择发生时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="持续时间">
          <el-input-number v-model="form.duration_minutes" :min="0" :step="5" controls-position="right" /> 分钟
        </el-form-item>
        <el-form-item label="影响">
          <el-input v-model="form.impact" type="textarea" :rows="2" placeholder="量化:工单增量/涉及金额/影响人数" />
        </el-form-item>
        <el-form-item label="来源/类型">
          <el-select v-model="form.category" placeholder="选择来源部门" clearable style="width: 100%">
            <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="涉及部门">
          <el-input v-model="form.department" placeholder="涉及部门(单选)" />
        </el-form-item>
        <el-form-item label="严重程度">
          <el-radio-group v-model="form.severity">
            <el-radio-button label="高" />
            <el-radio-button label="中" />
            <el-radio-button label="低" />
          </el-radio-group>
        </el-form-item>
        <el-form-item label="明细描述">
          <el-input v-model="form.detail" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="关联工单号">
          <el-input v-model="form.ticketsInput" placeholder="多个工单号用逗号分隔" />
        </el-form-item>
        <el-form-item label="周期性事件">
          <el-switch v-model="form.is_recurring" active-text="是" inactive-text="否" />
          <span class="form-hint">广告推送/定期维护等阶段性影响事件,每日生效时在首页突出显示</span>
        </el-form-item>
        <template v-if="form.is_recurring">
          <el-form-item label="周期模式">
            <el-select v-model="form.recurrence_pattern" style="width: 100%">
              <el-option label="每日(daily)" value="daily" />
              <el-option label="工作日(周一~周五)" value="weekday" />
              <el-option label="每周(同周几)" value="weekly" />
              <el-option label="每月(同几号)" value="monthly" />
            </el-select>
          </el-form-item>
          <el-form-item label="生效起止">
            <el-date-picker
              v-model="form.valid_from"
              type="datetime"
              placeholder="生效起始(含)"
              style="width: 48%"
            />
            <span style="margin: 0 4px; color: #909399">~</span>
            <el-date-picker
              v-model="form.valid_to"
              type="datetime"
              placeholder="结束(空=无限期)"
              style="width: 48%"
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">登记</el-button>
      </template>
    </el-dialog>

    <!-- 详情/复盘对话框 -->
    <el-dialog v-model="detailVisible" title="影响事件详情" width="640px">
      <el-descriptions v-if="current" :column="1" border>
        <el-descriptions-item label="事项名">{{ current.title }}</el-descriptions-item>
        <el-descriptions-item label="主体">{{ current.subject || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusType(current.status)" size="small">{{ statusMap[current.status] }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="发生时间">{{ formatTime(current.occurred_at) }}</el-descriptions-item>
        <el-descriptions-item label="持续">{{ current.duration_minutes ? current.duration_minutes + ' 分钟' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="影响">{{ current.impact || '-' }}</el-descriptions-item>
        <el-descriptions-item label="明细">{{ current.detail || '-' }}</el-descriptions-item>
        <el-descriptions-item label="关联工单">{{ (current.related_tickets || []).join('、') || '-' }}</el-descriptions-item>
        <el-descriptions-item label="周期性">
          <template v-if="current.is_recurring">
            <el-tag type="warning" size="small" effect="dark">周期</el-tag>
            <span style="margin-left:6px">{{ recurrenceLabel(current.recurrence_pattern) }}</span>
            <span v-if="current.valid_from" style="margin-left:6px;color:#909399">{{ formatTime(current.valid_from) }}{{ current.valid_to ? ' ~ ' + formatTime(current.valid_to) : ' ~ 无限期' }}</span>
          </template>
          <span v-else>否</span>
        </el-descriptions-item>
        <el-descriptions-item label="转预期">
          <el-tag v-if="current.converted_to_expected" type="success" size="small">已转预期</el-tag>
          <span v-else>否</span>
        </el-descriptions-item>
        <el-descriptions-item label="复盘结论">{{ current.review_note || '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider content-position="left">跟进记录</el-divider>
      <el-timeline v-if="followups.length" style="max-height: 240px; overflow-y: auto">
        <el-timeline-item
          v-for="f in followups"
          :key="f.id"
          :timestamp="formatTime(f.created_at)"
          :type="f.status_change ? 'primary' : ''"
        >
          <div>{{ f.content }}</div>
          <div v-if="f.status_change" class="followup-status">状态变更: {{ f.status_change }}</div>
          <div class="followup-author">{{ f.created_by || '-' }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无跟进记录" :image-size="40" />
      <div class="mt-8">
        <el-input v-model="followupInput" placeholder="一句话跟进(可选状态变更)" @keyup.enter="handleAddFollowup">
          <template #append>
            <el-button :loading="submitting" @click="handleAddFollowup">跟进</el-button>
          </template>
        </el-input>
      </div>
    </el-dialog>

    <!-- 转预期对话框 -->
    <el-dialog v-model="convertVisible" title="转为预期事项" width="520px">
      <el-alert type="info" :closable="false" class="mb-16" show-icon>
        复盘后转为预期事项,下次同类情况提前部署(异常→预防的闭环)。
      </el-alert>
      <el-form :model="convertForm" label-width="100px">
        <el-form-item label="复盘结论">
          <el-input v-model="convertForm.review_note" type="textarea" :rows="4" placeholder="复盘结论/后续部署建议" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="convertVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleConvert">转为预期</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listIncidents, createIncident, convertIncident, deleteIncident, listFollowups, addFollowup } from '@/api/incident'
import dayjs from 'dayjs'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ status: '', range: null })
const dialogVisible = ref(false)
const detailVisible = ref(false)
const convertVisible = ref(false)
const submitting = ref(false)
const current = ref(null)
const form = reactive(emptyForm())
const convertForm = reactive({ review_note: '' })
const followups = ref([])
const followupInput = ref('')

const statusMap = { registered: '登记', processing: '处理中', calmed: '已平息', reviewed: '已复盘' }
const statusType = (s) => ({ registered: 'danger', processing: 'warning', calmed: 'info', reviewed: 'success' }[s] || 'info')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const recurrenceLabel = (p) => ({ daily: '每日', weekday: '工作日', weekly: '每周', monthly: '每月' }[p] || (p || '每日'))
const categoryOptions = ['生产', '运营', '商务部', '店铺', '系统平台开发', '其他服务商']

function emptyForm() {
  return { title: '', subject: '', occurred_at: null, duration_minutes: null, impact: '', detail: '', ticketsInput: '', category: '', department: '', severity: '中', is_recurring: false, recurrence_pattern: 'daily', valid_from: null, valid_to: null }
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: 20, status: filter.status || undefined }
    if (filter.range && filter.range.length === 2) {
      params.start = filter.range[0]
      params.end = filter.range[1]
    }
    const res = await listIncidents(params)
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function resetAndLoad() {
  page.value = 1
  loadData()
}

function openCreate() {
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

async function handleCreate() {
  if (!form.title.trim()) {
    ElMessage.warning('请输入事项名')
    return
  }
  submitting.value = true
  try {
    const tickets = form.ticketsInput
      ? form.ticketsInput.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean)
      : []
    await createIncident({
      title: form.title,
      subject: form.subject || null,
      occurred_at: form.occurred_at ? dayjs(form.occurred_at).toISOString() : null,
      duration_minutes: form.duration_minutes || null,
      impact: form.impact || null,
      detail: form.detail || null,
      related_tickets: tickets.length ? tickets : null,
      category: form.category || null,
      department: form.department || null,
      severity: form.severity || '中',
      is_recurring: !!form.is_recurring,
      recurrence_pattern: form.is_recurring ? (form.recurrence_pattern || 'daily') : null,
      valid_from: form.is_recurring && form.valid_from ? dayjs(form.valid_from).toISOString() : null,
      valid_to: form.is_recurring && form.valid_to ? dayjs(form.valid_to).toISOString() : null,
    })
    ElMessage.success('登记成功')
    dialogVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function openDetail(row) {
  current.value = row
  followups.value = []
  followupInput.value = ''
  detailVisible.value = true
  try {
    const res = await listFollowups(row.id)
    followups.value = res.data || []
  } catch (e) {
    followups.value = []
  }
}

async function handleAddFollowup() {
  if (!followupInput.value.trim()) {
    ElMessage.warning('请输入跟进内容')
    return
  }
  submitting.value = true
  try {
    await addFollowup(current.value.id, { content: followupInput.value })
    followupInput.value = ''
    ElMessage.success('已跟进')
    const res = await listFollowups(current.value.id)
    followups.value = res.data || []
  } finally {
    submitting.value = false
  }
}

function openConvert(row) {
  current.value = row
  convertForm.review_note = row.review_note || ''
  convertVisible.value = true
}

async function handleConvert() {
  submitting.value = true
  try {
    await convertIncident(current.value.id, { review_note: convertForm.review_note })
    ElMessage.success('已转为预期事项')
    convertVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确认删除该影响事件?', '提示', { type: 'warning' })
  await deleteIncident(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(loadData)
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
.mt-8 {
  margin-top: 8px;
}
.followup-status {
  font-size: 12px;
  color: #409eff;
}
.followup-author {
  font-size: 12px;
  color: #909399;
}
.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: #c0c4cc;
}
</style>
