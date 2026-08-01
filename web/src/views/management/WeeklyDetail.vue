<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">{{ report.name || '周报看板详情' }}</div>
        <div class="page-sub">
          <el-tag :type="statusType(report.status)" size="small">{{ statusMap[report.status] || report.status }}</el-tag>
          <span style="margin-left: 8px">{{ formatDate(report.week_start) }} ~ {{ formatDate(report.week_end) }}</span>
          <span v-if="!isDraft" style="margin-left: 8px; color: #909399">· 已固化快照</span>
          <span v-if="report.confirmed_by" style="margin-left: 8px; color: #909399">· 确认人 {{ report.confirmed_by }}</span>
        </div>
      </div>
      <div class="flex gap-8">
        <el-button :icon="ArrowLeft" @click="$router.push('/management/weekly')">返回</el-button>
        <template v-if="isDraft">
          <el-button :icon="Files" @click="openTemplateDialog">套用模板</el-button>
          <el-button type="primary" :icon="DocumentChecked" :loading="saving" @click="handleSave">保存</el-button>
          <el-button type="success" :icon="Check" :loading="confirming" @click="handleConfirm">确认固化</el-button>
        </template>
        <el-button v-if="report.status === 'confirmed'" type="warning" :icon="FolderOpened" @click="handleArchive">归档</el-button>
      </div>
    </div>

    <!-- 周报总评 -->
    <el-card class="mb-16" header="周报总评">
      <el-input v-if="isDraft" v-model="report.summary" type="textarea" :rows="2" placeholder="本周总体情况概述" />
      <div v-else class="section-text">{{ report.summary || '（无）' }}</div>
    </el-card>

    <div class="framework-tip mb-16">
      可编辑看板框架({{ (report.framework || []).length }}项) · 视图型自动锁定本周时间范围({{ formatDate(report.week_start) }}~{{ formatDate(report.week_end) }})
      <span v-if="isDraft">· 可增删/排序分项/套用模板,确认后固化</span>
      <span v-else>· 已固化,后续跟进请在底部注释</span>
    </div>

    <!-- 10项框架 -->
    <el-card v-for="(section, idx) in report.framework" :key="section.key" class="section-card mb-16">
      <template #header>
        <div class="section-header">
          <span class="section-index">{{ idx + 1 }}</span>
          <el-input
            v-if="isDraft"
            v-model="section.title"
            size="small"
            style="width: 240px"
            placeholder="分项标题"
          />
          <span v-else class="section-title">{{ section.title }}</span>
          <el-select
            v-if="isDraft"
            v-model="section.section_type"
            size="small"
            style="width: 140px; margin-left: 8px"
            @change="onSectionTypeChange(section)"
          >
            <el-option v-for="opt in sectionTypeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <el-tag v-else size="small" :type="sectionTypeTag(section.section_type)" effect="plain" style="margin-left: 8px">
            {{ sectionTypeLabel(section.section_type) }}
          </el-tag>
          <span v-if="section.owner_id && !isDraft" class="muted" style="margin-left: auto">
            负责人: {{ userNameMap[section.owner_id] || '-' }}
          </span>
          <div v-if="isDraft" class="section-actions">
            <el-tooltip content="上移" placement="top">
              <el-button :icon="ArrowUp" text size="small" :disabled="idx === 0" @click="moveSection(idx, -1)" />
            </el-tooltip>
            <el-tooltip content="下移" placement="top">
              <el-button :icon="ArrowDown" text size="small" :disabled="idx === (report.framework || []).length - 1" @click="moveSection(idx, 1)" />
            </el-tooltip>
            <el-tooltip content="删除分项" placement="top">
              <el-button :icon="Delete" text size="small" type="danger" @click="removeSection(idx)" />
            </el-tooltip>
          </div>
        </div>
      </template>

      <!-- view 型: 关联多视图 + 负责人 + 渲染区 -->
      <template v-if="section.section_type === 'view'">
        <el-form label-width="100px">
          <el-form-item label="关联视图">
            <el-select
              v-if="isDraft"
              v-model="section.view_ids"
              multiple
              placeholder="选择本周数据视图(可多选/动态增删)"
              filterable
              style="width: 480px"
            >
              <el-option v-for="v in viewOptions" :key="v.id" :label="v.name" :value="v.id">
                <span>{{ v.name }}</span>
                <el-tag v-if="v.is_preset" size="small" type="warning" style="margin-left: 6px">预设</el-tag>
              </el-option>
            </el-select>
            <div v-else>
              <el-tag v-for="id in section.view_ids" :key="id" size="small" style="margin-right: 4px">
                {{ viewNameMap[id] || `视图 #${id}` }}
              </el-tag>
              <span v-if="!section.view_ids?.length" class="muted">（无）</span>
            </div>
          </el-form-item>
          <el-form-item label="负责人">
            <el-select v-if="isDraft" v-model="section.owner_id" placeholder="选择负责人" clearable filterable style="width: 240px">
              <el-option v-for="u in userOptions" :key="u.id" :label="u.display_name" :value="u.id" />
            </el-select>
            <span v-else>{{ userNameMap[section.owner_id] || '-' }}</span>
          </el-form-item>
          <el-form-item label="跟进摘要">
            <el-input
              v-if="isDraft"
              v-model="section.summary"
              type="textarea"
              :rows="2"
              placeholder="会前预填或会上共同填写本周该项情况/关键数据/结论"
            />
            <div v-else class="section-text">{{ section.summary || '（无）' }}</div>
          </el-form-item>
        </el-form>

        <!-- 多视图渲染区 -->
        <div v-if="section.view_ids && section.view_ids.length" class="view-render-area">
          <template v-for="viewId in section.view_ids" :key="`${section.key}-${viewId}`">
            <!-- 草稿态: 实时执行(本周时间) -->
            <ViewItemCard
              v-if="isDraft"
              :view-id="viewId"
              :title="`${section.title} - ${viewNameMap[viewId] || `视图 #${viewId}`}`"
              :start="weekStartStr"
              :end="weekEndStr"
            />
            <!-- 确认后: 用固化的 data[viewId] 快照渲染 -->
            <ViewItemCard
              v-else
              :view-id="viewId"
              :title="`${section.title} - ${viewNameMap[viewId] || `视图 #${viewId}`}`"
              :prefetched-result="section.data && section.data[String(viewId)]"
              :prefetched-view="{ name: viewNameMap[viewId] || `视图 #${viewId}` }"
            />
          </template>
        </div>
        <el-alert v-else type="info" :closable="false" show-icon class="mb-16">
          <template #title>未关联视图</template>
          <span v-if="isDraft">请在上方选择分析视图(可多选),系统将自动按本周时间范围执行渲染。</span>
          <span v-else>该分项未关联视图。</span>
        </el-alert>
      </template>

      <!-- measures / incidents / text 型: 摘要为主 -->
      <template v-else>
        <el-form label-width="100px">
          <el-form-item label="负责人">
            <el-select v-if="isDraft" v-model="section.owner_id" placeholder="选择负责人" clearable filterable style="width: 240px">
              <el-option v-for="u in userOptions" :key="u.id" :label="u.display_name" :value="u.id" />
            </el-select>
            <span v-else>{{ userNameMap[section.owner_id] || '-' }}</span>
          </el-form-item>
          <el-form-item label="跟进摘要">
            <el-input
              v-if="isDraft"
              v-model="section.summary"
              type="textarea"
              :rows="section.section_type === 'text' ? 5 : 4"
              :placeholder="summaryPlaceholder(section)"
            />
            <div v-else class="section-text">{{ section.summary || '（无）' }}</div>
          </el-form-item>
          <el-form-item v-if="isDraft && section.section_type !== 'text'" label="数据来源">
            <el-button link type="primary" @click="goDataSource(section.section_type)">
              查看{{ sectionTypeLabel(section.section_type) }}列表(另开)
            </el-button>
          </el-form-item>
        </el-form>
      </template>

      <!-- 关联专项报告(所有类型通用, 下钻审计) -->
      <el-form label-width="100px">
        <el-form-item label="专项报告">
          <div class="related-reports">
            <el-link
              v-for="rid in section.related_report_ids || []"
              :key="rid"
              type="primary"
              :underline="false"
              style="margin-right: 12px"
              @click="goReport(rid)"
            >
              报告 #{{ rid }}
            </el-link>
            <span v-if="!section.related_report_ids?.length" class="muted">（无）</span>
            <el-button v-if="isDraft" link type="success" :icon="Plus" @click="openDrillDialog(section)">
              安排专项审查
            </el-button>
          </div>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 添加分项(草稿态) -->
    <div v-if="isDraft" class="add-bar mb-16">
      <el-button :icon="Plus" type="primary" plain @click="addSection">添加分项</el-button>
      <span class="muted">共 {{ (report.framework || []).length }} 项 · 可上下排序/删除/改类型</span>
    </div>

    <!-- 会议评审 -->
    <el-card class="mb-16" header="会议评审（周会结论）">
      <el-form label-width="100px">
        <el-form-item label="会议结论">
          <el-input
            v-if="isDraft"
            v-model="report.meeting_conclusion"
            type="textarea"
            :rows="4"
            placeholder="记录周会分析结论、决议事项、安排的专项审查等"
          />
          <div v-else class="section-text">{{ report.meeting_conclusion || '（无）' }}</div>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 跟进注释时间线 -->
    <el-card header="跟进注释">
      <ReportCommentTimeline
        target-type="weekly"
        :target-id="weeklyId"
        :user-options="userOptions"
        placeholder="添加跟进注释(确认后可持续追加,不改原文)..."
      />
    </el-card>

    <!-- 专项下钻 dialog -->
    <el-dialog v-model="drillDialogVisible" title="安排专项审查(创建专项报告)" width="520px">
      <el-form :model="drillForm" label-width="100px">
        <el-form-item label="报告名称">
          <el-input v-model="drillForm.name" :placeholder="drillDefaultName" />
        </el-form-item>
        <el-form-item label="指派负责人">
          <el-select v-model="drillForm.assignee_id" placeholder="选择审查负责人" clearable filterable style="width: 100%">
            <el-option v-for="u in userOptions" :key="u.id" :label="u.display_name" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="要求提交时间">
          <el-date-picker v-model="drillForm.due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
      </el-form>
      <div class="page-sub mb-16">将创建一份专项审查报告(草稿),指派负责人在期限内完成审查并提交。报告会自动回链到当前分项。</div>
      <template #footer>
        <el-button @click="drillDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="drillSubmitting" @click="handleDrillCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 套用模板 dialog -->
    <el-dialog v-model="templateDialogVisible" title="套用模板(替换当前框架)" width="560px">
      <el-alert type="warning" :closable="false" class="mb-16" show-icon>
        套用模板会用模板框架替换当前周报看板分项(当前分项内容将被覆盖,未保存可恢复)。
      </el-alert>
      <el-form label-width="100px">
        <el-form-item label="选择模板">
          <el-select v-model="templateForm.templateId" placeholder="选择周报模板" filterable style="width: 100%">
            <el-option v-for="t in templateOptions" :key="t.id" :label="t.name" :value="t.id">
              <span>{{ t.name }}</span>
              <el-tag v-if="t.is_default" size="small" type="success" style="margin-left: 6px">默认</el-tag>
              <el-tag v-if="t.is_preset" size="small" type="warning" style="margin-left: 6px">预设</el-tag>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <div class="page-sub mb-16">模板分项会实例化为带空摘要/数据的周报看板分项,可继续编辑。</div>
      <template #footer>
        <el-button @click="templateDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="templateSubmitting" @click="handleApplyTemplate">套用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, ArrowUp, ArrowDown, Delete, Check, FolderOpened, DocumentChecked, Plus, Files } from '@element-plus/icons-vue'
