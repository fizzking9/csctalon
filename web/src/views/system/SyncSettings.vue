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
        <el-table-column v-if="hasCaptchaSources" label="验证码登录" width="180">
          <template #default="{ row }">
            <template v-if="row.requires_captcha">
              <div class="captcha-login-cell">
                <el-tag
                  :type="loginStates[row.data_type]?.has_storage_state ? 'success' : 'warning'"
                  size="small"
                  effect="light"
                  class="mr-8"
                >
                  {{ loginStates[row.data_type]?.has_storage_state ? '已登录' : '未登录' }}
                </el-tag>
                <el-button
                  link
                  type="primary"
                  size="small"
                  :loading="loginStates[row.data_type]?.busy"
                  @click="openCaptchaLogin(row)"
                >
                  {{ loginStates[row.data_type]?.has_storage_state ? '重新登录' : '登录' }}
                </el-button>
                <el-button
                  v-if="loginStates[row.data_type]?.has_storage_state"
                  link
                  type="danger"
                  size="small"
                  @click="handleLogout(row)"
                >
                  退出
                </el-button>
              </div>
            </template>
            <span v-else class="muted">—</span>
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

    <!-- 验证码登录对话框(不切换页面,直接在此输入验证码) -->
    <el-dialog
      v-model="captchaDialogVisible"
      :title="`${captchaRow?.name || captchaRow?.data_type} - 验证码登录`"
      width="480px"
      :close-on-click-modal="false"
      @closed="resetCaptchaForm"
    >
      <el-alert type="info" :closable="false" show-icon class="mb-16" size="small">
        请读取图片中的验证码后填入下方输入框,点击提交后会自动填账号密码并登录。
        登录成功后会自动保存登录态,后续同步无需再次输入验证码。
      </el-alert>
      <div v-if="captchaLoading" class="captcha-loading">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在启动浏览器并加载验证码...(最多等待 90 秒)</span>
      </div>
      <div v-else class="captcha-box">
        <div class="captcha-img-wrap">
          <img
            v-if="captchaImage"
            :src="captchaImage"
            alt="验证码"
            class="captcha-img"
            @click="refreshCaptcha"
            title="点击图片重试(注意:后端会话会重新生成)"
          />
          <div v-else class="captcha-empty">验证码加载失败</div>
        </div>
        <el-button link type="primary" size="small" :loading="captchaLoading" @click="refreshCaptcha">
          看不清,换一张
        </el-button>
      </div>
      <el-form :model="captchaForm" label-width="80px" class="mt-16">
        <el-form-item label="验证码">
          <el-input
            v-model="captchaForm.captcha"
            placeholder="请输入图片中的验证码"
            maxlength="8"
            clearable
            @keyup.enter="submitCaptcha"
          />
        </el-form-item>
        <el-form-item v-if="captchaHint" label="提示">
          <span class="hint-text">{{ captchaHint }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="captchaDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="captchaSubmitting"
          :disabled="!captchaForm.sessionId || !captchaForm.captcha"
          @click="submitCaptcha"
        >
          提交并登录
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'

defineProps({
  embedded: { type: Boolean, default: false },
})
import { listSyncSources, updateSyncSource } from '@/api/system'
import {
  captchaLoginCheck,
  captchaLoginStart,
  captchaLoginSubmit,
  captchaLoginLogout,
} from '@/api/fetch'

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

// 是否包含需要验证码登录的源(控制"验证码登录"列显示)
const hasCaptchaSources = computed(
  () => Array.isArray(sources.value) && sources.value.some(r => r.requires_captcha),
)

// 每个验证码源的登录态 { data_type: { has_storage_state, state_mtime, busy } }
const loginStates = reactive({})

// 验证码登录对话框相关
const captchaDialogVisible = ref(false)
const captchaRow = ref(null)
const captchaLoading = ref(false)
const captchaSubmitting = ref(false)
const captchaImage = ref('')
const captchaHint = ref('')
const captchaForm = reactive({
  sessionId: '',
  captcha: '',
})

onMounted(() => {
  loadSources()
})

async function loadSources() {
  loading.value = true
  try {
    const res = await listSyncSources()
    sources.value = res.data
    // 为需要验证码的源并行拉登录态
    await Promise.all(
      (sources.value || [])
        .filter(r => r.requires_captcha)
        .map(r => loadLoginState(r.data_type)),
    )
  } catch (e) {
    ElMessage.error(`加载失败: ${e.message}`)
  } finally {
    loading.value = false
  }
}

async function loadLoginState(dataType) {
  try {
    if (!loginStates[dataType]) loginStates[dataType] = { busy: false }
    loginStates[dataType].busy = true
    const res = await captchaLoginCheck(dataType)
    const d = res.data
    loginStates[dataType].has_storage_state = !!d?.has_storage_state
    loginStates[dataType].state_mtime = d?.state_mtime || null
    loginStates[dataType].state_path = d?.state_path || null
  } catch (e) {
    // 不打失败提示(接口可能尚未部署),但保留空状态
    loginStates[dataType] = loginStates[dataType] || { has_storage_state: false, busy: false }
  } finally {
    loginStates[dataType].busy = false
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

// ---------- 验证码登录流程(不切页面,在当前对话框完成) ----------

function openCaptchaLogin(row) {
  if (!row?.has_credentials) {
    ElMessage.warning('请先配置账号密码,再进行验证码登录')
    openEdit(row)
    return
  }
  captchaRow.value = row
  captchaDialogVisible.value = true
  resetCaptchaForm()
  // 打开对话框后立即拉取验证码
  startCaptchaLogin()
}

function resetCaptchaForm() {
  captchaForm.sessionId = ''
  captchaForm.captcha = ''
  captchaImage.value = ''
  captchaHint.value = ''
  captchaLoading.value = false
  captchaSubmitting.value = false
}

async function startCaptchaLogin() {
  if (!captchaRow.value) return
  captchaLoading.value = true
  captchaImage.value = ''
  captchaHint.value = ''
  try {
    const res = await captchaLoginStart(captchaRow.value.data_type)
    const d = res.data
    captchaForm.sessionId = d?.session_id || ''
    captchaImage.value = d?.captcha_image || ''
    captchaHint.value = d?.hint || ''
  } catch (e) {
    ElMessage.error(`获取验证码失败: ${e.message}`)
  } finally {
    captchaLoading.value = false
  }
}

function refreshCaptcha() {
  // 重新 start 即可(后端会开新会话)
  startCaptchaLogin()
}

async function submitCaptcha() {
  if (!captchaForm.sessionId) {
    ElMessage.warning('会话已失效,请重新获取验证码')
    return
  }
  if (!captchaForm.captcha.trim()) {
    ElMessage.warning('请输入验证码')
    return
  }
  captchaSubmitting.value = true
  try {
    const res = await captchaLoginSubmit(captchaForm.sessionId, captchaForm.captcha.trim())
    const d = res.data
    if (d?.success) {
      ElMessage.success(d?.message || '登录成功')
      captchaDialogVisible.value = false
      // 刷新登录态展示
      if (captchaRow.value) await loadLoginState(captchaRow.value.data_type)
    } else {
      ElMessageBox.alert(
        d?.message || '登录失败,可能是验证码错误或账号密码错误',
        '登录失败',
        { type: 'warning', confirmButtonText: '确定' },
      )
      // 失败后自动换一张验证码(除非已关闭)
      if (captchaDialogVisible.value) setTimeout(startCaptchaLogin, 400)
    }
  } catch (e) {
    ElMessage.error(`提交失败: ${e.message}`)
  } finally {
    captchaSubmitting.value = false
  }
}

async function handleLogout(row) {
  try {
    await ElMessageBox.confirm(
      `确认清除「${row.name || row.data_type}」的登录态?清除后下次同步需重新输入验证码。`,
      '退出登录',
      { type: 'warning', confirmButtonText: '确认清除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    if (loginStates[row.data_type]) loginStates[row.data_type].busy = true
    await captchaLoginLogout(row.data_type)
    await loadLoginState(row.data_type)
    ElMessage.success('登录态已清除')
  } catch (e) {
    ElMessage.error(`退出失败: ${e.message}`)
  } finally {
    if (loginStates[row.data_type]) loginStates[row.data_type].busy = false
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
.mt-16 {
  margin-top: 16px;
}
.mr-8 {
  margin-right: 8px;
}
.muted {
  color: #909399;
  font-size: 12px;
}
.captcha-login-cell {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.captcha-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  padding: 24px 0;
  color: #909399;
  font-size: 13px;
}
.captcha-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.captcha-img-wrap {
  width: 100%;
  display: flex;
  justify-content: center;
  background: #f5f7fa;
  border: 1px dashed #dcdfe6;
  border-radius: 6px;
  padding: 12px;
  min-height: 90px;
}
.captcha-img {
  max-width: 100%;
  max-height: 90px;
  cursor: pointer;
  border-radius: 4px;
}
.captcha-empty {
  color: #c0c4cc;
  font-size: 13px;
  line-height: 66px;
}
.hint-text {
  color: #909399;
  font-size: 12px;
}
</style>
