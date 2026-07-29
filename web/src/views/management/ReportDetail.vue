<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">{{ report.name || '报告详情' }}</div>
        <div class="page-sub">
          <el-tag size="small" effect="plain">{{ typeMap[report.report_type] || report.report_type }}</el-tag>
          <el-tag :type="statusType(report.status)" size="small" style="margin-left: 6px">{{ statusMap[report.status] || report.status }}</el-tag>
          <span style="margin-left: 8px">{{ formatTime(report.time_start) }} ~ {{ formatTime(report.time_end) }}</span>
          <span v-if="report.assignee_id" style="margin-left: 8px; color: #909399">· 负责人 {{ userNameMap[report.assignee_id] || report.assignee_id }}</span>
          <span v-if="report.due_date" style="margin-left: 8px; color: #909399">· 期限 {{ formatDate(report.due_date) }}</span>
          <el-link v-if="report.source_weekly_id" type="primary" :underline="false" style="margin-left: 8px" @click="$router.push(`/management/weekly/${report.source_weekly_id}`)">
            · 来源周报看板 #{{ report.source_weekly_id }}
          </el-link>
        </div>
      </div>
      <div class="flex gap-8">
        <el-button :icon="ArrowLeft" @click="$router.push('/management/reports')">返回</el-button>
        <el-button v-if="report.status === 'draft'" type="success" :icon="Check" @click="openConfirm">确认固化</el-button>
        <el-button v-if="report.status === 'confirmed'" type="warning" :icon="FolderOpened" @click="handleArchive">归档</el-button>
      </div>
    </div>

    <el-card class="mb-16" header="快照(视图配置+关键结果)">
      <div v-if="report.status === 'draft'">
        <el-alert type="info" :closable="false" class="mb-16" show-icon>
          草稿阶段:请填入关键结果快照(视图配置+执行结果JSON),确认后固化,不再随源数据变。
        </el-alert>
        <el-input v-model="snapshotText" type="textarea" :rows="10" placeholder='快照JSON,如 {"views":[...],"results":[...]}' />
      </div>
      <div v-else>
        <pre v-if="report.snapshot" class="snapshot-pre">{{ formatJson(report.snapshot) }}</pre>
        <el-empty v-else description="无快照数据" :image-size="60" />
      </div>
    </el-card>

    <!-- 审计附注 -->
    <el-card>
      <template #header>
        <div class="flex-between">
          <span>审计附注 ({{ notes.length }})</span>
          <el-button v-if="report.status !== 'draft'" type="primary" :icon="Plus" @click="openAddNote">添加附注</el-button>
        </div>
      </template>
      <el-alert v-if="report.status === 'draft'" type="warning" :closable="false" show-icon>
        草稿报告不可审计,请先确认固化后再添加审计附注("只有报告才能审计")。
      </el-alert>

      <div v-for="note in notes" :key="note.id" class="note-item">
        <div class="note-header">
          <el-tag :type="judgmentType(note.judgment)" size="small">{{ judgmentMap[note.judgment] || '未评判' }}</el-tag>
          <span class="note-author">{{ note.author_name || '匿名' }} · {{ formatTime(note.created_at) }}</span>
          <el-button link type="danger" @click="handleDeleteNote(note)">删除</el-button>
        </div>
        <div v-if="note.conclusion" class="note-conclusion">{{ note.conclusion }}</div>
        <div v-if="note.sampled_tickets && note.sampled_tickets.length" class="note-tickets">
          <span class="meta-label">抽检工单:</span>
          <el-tag v-for="t in note.sampled_tickets" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
        </div>
        <div v-if="note.attachments && note.attachments.length" class="note-attachments">
          <span class="meta-label">贴图:</span>
          <el-image
            v-for="(att, i) in note.attachments"
            :key="i"
            :src="att.url"
            :preview-src-list="note.attachments.map((a) => a.url)"
            :initial-index="i"
            fit="cover"
            class="att-img"
          />
        </div>
        <div v-if="note.output_measure_id" class="note-measure">
          <el-tag type="success" size="small">已派生措施 #{{ note.output_measure_id }}</el-tag>
        </div>
      </div>
      <el-empty v-if="!notes.length && report.status !== 'draft'" description="暂无审计附注" :image-size="60" />
    </el-card>

    <!-- 跟进注释(轻量跟进, 区别于正式审计附注) -->
    <el-card class="mt-16">
      <template #header>跟进注释</template>
      <ReportCommentTimeline
        target-type="report"
        :target-id="reportId"
        :user-options="userOptions"
        placeholder="添加跟进注释(审查进展/补充说明/后续安排)..."
      />
    </el-card>

    <!-- 确认固化对话框 -->
    <el-dialog v-model="confirmVisible" title="确认报告(固化快照)" width="560px">
      <el-alert type="warning" :closable="false" class="mb-16" show-icon>
        确认后快照固化,状态变为「已确认」,可添加审计附注。已确认后不可再编辑。
      </el-alert>
      <el-form label-width="100px">
        <el-form-item label="快照数据" required>
          <el-input v-model="snapshotText" type="textarea" :rows="8" placeholder="请填入关键结果快照JSON" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button type="success" :loading="submitting" @click="handleConfirm">确认固化</el-button>
      </template>
    </el-dialog>

    <!-- 添加审计附注对话框 -->
    <el-dialog v-model="noteVisible" title="添加审计附注" width="680px">
      <el-form :model="noteForm" label-width="100px">
        <el-form-item label="评判">
          <el-radio-group v-model="noteForm.judgment">
            <el-radio value="pass">通过</el-radio>
            <el-radio value="issue">有问题</el-radio>
            <el-radio value="improve">需改进</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="审计结论">
          <el-input v-model="noteForm.conclusion" type="textarea" :rows="3" placeholder="归因不归责:结论用于改进" />
        </el-form-item>
        <el-form-item label="抽检工单">
          <el-input v-model="noteForm.ticketsInput" placeholder="多个工单号用逗号分隔" />
        </el-form-item>
        <el-form-item label="贴图附件">
          <el-upload
            :file-list="noteForm.fileList"
            :auto-upload="true"
            :http-request="handleUpload"
            :before-upload="beforeUpload"
            list-type="picture-card"
            accept="image/*"
            :on-remove="handleRemoveAttach"
          >
            <el-icon><Plus /></el-icon>
          </el-upload>
          <div class="page-sub">上传聊天记录截图等(≤10MB,支持图片)</div>
        </el-form-item>
        <el-form-item label="派生措施">
          <el-switch v-model="noteForm.createMeasure" /> 启用则同时创建管理措施(审计→措施闭环)
        </el-form-item>
        <template v-if="noteForm.createMeasure">
          <el-form-item label="措施标题" required>
            <el-input v-model="noteForm.measureTitle" />
          </el-form-item>
          <el-form-item label="预期目标">
            <el-input v-model="noteForm.expectedGoal" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="验证时间">
            <el-date-picker v-model="noteForm.verifyTime" type="datetime" style="width: 100%" />
          </el-form-item>
          <el-form-item label="验证方式">
            <el-input v-model="noteForm.verifyMethod" type="textarea" :rows="2" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="noteVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleAddNote">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ArrowLeft, Check, FolderOpened } from '@element-plus/icons-vue'