import { getWeeklyReport, updateWeeklyReport, confirmWeeklyReport, archiveWeeklyReport, createDrillReport } from '@/api/weekly'
import { listReportTemplates, instantiateTemplate } from '@/api/report-template'
import { listAnalysisViews, executeAnalysisView } from '@/api/analysis-view'
import { listUsers } from '@/api/user'
import ViewItemCard from '@/components/ViewItemCard.vue'
import ReportCommentTimeline from '@/components/ReportCommentTimeline.vue'
import dayjs from 'dayjs'

const route = useRoute()
const router = useRouter()
const weeklyId = Number(route.params.id)
const report = ref({ framework: [] })
const viewOptions = ref([])
const userOptions = ref([])
const saving = ref(false)
const confirming = ref(false)

const statusMap = { draft: '草稿', confirmed: '已确认', archived: '已归档' }
const statusType = (s) => ({ draft: 'info', confirmed: 'success', archived: 'warning' }[s] || 'info')
const formatDate = (t) => (t ? dayjs(t).format('YY/MM/DD') : '-')
const isDraft = computed(() => report.value.status === 'draft')

const weekStartStr = computed(() => (report.value.week_start ? dayjs(report.value.week_start).format('YY/MM/DD') : ''))
const weekEndStr = computed(() => (report.value.week_end ? dayjs(report.value.week_end).format('YY/MM/DD') : ''))
const viewNameMap = computed(() => Object.fromEntries(viewOptions.value.map((v) => [v.id, v.name])))
const userNameMap = computed(() => Object.fromEntries(userOptions.value.map((u) => [u.id, u.display_name])))

