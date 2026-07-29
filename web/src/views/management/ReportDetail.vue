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
        <template v-if="isDraft">
          <el-button :icon="Files" @click="openTemplateDialog">套用模板</el-button>
          <el-button v-if="hasFramework" :icon="DocumentChecked" :loading="savingFramework" @click="handleSaveFramework">保存看板</el-button>
          <el-button type="success" :icon="Check" @click="openConfirm">确认固化</el-button>
        </template>
        <el-button v-if="report.status === 'confirmed'" type="warning" :icon="FolderOpened" @click="handleArchive">归档</el-button>
      </div>
    </div>

    <!-- 看板框架(可编辑看板) -->
    <el-card v-if="hasFramework || isDraft" class="mb-16">
      <template #header>
        <div class="flex-between">
          <span>看板框架({{ (report.framework || []).length }}项)<span class="muted" style="margin-left: 8px">{{ isDraft ? '· 可增删/排序/套用模板' : '· 已固化' }}</span></span>
          <el-button v-if="isDraft" :icon="Plus" type="primary" plain size="small" @click="addSection">添加分项</el-button>
        </div>
      </template>

      <div v-if="!hasFramework" class="muted" style="padding: 12px 0">
        暂无看板框架,可点击"套用模板"从模板实例化,或"添加分项"手动构建。也可使用下方纯快照模式。
      </div>

      <el-card v-for="(section, idx) in report.framework || []" :key="section.key" class="section-card" shadow="hover">
        <template #header>
          <div class="section-header">
            <span class="section-index">{{ idx + 1 }}</span>
            <el-input v-if="isDraft" v-model="section.title" size="small" style="width: 220px" placeholder="分项标题" />
            <span v-else class="section-title">{{ section.title }}</span>
            <el-tag size="small" :type="sectionTypeTag(section.section_type)" effect="plain" style="margin-left: 8px">{{ sectionTypeLabel(section.section_type) }}</el-tag>
            <div v-if="isDraft" class="section-actions">
              <el-tooltip content="上移" placement="top"><el-button :icon="ArrowUp" text size="small" :disabled="idx === 0" @click="moveSection(idx, -1)" /></el-tooltip>
              <el-tooltip content="下移" placement="top"><el-button :icon="ArrowDown" text size="small" :disabled="idx === (report.framework || []).length - 1" @click="moveSection(idx, 1)" /></el-tooltip>
              <el-tooltip content="删除" placement="top"><el-button :icon="Delete" text size="small" type="danger" @click="removeSection(idx)" /></el-tooltip>
            </div>
          </div>
        </template>

        <el-form label-width="100px" :disabled="!isDraft">
          <el-form-item v-if="section.section_type === 'view'" label="关联视图">
            <el-select v-if="isDraft" v-model="section.view_ids" multiple filterable collapse-tags collapse-tags-tooltip placeholder="选择分析视图(可多选)" style="width: 100%">
              <el-option v-for="v in viewOptions" :key="v.id" :label="v.name" :value="v.id" />
            </el-select>
            <div v-else>
              <el-tag v-for="id in section.view_ids" :key="id" size="small" style="margin-right: 4px">{{ viewNameMap[id] || `视图 #${id}` }}</el-tag>
              <span v-if="!section.view_ids?.length" class="muted">（无）</span>
            </div>
          </el-form-item>
          <el-form-item label="负责人">
            <el-select v-if="isDraft" v-model="section.owner_id" clearable filterable placeholder="负责人" style="width: 240px">
              <el-option v-for="u in userOptions" :key="u.id" :label="u.display_name" :value="u.id" />
            </el-select>
            <span v-else>{{ userNameMap[section.owner_id] || '-' }}</span>
          </el-form-item>
          <el-form-item label="跟进摘要">
            <el-input v-if="isDraft" v-model="section.summary" type="textarea" :rows="2" placeholder="该项审查摘要/关键数据/结论" />
            <div v-else class="section-text">{{ section.summary || '（无）' }}</div>
          </el-form-item>
        </el-form>

        <!-- 视图渲染区 -->
        <div v-if="section.section_type === 'view' && section.view_ids && section.view_ids.length" class="view-render-area">
          <template v-for="viewId in section.view_ids" :key="`${section.key}-${viewId}`">
            <ViewItemCard
              v-if="isDraft"
              :view-id="viewId"
              :title="`${section.title} - ${viewNameMap[viewId] || `视图 #${viewId}`}`"
              :start="timeStartStr"
              :end="timeEndStr"
            />
            <ViewItemCard
              v-else
              :view-id="viewId"
              :title="`${section.title} - ${viewNameMap[viewId] || `视图 #${viewId}`}`"
              :prefetched-result="section.data && section.data[String(viewId)]"
              :prefetched-view="{ name: viewNameMap[viewId] || `视图 #${viewId}` }"
            />
          </template>
        </div>
      </el-card>
    </el-card>

    <!-- 快照(纯快照模式或已固化的派生快照) -->
    <el-card v-if="!hasFramework || !isDraft" class="mb-16" header="快照(视图配置+关键结果)">
      <div v-if="isDraft && !hasFramework">
        <el-alert type="info" :closable="false" class="mb-16" show-icon>
          草稿阶段(纯快照模式):请填入关键结果快照(视图配置+执行结果JSON),确认后固化,不再随源数据变。或上方"套用模板"启用看板模式。
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
      <template v-if="hasFramework">
        <el-alert type="info" :closable="false" class="mb-16" show-icon>
          看板模式:系统将执行所有关联视图并固化各分项快照,自动派生轻量快照摘要。
        </el-alert>
      </template>
      <el-form v-else label-width="100px">
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

    <!-- 套用模板 dialog -->
    <el-dialog v-model="templateDialogVisible" title="套用模板(替换当前框架)" width="560px">
      <el-alert type="warning" :closable="false" class="mb-16" show-icon>
        套用模板会用模板框架替换当前看板分项(当前分项内容将被覆盖)。
      </el-alert>
      <el-form label-width="100px">
        <el-form-item label="选择模板">
          <el-select v-model="templateForm.templateId" placeholder="选择看板模板" filterable style="width: 100%">
            <el-option v-for="t in templateOptions" :key="t.id" :label="t.name" :value="t.id">
              <span>{{ t.name }}</span>
              <el-tag v-if="t.is_default" size="small" type="success" style="margin-left: 6px">默认</el-tag>
              <el-tag v-if="t.is_preset" size="small" type="warning" style="margin-left: 6px">预设</el-tag>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <div class="page-sub mb-16">模板分项会实例化为带空摘要/数据的看板分项,可继续编辑。</div>
      <template #footer>
        <el-button @click="templateDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="templateSubmitting" @click="handleApplyTemplate">套用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, ArrowLeft, ArrowUp, ArrowDown, Delete, Check, FolderOpened, Files, DocumentChecked } from '@element-plus/icons-vue'
