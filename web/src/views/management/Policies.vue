<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">政策措施登记</div>
        <div class="page-sub">管理发布物: SOP/规范/话术/预案 · 替代链可视化 · 主题/状态多维筛选</div>
      </div>
      <el-button v-if="authStore.isManager" type="primary" :icon="Plus" @click="openCreate">新建制度</el-button>
    </div>

    <!-- FilterBar(规划设计 §6.1 列表页范式) -->
    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-input v-model="keyword" placeholder="标题/摘要关键词" clearable style="width: 220px" @keyup.enter="loadData" />
        <el-select v-model="category" placeholder="分类" clearable style="width: 160px" @change="loadData">
          <el-option v-for="c in categories" :key="c.value" :label="catLabel(c.value)" :value="c.value" />
        </el-select>
        <el-select v-model="status" placeholder="状态" clearable style="width: 130px" @change="loadData">
          <el-option v-for="s in statuses" :key="s.value" :label="statusLabel(s.value)" :value="s.value" />
        </el-select>
        <el-select v-model="topic" placeholder="主题" clearable filterable style="width: 160px" @change="loadData">
          <el-option v-for="t in topics" :key="t" :label="t" :value="t" />
        </el-select>
        <el-checkbox v-model="onlyPublished" @change="loadData">只看已发布</el-checkbox>
        <el-checkbox v-model="hasReplacement" @change="loadData">仅替代链</el-checkbox>
        <el-button type="primary" @click="loadData">查询</el-button>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </el-card>

    <!-- 列表 -->
    <el-card>
      <el-table v-loading="loading" :data="list" border stripe style="cursor: pointer" @row-click="openDetail">
        <el-table-column prop="title" label="标题" show-overflow-tooltip min-width="220">
          <template #default="{ row }">
            <el-icon v-if="row.replaces_policy_id || row.replaced_by_policy_id" color="#9b59b6" style="margin-right: 4px"><Connection /></el-icon>
            {{ row.title }}
            <el-tag v-if="row.version" size="small" effect="plain" style="margin-left: 6px">v{{ row.version }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分类" width="120">
          <template #default="{ row }">{{ catLabel(row.category) }}</template>
        </el-table-column>
        <el-table-column label="主题" width="180">
          <template #default="{ row }">
            <el-tag v-for="t in (row.topic_tags || []).slice(0, 2)" :key="t" size="small" effect="plain" style="margin-right: 4px">{{ t }}</el-tag>
            <span v-if="(row.topic_tags || []).length > 2" class="text-muted">+{{ row.topic_tags.length - 2 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusColor(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="生效期" width="200">
          <template #default="{ row }">
            <span v-if="row.valid_from || row.valid_to" class="text-muted">
              {{ row.valid_from ? formatTime(row.valid_from) : '...' }} ~ {{ row.valid_to ? formatTime(row.valid_to) : '至今' }}
            </span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="view_count" label="查阅" width="70" align="right" />
        <el-table-column v-if="authStore.isManager" label="操作" width="240" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 'draft'" link type="warning" @click.stop="handleSubmitReview(row)">提交审批</el-button>
            <el-button v-if="row.status === 'pending_approval'" link type="success" @click.stop="handleApprove(row)">通过</el-button>
            <el-button v-if="row.status === 'pending_approval'" link type="danger" @click.stop="handleReject(row)">驳回</el-button>
            <el-button v-if="row.status === 'published'" link type="info" @click.stop="openSupersede(row)">被替代</el-button>
            <el-button v-if="row.status !== 'archived' && row.status !== 'draft'" link type="info" @click.stop="handleArchive(row)">归档</el-button>
            <el-button v-if="row.status === 'draft'" link type="danger" @click.stop="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 详情抽屉(含替代链时间轴) -->
    <el-drawer v-model="detailVisible" :title="current?.title || '详情'" size="60%">
      <div v-if="current" class="detail-body">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="标题" :span="2">{{ current.title }}</el-descriptions-item>
          <el-descriptions-item label="分类">{{ catLabel(current.category) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusColor(current.status)" size="small">{{ statusLabel(current.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="版本">{{ current.version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="查阅次数">{{ current.view_count || 0 }}</el-descriptions-item>
          <el-descriptions-item label="生效期" :span="2">
            {{ current.valid_from ? formatTime(current.valid_from) : '未指定' }} ~ {{ current.valid_to ? formatTime(current.valid_to) : '至今' }}
          </el-descriptions-item>
          <el-descriptions-item label="主题标签" :span="2">
            <el-tag v-for="t in (current.topic_tags || [])" :key="t" size="small" effect="plain" style="margin-right: 6px">{{ t }}</el-tag>
            <span v-if="!(current.topic_tags || []).length" class="text-muted">无</span>
          </el-descriptions-item>
          <el-descriptions-item label="摘要" :span="2">
            <span v-if="current.summary">{{ current.summary }}</span>
            <span v-else class="text-muted">无</span>
          </el-descriptions-item>
          <el-descriptions-item label="正文" :span="2">
            <div class="content-box">{{ current.content || '（无正文）' }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="附件" :span="2">
            <el-link v-for="(u, i) in (current.attachment_urls || [])" :key="i" :href="u" target="_blank" type="primary" style="margin-right: 12px">附件 {{ i + 1 }}</el-link>
            <span v-if="!(current.attachment_urls || []).length" class="text-muted">无</span>
          </el-descriptions-item>
          <el-descriptions-item label="示意图" :span="2">
            <el-link v-for="(u, i) in (current.diagram_urls || [])" :key="i" :href="u" target="_blank" type="primary" style="margin-right: 12px">示意图 {{ i + 1 }}</el-link>
            <span v-if="!(current.diagram_urls || []).length" class="text-muted">无</span>
          </el-descriptions-item>
          <el-descriptions-item label="创建人">{{ current.created_by || '-' }}</el-descriptions-item>
          <el-descriptions-item label="审批人">{{ current.approver_id ? ('#' + current.approver_id) : '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatTime(current.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatTime(current.updated_at) }}</el-descriptions-item>
        </el-descriptions>

        <!-- 替代链可视化 -->
        <div class="chain-section">
          <div class="chain-title">
            <el-icon><Connection /></el-icon>
            <span>政策演进时间轴</span>
            <el-button v-if="authStore.isManager && current.status === 'published'" link type="primary" @click="openSupersede(current)">建立替代关系</el-button>
          </div>
          <div v-if="chainLoading" class="text-muted" style="padding: 16px 0">加载中...</div>
          <el-empty v-else-if="!chain || (!chain.predecessors?.length && !chain.successors?.length)" description="暂无替代关系" :image-size="60" />
          <div v-else class="chain-timeline">
            <!-- predecessors(被替代的旧政策) -->
            <div v-for="p in chain.predecessors" :key="p.id" class="chain-item chain-old" @click="jumpPolicy(p.id)">
              <el-tag size="small" type="info">旧</el-tag>
              <span class="chain-name">{{ p.title }}</span>
              <span class="text-muted">#{{ p.id }}</span>
              <el-tag :type="statusColor(p.status)" size="small">{{ statusLabel(p.status) }}</el-tag>
              <span v-if="p.valid_to" class="text-muted">{{ formatTime(p.valid_to) }}</span>
            </div>
            <!-- current -->
            <div class="chain-item chain-current">
              <el-tag size="small" type="success">当前</el-tag>
              <span class="chain-name">{{ chain.current.title }}</span>
              <span class="text-muted">#{{ chain.current.id }}</span>
              <el-tag :type="statusColor(chain.current.status)" size="small">{{ statusLabel(chain.current.status) }}</el-tag>
            </div>
            <!-- successors(后续替代政策) -->
            <div v-for="s in chain.successors" :key="s.id" class="chain-item chain-new" @click="jumpPolicy(s.id)">
              <el-tag size="small" type="warning">新</el-tag>
              <span class="chain-name">{{ s.title }}</span>
              <span class="text-muted">#{{ s.id }}</span>
              <el-tag :type="statusColor(s.status)" size="small">{{ statusLabel(s.status) }}</el-tag>
              <span v-if="s.valid_from" class="text-muted">{{ formatTime(s.valid_from) }}</span>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>

    <!-- 新建/编辑 -->
    <el-dialog v-model="editVisible" :title="editForm.id ? '编辑制度' : '新建制度'" width="720px">
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="标题" required><el-input v-model="editForm.title" placeholder="制度标题" /></el-form-item>
        <el-form-item label="分类">
          <el-select v-model="editForm.category" style="width: 100%">
            <el-option v-for="c in categories" :key="c.value" :label="catLabel(c.value)" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="摘要"><el-input v-model="editForm.summary" type="textarea" :rows="2" placeholder="一句话摘要" /></el-form-item>
        <el-form-item label="正文"><el-input v-model="editForm.content" type="textarea" :rows="8" placeholder="正文(Markdown/纯文本)" /></el-form-item>
        <el-form-item label="主题标签">
          <el-select v-model="editForm.topic_tags" multiple filterable allow-create default-first-option placeholder="回车添加主题标签" style="width: 100%">
            <el-option v-for="t in topics" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="版本"><el-input v-model="editForm.version" placeholder="v1.0" /></el-form-item>
        <el-form-item label="生效起止">
          <el-date-picker v-model="editForm.valid_range" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ss" start-placeholder="生效起始" end-placeholder="生效结束" style="width: 100%" />
        </el-form-item>
        <el-form-item label="附件URL">
          <el-select v-model="editForm.attachment_urls" multiple filterable allow-create default-first-option placeholder="粘贴附件URL回车添加" style="width: 100%" />
        </el-form-item>
        <el-form-item label="示意图URL">
          <el-select v-model="editForm.diagram_urls" multiple filterable allow-create default-first-option placeholder="粘贴示意图URL回车添加" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="editForm.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSave">保存草稿</el-button>
      </template>
    </el-dialog>

    <!-- 被替代 Dialog -->
    <el-dialog v-model="supersedeVisible" title="建立替代关系" width="480px">
      <el-form :model="supersedeForm" label-width="120px">
        <el-form-item label="当前政策">
          <span style="font-weight: 600">{{ current?.title }}</span>
          <el-tag size="small" style="margin-left: 8px">{{ statusLabel(current?.status) }}</el-tag>
        </el-form-item>
        <el-form-item label="替代为(新)">
          <el-select v-model="supersedeForm.new_policy_id" filterable placeholder="选择新政策" style="width: 100%">
            <el-option v-for="p in publishableForSupersede" :key="p.id" :label="p.title + ' (#' + p.id + ')'" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="supersedeForm.remark" type="textarea" :rows="2" placeholder="替代原因/差异说明" /></el-form-item>
        <div class="text-muted" style="font-size: 12px">操作后: 当前政策自动归档 + replaced_by 指向新政策; 新政策 replaces 指向当前。</div>
      </el-form>
      <template #footer>
        <el-button @click="supersedeVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSupersede">建立替代</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Connection } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { statusColor, statusLabel } from '@/utils/statusColor'
import {
  listPolicies, getPolicy, createPolicy, updatePolicy, deletePolicy,
  submitPolicyReview, approvePolicy, rejectPolicy, archivePolicy, supersedePolicy, getPolicyChain,
  listPolicyCategories, listPolicyStatuses, listPolicyTopics,
} from '@/api/policy'
import dayjs from 'dayjs'

const authStore = useAuthStore()
const list = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const submitting = ref(false)
const keyword = ref('')
const category = ref('')
const status = ref('')
const topic = ref('')
const onlyPublished = ref(false)
const hasReplacement = ref(false)
const categories = ref([])
const statuses = ref([])
const topics = ref([])
const detailVisible = ref(false)
const editVisible = ref(false)
const supersedeVisible = ref(false)
const current = ref(null)
const chain = ref(null)
const chainLoading = ref(false)
const editForm = reactive({ id: null, title: '', category: 'other', summary: '', content: '', topic_tags: [], version: '', valid_range: [], attachment_urls: [], diagram_urls: [], remark: '' })
const supersedeForm = reactive({ new_policy_id: null, remark: '' })

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const catLabel = (v) => ({ service_standard: '服务规范', process_sop: '流程SOP', management_rule: '管理规定', communication_script: '沟通话术', incident_response: '应急预案', other: '其他' }[v] || v)

// 用于替代下拉: 当前 + 草稿/已发布中除当前外的
const publishableForSupersede = computed(() => list.value.filter((p) => p.id !== current.value?.id && ['draft', 'pending_approval', 'published'].includes(p.status)))

async function loadMeta() {
  const [c, s, t] = await Promise.all([listPolicyCategories(), listPolicyStatuses(), listPolicyTopics()])
  categories.value = c.data || []
  statuses.value = s.data || []
  topics.value = t.data || []
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: 20 }
    if (keyword.value) params.keyword = keyword.value
    if (category.value) params.category = category.value
    if (status.value) params.status = status.value
    if (topic.value) params.topic = topic.value
    if (onlyPublished.value) params.only_published = true
    if (hasReplacement.value) params.has_replacement = true
    const res = await listPolicies(params)
    list.value = res.data.items || []
    total.value = res.data.total || 0
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  const res = await getPolicy(row.id)
  current.value = res.data
  detailVisible.value = true
  loadChain(row.id)
}

async function loadChain(id) {
  chainLoading.value = true
  try {
    const res = await getPolicyChain(id)
    chain.value = res.data
  } catch {
    chain.value = null
  } finally {
    chainLoading.value = false
  }
}

function jumpPolicy(id) {
  if (!id) return
  // 重新加载该政策详情 + 链
  openDetail({ id })
}

function openCreate() {
  Object.assign(editForm, { id: null, title: '', category: 'other', summary: '', content: '', topic_tags: [], version: '', valid_range: [], attachment_urls: [], diagram_urls: [], remark: '' })
  editVisible.value = true
}

async function openEdit(row) {
  const res = await getPolicy(row.id)
  const p = res.data
  Object.assign(editForm, {
    id: p.id, title: p.title, category: p.category, summary: p.summary || '', content: p.content || '',
    topic_tags: p.topic_tags || [], version: p.version || '',
    valid_range: [p.valid_from, p.valid_to].filter(Boolean),
    attachment_urls: p.attachment_urls || [], diagram_urls: p.diagram_urls || [], remark: p.remark || '',
  })
  editVisible.value = true
}

async function handleSave() {
  if (!editForm.title.trim()) {
    ElMessage.warning('请输入标题')
    return
  }
  submitting.value = true
  try {
    const payload = {
      title: editForm.title, category: editForm.category, summary: editForm.summary, content: editForm.content,
      topic_tags: editForm.topic_tags, version: editForm.version || null,
      valid_from: editForm.valid_range?.[0] || null, valid_to: editForm.valid_range?.[1] || null,
      attachment_urls: editForm.attachment_urls, diagram_urls: editForm.diagram_urls, remark: editForm.remark,
    }
    if (editForm.id) {
      await updatePolicy(editForm.id, payload)
    } else {
      await createPolicy(payload)
    }
    ElMessage.success('保存成功')
    editVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function handleSubmitReview(row) {
  await ElMessageBox.confirm(`提交审批「${row.title}」?`, '提示')
  await submitPolicyReview(row.id)
  ElMessage.success('已提交审批')
  loadData()
}

async function handleApprove(row) {
  const { value } = await ElMessageBox.prompt('审批意见(可空)', `通过「${row.title}」`, { inputType: 'textarea' })
  await approvePolicy(row.id, { approver_note: value })
  ElMessage.success('已发布')
  loadData()
}

async function handleReject(row) {
  const { value } = await ElMessageBox.prompt('驳回原因', `驳回「${row.title}」`, { inputType: 'textarea', inputPlaceholder: '必填' })
  if (!value) return ElMessage.warning('请输入驳回原因')
  await rejectPolicy(row.id, { approver_note: value })
  ElMessage.success('已驳回')
  loadData()
}

async function handleArchive(row) {
  const { value } = await ElMessageBox.prompt('归档备注(可空)', `归档「${row.title}」`, { inputType: 'textarea' })
  await archivePolicy(row.id, { remark: value })
  ElMessage.success('已归档')
  loadData()
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`删除草稿「${row.title}」? 此操作不可恢复`, '提示', { type: 'warning' })
  await deletePolicy(row.id)
  ElMessage.success('已删除')
  loadData()
}

function openSupersede(row) {
  supersedeForm.new_policy_id = null
  supersedeForm.remark = ''
  supersedeVisible.value = true
  // 确保列表已加载
  if (!list.value.length) loadData()
}

async function handleSupersede() {
  if (!supersedeForm.new_policy_id) return ElMessage.warning('请选择新政策')
  submitting.value = true
  try {
    await supersedePolicy(current.value.id, { new_policy_id: supersedeForm.new_policy_id, remark: supersedeForm.remark })
    ElMessage.success('替代关系已建立, 当前政策已归档')
    supersedeVisible.value = false
    loadData()
    // 刷新详情与链
    const res = await getPolicy(current.value.id)
    current.value = res.data
    loadChain(current.value.id)
  } finally {
    submitting.value = false
  }
}

function loadAll() {
  loadMeta()
  loadData()
}

onMounted(loadAll)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.text-muted { color: #909399; font-size: 12px; }
.detail-body { padding: 0 16px 16px; }
.content-box {
  white-space: pre-wrap; line-height: 1.8; color: #303133;
  background: #f5f7fa; padding: 12px; border-radius: 4px; max-height: 280px; overflow: auto;
}
.chain-section { margin-top: 24px; border-top: 1px dashed #dcdfe6; padding-top: 16px; }
.chain-title { display: flex; align-items: center; gap: 8px; font-weight: 600; margin-bottom: 12px; }
.chain-timeline { display: flex; flex-direction: column; gap: 8px; }
.chain-item {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  border-left: 3px solid #dcdfe6; background: #f5f7fa; border-radius: 4px; cursor: pointer;
  transition: all 0.15s;
}
.chain-item:hover { background: #ecf5ff; border-left-color: #409eff; }
.chain-old { border-left-color: #909399; }
.chain-current { border-left-color: #67c23a; background: #f0f9eb; font-weight: 600; }
.chain-new { border-left-color: #e6a23c; background: #fdf6ec; }
.chain-name { flex: 1; color: #303133; }
</style>
