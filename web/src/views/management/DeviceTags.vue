<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">设备专项标签</div>
        <div class="page-sub">设备 SN 动态打标(清退/降速/异常等) · 批量清单提交 · 接待关联提示 · 统计分析</div>
      </div>
      <div class="flex gap-8">
        <el-button type="primary" :icon="Upload" @click="openImport">批量导入 SN</el-button>
        <el-button :icon="Plus" @click="openTagCreate">新建标签</el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :md="16">
        <el-card>
          <template #header>
            <div class="flex-between">
              <span style="font-weight: 600">标签字典</span>
              <el-button :icon="Refresh" text @click="loadTags">刷新</el-button>
            </div>
          </template>
          <el-table :data="tags" v-loading="loading" border stripe>
            <el-table-column prop="tag_name" label="标签名" />
            <el-table-column label="类型" width="100">
              <template #default="{ row }">
                <el-tag :type="row.tag_type === 'preset' ? 'warning' : 'info'" size="small">{{ row.tag_type === 'preset' ? '预设' : '自定义' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="颜色" width="80">
              <template #default="{ row }"><span class="color-dot" :style="{ background: row.color || '#409eff' }" /></template>
            </el-table-column>
            <el-table-column prop="description" label="说明/处理建议" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card>
          <template #header><span style="font-weight: 600">标签统计(激活SN数)</span></template>
          <el-table :data="stats" v-loading="statsLoading" border stripe size="small">
            <el-table-column prop="tag_name" label="标签" />
            <el-table-column prop="sn_count" label="SN数" width="80" align="right" />
          </el-table>
          <el-empty v-if="!statsLoading && !stats.length" description="暂无打标数据" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <el-card class="mt-16">
      <template #header>
        <div class="flex-between">
          <span style="font-weight: 600">批量导入批次</span>
          <el-button :icon="Refresh" text @click="loadBatches">刷新</el-button>
        </div>
      </template>
      <el-table :data="batches" v-loading="batchesLoading" border stripe>
        <el-table-column prop="batch_no" label="批次号" width="200" />
        <el-table-column prop="tag_name" label="标签" width="120" />
        <el-table-column prop="title" label="标题" show-overflow-tooltip />
        <el-table-column prop="total_count" label="提交数" width="80" align="right" />
        <el-table-column prop="matched_count" label="成功打标" width="90" align="right" />
        <el-table-column prop="created_by" label="导入人" width="100" />
        <el-table-column label="导入时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 批量导入对话框 -->
    <el-dialog v-model="importVisible" title="批量导入 SN 打标" width="640px">
      <el-form :model="importForm" label-width="100px">
        <el-form-item label="标签" required>
          <el-select v-model="importForm.tag_id" placeholder="选择标签" style="width: 100%">
            <el-option v-for="t in tags" :key="t.id" :label="t.tag_name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="importForm.title" placeholder="如 2026-08 清退清单" />
        </el-form-item>
        <el-form-item label="SN清单" required>
          <el-input v-model="importForm.snText" type="textarea" :rows="10" placeholder="每行一个SN，或粘贴/上传的清单" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="importForm.note" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <el-alert v-if="importResult" type="info" :closable="false" class="mb-8" show-icon>
        提交 {{ importResult.total }} · 成功打标 {{ importResult.matched }} · 已存在 {{ importResult.already }} · 无效 {{ importResult.invalid_count }}
        <div v-if="importResult.invalid_count" class="mt-8">
          无效SN: <span class="invalid-list">{{ importResult.invalid.join(', ') }}</span>
        </div>
      </el-alert>
      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <el-button type="primary" :loading="submitting" @click="handleImport">导入</el-button>
      </template>
    </el-dialog>

    <!-- 新建标签对话框 -->
    <el-dialog v-model="tagDialog" title="新建标签" width="480px">
      <el-form :model="tagForm" label-width="100px">
        <el-form-item label="标签名" required><el-input v-model="tagForm.tag_name" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="tagForm.description" placeholder="处理建议/指导说明" /></el-form-item>
        <el-form-item label="颜色">
          <el-color-picker v-model="tagForm.color" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tagDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleTagCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Upload } from '@element-plus/icons-vue'
import { listTags, createTag, tagStats, batchImport, listTagBatches } from '@/api/device'
import dayjs from 'dayjs'

const tags = ref([])
const stats = ref([])
const batches = ref([])
const loading = ref(false)
const statsLoading = ref(false)
const batchesLoading = ref(false)
const submitting = ref(false)
const importVisible = ref(false)
const tagDialog = ref(false)
const importResult = ref(null)
const importForm = reactive({ tag_id: null, title: '', snText: '', note: '' })
const tagForm = reactive({ tag_name: '', description: '', color: '#409eff' })
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

async function loadTags() {
  loading.value = true
  try {
    const res = await listTags()
    tags.value = res.data || []
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  statsLoading.value = true
  try {
    const res = await tagStats()
    stats.value = res.data || []
  } finally {
    statsLoading.value = false
  }
}

async function loadBatches() {
  batchesLoading.value = true
  try {
    const res = await listTagBatches({ page: 1, page_size: 50 })
    batches.value = res.data.items || []
  } finally {
    batchesLoading.value = false
  }
}

function openImport() {
  importForm.tag_id = tags.value[0]?.id || null
  importForm.title = ''
  importForm.snText = ''
  importForm.note = ''
  importResult.value = null
  importVisible.value = true
}

async function handleImport() {
  if (!importForm.tag_id) {
    ElMessage.warning('请选择标签')
    return
  }
  const sns = importForm.snText.split(/[\n,，\s]+/).map((s) => s.trim()).filter(Boolean)
  if (!sns.length) {
    ElMessage.warning('请输入SN清单')
    return
  }
  submitting.value = true
  try {
    const res = await batchImport({
      tag_id: importForm.tag_id,
      title: importForm.title || null,
      note: importForm.note || null,
      sn_list: sns,
    })
    importResult.value = res.data
    ElMessage.success(`导入完成: 成功 ${res.data.matched} / 无效 ${res.data.invalid_count}`)
    loadStats()
    loadBatches()
  } finally {
    submitting.value = false
  }
}

function openTagCreate() {
  tagForm.tag_name = ''
  tagForm.description = ''
  tagForm.color = '#409eff'
  tagDialog.value = true
}

async function handleTagCreate() {
  if (!tagForm.tag_name.trim()) {
    ElMessage.warning('请输入标签名')
    return
  }
  submitting.value = true
  try {
    await createTag({ tag_name: tagForm.tag_name, description: tagForm.description, color: tagForm.color, tag_type: 'custom' })
    ElMessage.success('创建成功')
    tagDialog.value = false
    loadTags()
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadTags()
  loadStats()
  loadBatches()
})
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.mb-8 { margin-bottom: 8px; }
.mt-8 { margin-top: 8px; }
.mt-16 { margin-top: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.color-dot { display: inline-block; width: 14px; height: 14px; border-radius: 4px; }
.invalid-list { color: #f56c6c; font-size: 12px; word-break: break-all; }
</style>