import {
  getReport,
  updateReport,
  confirmReport,
  archiveReport,
  listAuditNotes,
  addAuditNote,
  deleteAuditNote,
  uploadAuditAttachment,
} from '@/api/report'
import { listReportTemplates, instantiateTemplate } from '@/api/report-template'
import { listAnalysisViews, executeAnalysisView } from '@/api/analysis-view'
import { listUsers } from '@/api/user'
import ViewItemCard from '@/components/ViewItemCard.vue'
import ReportCommentTimeline from '@/components/ReportCommentTimeline.vue'
import dayjs from 'dayjs'

const route = useRoute()
const reportId = Number(route.params.id)
const report = ref({})
const notes = ref([])
const userOptions = ref([])
const viewOptions = ref([])
const templateOptions = ref([])
const loading = ref(false)
const confirmVisible = ref(false)
const noteVisible = ref(false)
const submitting = ref(false)
const savingFramework = ref(false)
const snapshotText = ref('')
const noteForm = reactive(emptyNoteForm())

const userNameMap = computed(() => Object.fromEntries(userOptions.value.map((u) => [u.id, u.display_name])))
const viewNameMap = computed(() => Object.fromEntries(viewOptions.value.map((v) => [v.id, v.name])))
const formatDate = (t) => (t ? dayjs(t).format('YYYY-MM-DD') : '-')
const isDraft = computed(() => report.value.status === 'draft')
const hasFramework = computed(() => Array.isArray(report.value.framework) && report.value.framework.length > 0)
const timeStartStr = computed(() => (report.value.time_start ? dayjs(report.value.time_start).format('YYYY-MM-DD') : ''))
const timeEndStr = computed(() => (report.value.time_end ? dayjs(report.value.time_end).format('YYYY-MM-DD') : ''))

