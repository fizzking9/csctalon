<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">知识库 / 话术库</div>
        <div class="page-sub">常见问题 → 标准处理流程/话术 · 与政策打通 · 面向一线检索</div>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建条目</el-button>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8">
        <el-input v-model="keyword" placeholder="搜索标题/问题/话术" clearable style="width: 240px" @keyup.enter="loadData" />
        <el-select v-model="category" placeholder="分类" clearable style="width: 160px" @change="loadData">
          <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
        </el-select>
        <el-button type="primary" @click="loadData">搜索</el-button>
        <el-button v-if="authStore.isManager" link @click="includeDraft = !includeDraft">含草稿</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe @row-click="openDetail" style="cursor: pointer">
        <el-table-column prop="title" label="标题" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="100" />
        <el-table-column prop="question" label="问题/场景" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="view_count" label="检索" width="80" align="right" />
        <el-table-column v-if="authStore.isManager" label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click.stop="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 详情 -->
    <el-dialog v-model="detailVisible" :title="current?.title || '条目详情'" width="680px">
      <div v-if="current">
        <div class="kb-question">{{ current.question }}</div>
        <div class="kb-answer" style="white-space: pre-wrap">{{ current.answer }}</div>
      </div>
    </el-dialog>

    <!-- 编辑 -->
    <el-dialog v-model="editVisible" :title="editForm.id ? '编辑条目' : '新建条目'" width="640px">
      <el-form :model="editForm" label-width="90px">
        <el-form-item label="标题" required><el-input v-model="editForm.title" /></el-form-item>
        <el-form-item label="分类">
          <el-select v-model="editForm.category" allow-create filterable style="width: 100%">
            <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="问题/场景"><el-input v-model="editForm.question" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="话术/流程"><el-input v-model="editForm.answer" type="textarea" :rows="8" placeholder="标准处理流程/话术" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option v-for="s in ['草稿', '已发布', '已归档']" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { listArticles, listCategories, getArticle, createArticle, updateArticle, deleteArticle } from '@/api/kbArticle'

const authStore = useAuthStore()
const list = ref([])
const categories = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const submitting = ref(false)
const keyword = ref('')
const category = ref('')
const includeDraft = ref(false)
const detailVisible = ref(false)
const editVisible = ref(false)
const current = ref(null)
const editForm = reactive({ id: null, title: '', category: '', question: '', answer: '', status: '已发布' })
const statusType = (s) => ({ 草稿: 'info', 已发布: 'success', 已归档: 'warning' }[s] || 'info')

async function loadCategories() {
  const res = await listCategories()
  categories.value = res.data || []
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: 20, keyword: keyword.value || undefined, category: category.value || undefined, include_draft: includeDraft.value }
    const res = await listArticles(params)
    list.value = res.data.items || []
    total.value = res.data.total || 0
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  const res = await getArticle(row.id)
  current.value = res.data
  detailVisible.value = true
}

function openCreate() {
  Object.assign(editForm, { id: null, title: '', category: '', question: '', answer: '', status: '已发布' })
  editVisible.value = true
}

function openEdit(row) {
  Object.assign(editForm, { id: row.id, title: row.title, category: row.category, question: row.question, answer: row.answer, status: row.status })
  editVisible.value = true
}

async function handleSave() {
  if (!editForm.title.trim()) {
    ElMessage.warning('请输入标题')
    return
  }
  submitting.value = true
  try {
    if (editForm.id) {
      await updateArticle(editForm.id, { ...editForm })
    } else {
      await createArticle({ ...editForm })
    }
    ElMessage.success('保存成功')
    editVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确认删除该条目?', '提示', { type: 'warning' })
  await deleteArticle(row.id)
  ElMessage.success('已删除')
  loadData()
}

onMounted(() => {
  loadCategories()
  loadData()
})
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.kb-question { font-weight: 600; color: #303133; margin-bottom: 12px; }
.kb-answer { color: #606266; line-height: 1.8; }
</style>