import {
  getReport,
  confirmReport,
  archiveReport,
  listAuditNotes,
  addAuditNote,
  deleteAuditNote,
  uploadAuditAttachment,
} from '@/api/report'
import { listUsers } from '@/api/user'
import ReportCommentTimeline from '@/components/ReportCommentTimeline.vue'
import dayjs from 'dayjs'

const route = useRoute()
const reportId = Number(route.params.id)
const report = ref({})
const notes = ref([])
const userOptions = ref([])
const loading = ref(false)
const confirmVisible = ref(false)
const noteVisible = ref(false)
const submitting = ref(false)
const snapshotText = ref('')
const noteForm = reactive(emptyNoteForm())

const userNameMap = computed(() => Object.fromEntries(userOptions.value.map((u) => [u.id, u.display_name])))
const formatDate = (t) => (t ? dayjs(t).format('YYYY-MM-DD') : '-')

const typeMap = { weekly: '周报', special_audit: '专项审计', custom: '自定义' }
const statusMap = { draft: '草稿', confirmed: '已确认', archived: '已归档' }
const statusType = (s) => ({ draft: 'info', confirmed: 'success', archived: 'warning' }[s] || 'info')
const judgmentMap = { pass: '通过', issue: '有问题', improve: '需改进' }
const judgmentType = (j) => ({ pass: 'success', issue: 'danger', improve: 'warning' }[j] || 'info')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const formatJson = (obj) => {
  try {
    return typeof obj === 'string' ? JSON.stringify(JSON.parse(obj), null, 2) : JSON.stringify(obj, null, 2)
  } catch (e) {
    return String(obj)
  }
}

function emptyNoteForm() {
  return {
    judgment: 'pass',
    conclusion: '',
    ticketsInput: '',
    fileList: [],
    attachments: [],
    createMeasure: false,
    measureTitle: '',
    expectedGoal: '',
    verifyTime: null,
    verifyMethod: '',
  }
}

