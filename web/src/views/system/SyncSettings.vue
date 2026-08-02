<template>
  <div :class="embedded ? 'embedded-wrap' : 'page-container'">
    <div v-if="!embedded" class="page-title">同步设置</div>

    <el-card>
      <template #header>
        <div class="flex justify-between items-center">
          <span>BMS 同步源配置</span>
          <el-button v-if="!embedded" text type="primary" @click="$router.push('/data/fetch')">返回 BMS 同步</el-button>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="mb-16">
        <p class="alert-text">
          每个数据源独立配置 BMS 地址与账号密码(加密存储)。不同子域名(wifi/support.cloud-cat.cn)可能用不同账号。
          「默认同步」控制是否纳入定时同步与手动同步的默认勾选。账号/密码留空表示不修改原值。
        </p>
      </el-alert>

      <el-table :data="sources" border size="small" v-loading="loading">
        <el-table-column prop="name" label="数据源" width="130" />
        <el-table-column prop="data_type" label="代码" width="110" />
        <el-table-column label="凭证" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.has_credentials" type="success" size="small">已配置</el-tag>
            <el-tag v-else type="danger" size="small">未配置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="默认同步" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.default_sync" type="primary" size="small">纳入</el-tag>
            <el-tag v-else type="info" size="small">不纳入</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.enabled" type="success" size="small">是</el-tag>
            <el-tag v-else type="info" size="small">否</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="BMS 页面" show-overflow-tooltip>
          <template #default="{ row }">{{ row.base_url }}{{ row.page_path }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="`编辑同步源 - ${editForm.name || editForm.data_type}`" width="560px">
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="显示名">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="基础地址">
          <el-input v-model="editForm.base_url" placeholder="如 https://support.cloud-cat.cn/bms" />
        </el-form-item>
        <el-form-item label="页面路径">
          <el-input v-model="editForm.page_path" placeholder="如 /index.html#/device/device-combo" />
        </el-form-item>
        <el-form-item label="时间标签">
          <el-input
            v-model="editForm.time_labels_text"
            placeholder="多个时间筛选框时按标签精确填,逗号分隔(如: 起始完成时间,结束完成时间);单个日期范围留空"
          />
        </el-form-item>
        <el-form-item label="默认同步">
          <el-switch v-model="editForm.default_sync" active-text="纳入定时/默认勾选" inactive-text="不纳入" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="BMS 账号">
          <el-input v-model="editForm.username" placeholder="留空表示不修改原值" />
        </el-form-item>
        <el-form-item label="BMS 密码">
          <el-input v-model="editForm.password" type="password" show-password placeholder="留空表示不修改原值" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

defineProps({
  embedded: { type: Boolean, default: false },
})
import { listSyncSources, updateSyncSource } from '@/api/system'

const loading = ref(false)
const saving = ref(false)
const sources = ref([])
const dialogVisible = ref(false)

const editForm = reactive({
  data_type: '',
  name: '',
  base_url: '',
  page_path: '',
  time_labels_text: '',
  default_sync: true,
  enabled: true,
  username: '',
  password: '',
})

onMounted(() => {
  loadSources()
})

async function loadSources() {
  loading.value = true
  try {
    const res = await listSyncSources()
    sources.value = res.data
  } catch (e) {
    ElMessage.error(`加载失败: ${e.message}`)
  } finally {
    loading.value = false
  }
}

function openEdit(row) {
  editForm.data_type = row.data_type
  editForm.name = row.name
  editForm.base_url = row.base_url
  editForm.page_path = row.page_path
  editForm.time_labels_text = (row.time_labels || []).join(',')
  editForm.default_sync = row.default_sync
  editForm.enabled = row.enabled
  editForm.username = ''
  editForm.password = ''
  dialogVisible.value = true
}

async function handleSave() {
  saving.value = true
  try {
    const payload = {
      name: editForm.name,
      base_url: editForm.base_url,
      page_path: editForm.page_path,
      time_labels: editForm.time_labels_text
        ? editForm.time_labels_text.split(',').map(s => s.trim()).filter(Boolean)
        : [],
      default_sync: editForm.default_sync,
      enabled: editForm.enabled,
    }
    // 账号密码仅当非空时传(后端:空表示不改)
    if (editForm.username) payload.username = editForm.username
    if (editForm.password) payload.password = editForm.password

    await updateSyncSource(editForm.data_type, payload)
    ElMessage.success('保存成功')
    dialogVisible.value = false
    await loadSources()
  } catch (e) {
    ElMessage.error(`保存失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.alert-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}
.mb-16 {
  margin-bottom: 16px;
}
</style>
