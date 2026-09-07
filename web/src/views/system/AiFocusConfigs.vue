<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">AI 助手提示词</div>
        <div class="page-sub">经营数据 AI 助手 · 关注点/分析视角配置（注入 Prompt 的建议性指导，不覆盖口径卡/铁律）</div>
      </div>
      <div>
        <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建关注点</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        说明：这里配置的是 AI 分析时的<b>关注点/分析视角</b>（如"退款关注涉诉率、投诉关注积压"）。仅作为模型组织回答时的侧重参考；
        指标口径与防幻觉铁律仍由系统内置（不可在此修改）。<code>scope</code> 决定命中哪类问题：global=通用恒带 / refund=退款 / complaint=投诉 / reception=接待。
      </template>
    </el-alert>

    <el-card v-loading="loading">
      <el-table :data="items" border size="small">
        <el-table-column prop="sort_order" label="排序" width="70" />
        <el-table-column label="scope" width="110">
          <template #default="{ row }">
            <el-tag :type="scopeTagType(row.scope_key)" size="small" effect="plain">{{ row.scope_key }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="130" />
        <el-table-column prop="content" label="关注点内容" min-width="360" show-overflow-tooltip />
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" size="small" @change="(v) => toggleEnabled(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="更新人" width="90">
          <template #default="{ row }">{{ row.updated_by || row.created_by || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm title="删除该关注点? 删除后不再注入 AI 提示词" @confirm="remove(row)">
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑关注点' : '新建关注点'" width="600px" :close-on-click-modal="false">
      <el-form label-width="90px" label-position="right">
        <el-form-item v-if="!editingId" label="code" required>
          <el-input v-model="form.code" placeholder="唯一标识, 如 focus-refund-litigation" maxlength="32" />
        </el-form-item>
        <el-form-item label="scope" required>
          <el-select v-model="form.scope_key" style="width: 100%">
            <el-option label="global(通用, 恒带)" value="global" />
            <el-option label="refund(退款)" value="refund" />
            <el-option label="complaint(投诉)" value="complaint" />
            <el-option label="reception(接待)" value="reception" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="如: 退款关注点" maxlength="64" />
        </el-form-item>
        <el-form-item label="内容" required>
          <el-input v-model="form.content" type="textarea" :rows="5" placeholder="喂给 AI 的关注点/分析视角指导文本…" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" :max="9999" />
          <div class="sub-tip">数字小在前；同 scope 内按此顺序拼接</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listAiFocus, createAiFocus, updateAiFocus, deleteAiFocus } from '@/api/aiFocus'

const loading = ref(false)
const items = ref([])
const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref(null)
const form = reactive({ code: '', scope_key: 'global', title: '', content: '', sort_order: 0 })

function scopeTagType(s) {
  return { global: 'success', refund: 'warning', complaint: 'danger', reception: 'primary' }[s] || 'info'
}

async function load() {
  loading.value = true
  try {
    const res = await listAiFocus()
    items.value = res.data || []
  } catch (e) { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, { code: '', scope_key: 'global', title: '', content: '', sort_order: 0 })
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    code: row.code, scope_key: row.scope_key, title: row.title,
    content: row.content, sort_order: row.sort_order,
  })
  dialogVisible.value = true
}

async function save() {
  if (!form.title.trim()) { ElMessage.warning('请填写标题'); return }
  if (!form.content.trim()) { ElMessage.warning('请填写内容'); return }
  if (!editingId.value && !form.code.trim()) { ElMessage.warning('请填写 code'); return }
  saving.value = true
  try {
    if (editingId.value) {
      // code 不可改; scope_key 可改
      await updateAiFocus(editingId.value, {
        scope_key: form.scope_key, title: form.title, content: form.content, sort_order: form.sort_order,
      })
    } else {
      await createAiFocus({ ...form })
    }
    ElMessage.success('已保存')
    dialogVisible.value = false
    load()
  } catch (e) { /* 已提示 */ } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, v) {
  try {
    await updateAiFocus(row.id, { enabled: v })
    row.enabled = v
    ElMessage.success(v ? '已启用' : '已停用')
  } catch (e) { /* 已提示 */ }
}

async function remove(row) {
  try {
    await deleteAiFocus(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) { /* 已提示 */ }
}

onMounted(load)
</script>

<style scoped>
.sub-tip {
  color: #909399;
  font-size: 12px;
  line-height: 1.6;
  margin-top: 2px;
}
</style>