const sectionTypeLabel = (t) => ({ view: '视图', measures: '措施', incidents: '异常', text: '文字' }[t] || t)
const sectionTypeTag = (t) => ({ view: 'primary', measures: 'warning', incidents: 'danger', text: 'info' }[t] || 'info')
const sectionTypeOptions = [
  { value: 'view', label: '视图(数据看板)' },
  { value: 'text', label: '文本(说明记录)' },
  { value: 'measures', label: '措施(跟进/计划)' },
  { value: 'incidents', label: '异常事件' },
]
const summaryPlaceholder = (section) => {
  if (section.section_type === 'measures') return '本周措施跟进情况/完成进度'
  if (section.section_type === 'incidents') return '本周异常事故记录/影响/处理'
  return '本周该项情况/结论'
}

// ---------- 可编辑看板: 增删/排序/套用模板 ----------
const templateOptions = ref([])
const templateDialogVisible = ref(false)
const templateSubmitting = ref(false)
const templateForm = reactive({ templateId: null })

async function loadTemplateOptions() {
  try {
    const res = await listReportTemplates({ report_type: 'weekly', page: 1, page_size: 100 })
    templateOptions.value = res.data.items || []
  } catch (e) {
    templateOptions.value = []
  }
}

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
    // 用实例化框架替换当前 framework
    report.value.framework = res.data || []
    templateDialogVisible.value = false
    ElMessage.success('已套用模板,请检查各分项视图绑定并保存')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '套用失败')
  } finally {
    templateSubmitting.value = false
  }
}