const typeMap = { weekly: '周报', special_audit: '专项审计', custom: '自定义' }
const statusMap = { draft: '草稿', confirmed: '已确认', archived: '已归档' }
const statusType = (s) => ({ draft: 'info', confirmed: 'success', archived: 'warning' }[s] || 'info')
const judgmentMap = { pass: '通过', issue: '有问题', improve: '需改进' }
const judgmentType = (j) => ({ pass: 'success', issue: 'danger', improve: 'warning' }[j] || 'info')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const sectionTypeLabel = (t) => ({ view: '视图', measures: '措施', incidents: '异常', text: '文字' }[t] || t)
const sectionTypeTag = (t) => ({ view: 'primary', measures: 'warning', incidents: 'danger', text: 'info' }[t] || 'info')
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
  // 兼容旧数据: 确保 framework 各 section 有数组字段
  for (const s of report.value.framework || []) {
    if (!Array.isArray(s.view_ids)) s.view_ids = []
    if (!Array.isArray(s.related_report_ids)) s.related_report_ids = []
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

async function loadViewOptions() {
  const res = await listAnalysisViews({ page: 1, page_size: 100 })
  viewOptions.value = res.data.items || []
}

async function loadTemplateOptions() {
  try {
    const res = await listReportTemplates({ page: 1, page_size: 100 })
    templateOptions.value = res.data.items || []
  } catch (e) {
    templateOptions.value = []
  }
}

// ---------- 看板框架编辑 ----------
const templateDialogVisible = ref(false)
const templateSubmitting = ref(false)
const templateForm = reactive({ templateId: null })

function openTemplateDialog() {
  templateForm.templateId = null
  templateDialogVisible.value = true
}

async function handleApplyTemplate() {
  if (!templateForm.templateId) {
    ElMessage.warning('请选择模板')
    return
  }
  templateSubmitting.value = true
  try {
    const res = await instantiateTemplate(templateForm.templateId)
    report.value.framework = res.data || []
    // 立即保存到后端
    await updateReport(reportId, { framework: report.value.framework })
    templateDialogVisible.value = false
    ElMessage.success('已套用模板并保存')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '套用失败')
  } finally {
    templateSubmitting.value = false
  }
}

function addSection() {
  if (!Array.isArray(report.value.framework)) report.value.framework = []
  report.value.framework.push({
    key: `section_${Date.now()}`,
    title: '新分项',
    section_type: 'view',
    view_ids: [],
    owner_id: null,
    summary: '',
    data: null,
    related_report_ids: [],
  })
}

async function removeSection(idx) {
  try {
    await ElMessageBox.confirm(`确认删除分项[${report.value.framework[idx].title}]?`, '删除分项', { type: 'warning' })
  } catch {
    return
  }
  report.value.framework.splice(idx, 1)
}

function moveSection(idx, direction) {
  const arr = report.value.framework
  const target = idx + direction
  if (target < 0 || target >= arr.length) return
  const tmp = arr[idx]
  arr[idx] = arr[target]
  arr[target] = tmp
  report.value.framework = [...arr]
}

function buildFrameworkPayload(skipData = false) {
  return (report.value.framework || []).map((s) => ({
    key: s.key,
    title: s.title,
    section_type: s.section_type,
    view_ids: s.view_ids || [],
    owner_id: s.owner_id || null,
    summary: s.summary || '',
    data: skipData ? s.data : (s.data ?? null),
    related_report_ids: s.related_report_ids || [],
  }))
}

async function handleSaveFramework() {
  savingFramework.value = true
  try {
    await updateReport(reportId, { framework: buildFrameworkPayload() })
    ElMessage.success('看板框架已保存')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingFramework.value = false
  }
}

async function fetchViewSnapshots() {
  const start = timeStartStr.value
  const end = timeEndStr.value
  for (const s of report.value.framework || []) {
    if (s.section_type === 'view' && s.view_ids && s.view_ids.length) {
      const dataObj = {}
      for (const viewId of s.view_ids) {
        try {
          const params = {}
          if (start) params.start = start
          if (end) params.end = end
          const res = await executeAnalysisView({ view_id: viewId, params })
          dataObj[String(viewId)] = res.data
        } catch (e) {
          dataObj[String(viewId)] = { error: e.response?.data?.detail || e.message || '执行失败' }
        }
      }
      s.data = dataObj
    }
  }
}

function openConfirm() {
  confirmVisible.value = true
}

async function handleConfirm() {
  // 看板模式: 固化各 section.data(执行视图拿快照) + 派生 snapshot; 纯快照模式: 解析 snapshotText
  if (hasFramework.value) {
    submitting.value = true
    try {
      await fetchViewSnapshots()
      const framework = buildFrameworkPayload()
      await updateReport(reportId, { framework })
      await confirmReport(reportId, { framework })
      ElMessage.success('已确认固化(看板快照已锁定)')
      confirmVisible.value = false
      loadReport()
      loadNotes()
    } catch (e) {
      ElMessage.error(e.response?.data?.detail || '确认失败')
    } finally {
      submitting.value = false
    }
    return
  }
  // 纯快照模式
  let snapshot
  try {
    snapshot = snapshotText.value.trim() ? JSON.parse(snapshotText.value) : null
  } catch (e) {
    ElMessage.error('快照JSON格式错误')
    return
  }
  if (!snapshot) {
    ElMessage.warning('请填入快照数据,或套用看板模板')
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
  loadViewOptions()
  loadTemplateOptions()
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
.section-card {
  margin-bottom: 12px;
}
.section-card :deep(.el-card__header) {
  padding: 10px 16px;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.section-text {
  font-size: 13px;
  color: #303133;
  line-height: 1.7;
  white-space: pre-wrap;
}
.section-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
}
.view-render-area {
  margin: 0 0 8px 0;
}
.muted {
  color: #909399;
  font-size: 13px;
}
</style>
