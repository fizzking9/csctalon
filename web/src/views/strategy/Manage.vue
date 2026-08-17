<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">策略管理</div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建草稿</el-button>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="loadData">
          <el-option label="草稿" value="draft" />
          <el-option label="已发布" value="published" />
          <el-option label="已归档" value="archived" />
        </el-select>
        <el-select v-model="filter.category" placeholder="分类" clearable style="width: 140px" @change="loadData">
          <el-option label="退款策略" value="退款策略" />
          <el-option label="清退策略" value="清退策略" />
          <el-option label="服务规范" value="服务规范" />
          <el-option label="通用" value="通用" />
        </el-select>
        <el-input v-model="filter.keyword" placeholder="搜索标题" clearable style="width: 200px" @keyup.enter="loadData" @clear="loadData" />
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column label="分类" width="110">
          <template #default="{ row }">
            <el-tag size="small">{{ row.category || '通用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="70">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="已读" width="90">
          <template #default="{ row }">
            <span v-if="row.status === 'published'">{{ row.read_count || 0 }}</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="发布时间" width="160">
          <template #default="{ row }">{{ formatTime(row.published_at) }}</template>
        </el-table-column>
        <el-table-column prop="published_by" label="发布人" width="90" />
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'draft'" size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 'draft'" size="small" link type="success" @click="handlePublish(row)">发布</el-button>
            <el-button v-if="row.status === 'published'" size="small" link type="warning" @click="handleNewVersion(row)">新建版本</el-button>
            <el-button v-if="row.status === 'published'" size="small" link @click="openReceipts(row)">已读情况</el-button>
            <el-button v-if="row.status !== 'published'" size="small" link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 编辑/新建弹窗 -->
    <el-dialog v-model="editVisible" :title="editing.id ? '编辑草稿' : '新建草稿'" width="820px" @opened="onEditorOpen">
      <el-form :model="editing" label-width="80px">
        <el-form-item label="标题" required>
          <el-input v-model="editing.title" :disabled="!!editing.id" placeholder="标题作为文档唯一标识,发布后同标题会归档旧版本" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="editing.category" style="width: 100%" placeholder="选择分类" allow-create>
            <el-option label="退款策略" value="退款策略" />
            <el-option label="清退策略" value="清退策略" />
            <el-option label="服务规范" value="服务规范" />
            <el-option label="通用" value="通用" />
          </el-select>
        </el-form-item>
        <el-form-item label="内容">
          <el-tabs v-model="editorTab" class="editor-tabs">
            <el-tab-pane label="编辑" name="edit">
              <el-input
                v-model="editing.content"
                type="textarea"
                :rows="16"
                placeholder="支持 Markdown 格式:标题用 #、列表用 - /*、强调用 **文字** 等"
              />
            </el-tab-pane>
            <el-tab-pane label="预览" name="preview">
              <div class="markdown-body" v-html="previewHtml" />
            </el-tab-pane>
          </el-tabs>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 已读情况弹窗 -->
    <el-dialog v-model="receiptVisible" :title="`已读情况 - ${receiptData.title || ''}`" width="640px">
      <div v-loading="receiptLoading">
        <el-alert v-if="receiptData.total !== undefined" :title="`已读 ${receiptData.read_count} / ${receiptData.total} 人`" :type="receiptData.read_count === receiptData.total ? 'success' : 'warning'" :closable="false" class="mb-16" />
        <el-tabs>
          <el-tab-pane :label="`已读 (${receiptData.read_count || 0})`">
            <el-table :data="receiptData.read_users" size="small" border max-height="300">
              <el-table-column prop="display_name" label="姓名" />
              <el-table-column prop="role" label="角色" width="80">
                <template #default="{ row }">{{ roleLabel(row.role) }}</template>
              </el-table-column>
              <el-table-column label="阅读时间" width="160">
                <template #default="{ row }">{{ formatTime(row.read_at) }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`未读 (${receiptData.unread_count || 0})`">
            <el-table :data="receiptData.unread_users" size="small" border max-height="300">
              <el-table-column prop="display_name" label="姓名" />
              <el-table-column prop="role" label="角色" width="80">
                <template #default="{ row }">{{ roleLabel(row.role) }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { renderMarkdown } from '@/utils/sanitize'
import { listStrategy, createStrategy, updateStrategy, deleteStrategy, publishStrategy, newVersion, getReceipts, getStrategy } from '@/api/strategy'
import dayjs from 'dayjs'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ status: '', category: '', keyword: '' })
const editVisible = ref(false)
const submitting = ref(false)
const editing = reactive({ id: null, title: '', category: '', content: '' })
const editorTab = ref('edit')
const receiptVisible = ref(false)
const receiptLoading = ref(false)
const receiptData = reactive({ title: '', total: 0, read_count: 0, unread_count: 0, read_users: [], unread_users: [] })