function addSection() {
  const newSec = {
    key: `section_${Date.now()}`,
    title: '新分项',
    section_type: 'view',
    view_ids: [],
    owner_id: null,
    summary: '',
    data: null,
    related_report_ids: [],
  }
  if (!Array.isArray(report.value.framework)) report.value.framework = []
  report.value.framework.push(newSec)
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
  // 触发响应式更新
  report.value.framework = [...arr]
}

function onSectionTypeChange(section) {
  // 切换到非 view 类型时清空 view_ids 无需,保留以便切回; data 清空避免误用
  if (section.section_type !== 'view') {
    section.data = null
  }
}

async function loadReport() {
  const res = await getWeeklyReport(weeklyId)
  report.value = res.data
  // 兼容旧数据: 确保各 section 有 view_ids/related_report_ids 数组
  for (const s of report.value.framework || []) {
    if (!Array.isArray(s.view_ids)) s.view_ids = []
    if (!Array.isArray(s.related_report_ids)) s.related_report_ids = []
  }
}

async function loadViewOptions() {
  const res = await listAnalysisViews({ page: 1, page_size: 100 })
  viewOptions.value = res.data.items || []
}

async function loadUsers() {
  const res = await listUsers({ page: 1, page_size: 100 })
  userOptions.value = res.data.items || []
}