async function loadReport() {
  const res = await getReport(reportId)
  report.value = res.data
  if (res.data.snapshot) {
    snapshotText.value = typeof res.data.snapshot === 'string' ? res.data.snapshot : JSON.stringify(res.data.snapshot, null, 2)
  }
}

async function loadNotes() {
  const res = await listAuditNotes(reportId)
  notes.value = res.data
}

async function loadUsers() {
  const res = await listUsers({ page: 1, page_size: 100 })
  userOptions.value = res.data.items || []
}

function openConfirm() {
  confirmVisible.value = true
}

async function handleConfirm() {
  let snapshot
  try {
    snapshot = snapshotText.value.trim() ? JSON.parse(snapshotText.value) : null
  } catch (e) {
    ElMessage.error('快照JSON格式错误')
    return
  }
  if (!snapshot) {
    ElMessage.warning('请填入快照数据')
    return
  }
  submitting.value = true
  try {
    await confirmReport(reportId, { snapshot })
    ElMessage.success('已确认固化')
    confirmVisible.value = false
    loadReport()
    loadNotes()
  } finally {
    submitting.value = false
  }
}

async function handleArchive() {
  await ElMessageBox.confirm('确认归档该报告?', '提示', { type: 'warning' })
  await archiveReport(reportId)
  ElMessage.success('已归档')
  loadReport()
}

function openAddNote() {
  Object.assign(noteForm, emptyNoteForm())
  noteVisible.value = true
}

function beforeUpload(file) {
  const isImage = file.type.startsWith('image/')
  if (!isImage) {
    ElMessage.error('仅支持图片格式')
    return false
  }
  if (file.size > 10 * 1024 * 1024) {
    ElMessage.error('图片大小不能超过10MB')
    return false
  }
  return true
}

async function handleUpload({ file, onSuccess, onError }) {
  try {
    const res = await uploadAuditAttachment(file, reportId)
    noteForm.attachments.push({ path: res.data.path, url: res.data.url, name: file.name })
    onSuccess && onSuccess(res)
  } catch (e) {
    onError && onError(e)
  }
}

function handleRemoveAttach(file) {
  const idx = noteForm.attachments.findIndex((a) => a.name === file.name || a.url === file.url)
  if (idx >= 0) noteForm.attachments.splice(idx, 1)
}

async function handleAddNote() {
  if (noteForm.createMeasure && !noteForm.measureTitle.trim()) {
    ElMessage.warning('请输入派生措施标题')
    return
  }
  submitting.value = true
  try {
    const tickets = noteForm.ticketsInput
      ? noteForm.ticketsInput.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean)
      : []
    const payload = {
      judgment: noteForm.judgment,
      conclusion: noteForm.conclusion || null,
      sampled_tickets: tickets.length ? tickets : null,
      attachments: noteForm.attachments.length ? noteForm.attachments : null,
    }
    if (noteForm.createMeasure) {
      payload.create_measure = {
        title: noteForm.measureTitle,
        expected_goal: noteForm.expectedGoal || null,
        verify_time: noteForm.verifyTime ? dayjs(noteForm.verifyTime).toISOString() : null,
        verify_method: noteForm.verifyMethod || null,
      }
    }
    await addAuditNote(reportId, payload)
    ElMessage.success('附注已添加')
    noteVisible.value = false
    loadNotes()
  } finally {
    submitting.value = false
  }
}

async function handleDeleteNote(note) {
  await ElMessageBox.confirm('确认删除该审计附注?', '提示', { type: 'warning' })
  await deleteAuditNote(reportId, note.id)
  ElMessage.success('已删除')
  loadNotes()
}

onMounted(() => {
  loadReport()
  loadNotes()
  loadUsers()
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
.snapshot-pre {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  max-height: 400px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.note-item {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 12px;
}
.note-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.note-author {
  color: #909399;
  font-size: 12px;
  flex: 1;
}
.note-conclusion {
  font-size: 13px;
  color: #303133;
  line-height: 1.6;
  margin-bottom: 8px;
  white-space: pre-wrap;
}
.note-tickets,
.note-attachments {
  margin-top: 6px;
  font-size: 12px;
}
.meta-label {
  color: #909399;
  margin-right: 6px;
}
.att-img {
  width: 80px;
  height: 80px;
  border-radius: 4px;
  margin: 4px 6px 0 0;
  border: 1px solid #ebeef5;
}
.note-measure {
  margin-top: 8px;
}
</style>