const previewHtml = computed(() => {
  if (!editing.content) return '<p style="color:#c0c4cc">暂无内容</p>'
  // P0-5: marked 解析后经 DOMPurify 消毒, 防存储型 XSS
  return renderMarkdown(editing.content)
})

function onEditorOpen() {
  // 打开编辑弹窗时默认回到编辑 tab
  editorTab.value = 'edit'
}

const statusMap = { draft: '草稿', published: '已发布', archived: '已归档' }
const statusLabel = (s) => statusMap[s] || s
const statusType = (s) => ({ draft: 'info', published: 'success', archived: 'warning' }[s] || 'info')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const roleLabel = (r) => ({ admin: '管理员', supervisor: '主管', agent: '客服' }[r] || r)

async function loadData() {
  loading.value = true
  try {
    const res = await listStrategy({ page: page.value, page_size: 20, ...filter })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(editing, { id: null, title: '', category: '', content: '' })
  editVisible.value = true
}

function openEdit(row) {
  Object.assign(editing, { id: row.id, title: row.title, category: row.category, content: '' })
  // 加载完整内容
  getStrategy(row.id).then((res) => {
    editing.content = res.data.content || ''
  })
  editVisible.value = true
}

async function handleSave() {
  if (!editing.title.trim()) {
    ElMessage.warning('请输入标题')
    return
  }
  submitting.value = true
  try {
    if (editing.id) {
      await updateStrategy(editing.id, { title: editing.title, category: editing.category, content: editing.content })
      ElMessage.success('已保存')
    } else {
      await createStrategy({ title: editing.title, category: editing.category, content: editing.content })
      ElMessage.success('已创建草稿')
    }
    editVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function handlePublish(row) {
  await ElMessageBox.confirm(`确认发布「${row.title}」v${row.version}?发布后不可编辑,同标题旧版本将归档`, '发布确认', { type: 'warning' })
  await publishStrategy(row.id)
  ElMessage.success('已发布')
  loadData()
}

async function handleNewVersion(row) {
  await ElMessageBox.confirm(`基于「${row.title}」v${row.version}新建版本?将创建一个新草稿(v${row.version + 1})`, '新建版本', { type: 'info' })
  const res = await newVersion(row.id)
  ElMessage.success(`已创建草稿 v${res.data.version}`)
  loadData()
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除「${row.title}」v${row.version}?`, '删除确认', { type: 'warning' })
  await deleteStrategy(row.id)
  ElMessage.success('已删除')
  loadData()
}

async function openReceipts(row) {
  Object.assign(receiptData, { title: row.title, total: 0, read_count: 0, unread_count: 0, read_users: [], unread_users: [] })
  receiptVisible.value = true
  receiptLoading.value = true
  try {
    const res = await getReceipts(row.id)
    Object.assign(receiptData, res.data)
  } finally {
    receiptLoading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.text-muted {
  color: #c0c4cc;
}
.editor-tabs :deep(.el-tabs__content) {
  min-height: 360px;
}
.markdown-body {
  color: #303133;
  font-size: 14px;
  line-height: 1.8;
  word-break: break-word;
  min-height: 360px;
  padding: 4px 8px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 14px 0 8px;
  font-weight: 600;
}
.markdown-body :deep(h1) {
  font-size: 19px;
}
.markdown-body :deep(h2) {
  font-size: 16px;
}
.markdown-body :deep(p) {
  margin: 6px 0;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 24px;
  margin: 6px 0;
}
.markdown-body :deep(li) {
  margin: 3px 0;
}
.markdown-body :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  color: #c7254e;
}
.markdown-body :deep(pre) {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
  overflow-x: auto;
}
.markdown-body :deep(blockquote) {
  border-left: 4px solid #dcdfe6;
  padding-left: 12px;
  margin: 8px 0;
  color: #606266;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 6px 10px;
}
.markdown-body :deep(th) {
  background: #f5f7fa;
}
</style>