function goDataSource(sectionType) {
  const path = sectionType === 'measures' ? '/management/measures' : '/management/incidents'
  window.open(router.resolve(path).href, '_blank')
}

function goReport(id) {
  router.push(`/management/reports/${id}`)
}

function buildFrameworkPayload(skipData = false) {
  return report.value.framework.map((s) => ({
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

async function handleSave() {
  saving.value = true
  try {
    await updateWeeklyReport(weeklyId, {
      framework: buildFrameworkPayload(),
      summary: report.value.summary,
      meeting_conclusion: report.value.meeting_conclusion,
    })
    ElMessage.success('已保存')
    loadReport()
  } finally {
    saving.value = false
  }
}

async function fetchViewSnapshots() {
  // 对每个 view 型且 view_ids 存在的 section, 逐个执行视图(本周时间)拿结果存入 data[viewId]
  const start = weekStartStr.value
  const end = weekEndStr.value
  for (const s of report.value.framework) {
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

async function handleConfirm() {
  await ElMessageBox.confirm(
    '确认固化周报?系统将执行所有关联视图(本周时间)并固化快照,确认后不可再编辑,后续跟进走注释。',
    '确认固化',
    { type: 'warning' }
  )
  confirming.value = true
  try {
    await fetchViewSnapshots()
    await updateWeeklyReport(weeklyId, {
      framework: buildFrameworkPayload(),
      summary: report.value.summary,
      meeting_conclusion: report.value.meeting_conclusion,
    })
    await confirmWeeklyReport(weeklyId)
    ElMessage.success('已确认固化(视图快照已锁定)')
    loadReport()
  } finally {
    confirming.value = false
  }
}

async function handleArchive() {
  await ElMessageBox.confirm('确认归档该周报?', '提示', { type: 'warning' })
  await archiveWeeklyReport(weeklyId)
  ElMessage.success('已归档')
  loadReport()
}

// ---------- 专项下钻 ----------
const drillDialogVisible = ref(false)
const drillSubmitting = ref(false)
const drillForm = reactive({ name: '', assignee_id: null, due_date: '' })
const drillSection = ref(null)
const drillDefaultName = computed(() => {
  if (!drillSection.value) return ''
  return `${report.value.name || ''} - ${drillSection.value.title} 专项审查`
})

function openDrillDialog(section) {
  drillSection.value = section
  Object.assign(drillForm, { name: '', assignee_id: section.owner_id || null, due_date: '' })
  drillDialogVisible.value = true
}

async function handleDrillCreate() {
  if (!drillSection.value) return
  drillSubmitting.value = true
  try {
    const payload = {
      name: drillForm.name || drillDefaultName.value,
      assignee_id: drillForm.assignee_id || undefined,
      due_date: drillForm.due_date ? drillForm.due_date + 'T23:59:59' : undefined,
    }
    const res = await createDrillReport(weeklyId, drillSection.value.key, payload)
    ElMessage.success(res.message || '已创建专项报告')
    drillDialogVisible.value = false
    loadReport()
  } finally {
    drillSubmitting.value = false
  }
}

onMounted(() => {
  loadReport()
  loadViewOptions()
  loadUsers()
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
.framework-tip {
  color: #909399;
  font-size: 13px;
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
.view-render-area {
  margin: 0 0 8px 0;
}
.muted {
  color: #909399;
  font-size: 13px;
}
.related-reports {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.section-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
}
.add-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
