<template>
  <div :class="embedded ? 'embedded-wrap' : 'page-container'">
    <div v-if="!embedded" class="page-title">同步设置</div>

    <el-card>
      <template #header>
        <div class="flex justify-between items-center">
          <span>同步源配置（分组管理）</span>
          <el-button v-if="!embedded" text type="primary" @click="$router.push('/data/fetch')">返回 BMS 同步</el-button>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="mb-16">
        <p class="alert-text">
          按<strong>平台/域名分组</strong>管理：同一 Web 源下的多个数据源共用登录凭证，可在<strong>组级统一配置账号密码</strong>，
          也可对<strong>单个数据源单独配置</strong>（单源优先）。保存后可用「测试登录」验证账号密码是否有效。<br />
          • <strong>BMS</strong>: 不同子域名(wifi/support.cloud-cat.cn)可能用不同账号。Playwright 页面抓取模式。<br />
          • <strong>SCRM</strong>: 支持 OpenAPI(默认,稳定) / Playwright 网页(数学验证码兜底) / 双模式。<br />
          「默认同步」控制是否纳入定时同步与手动同步的默认勾选。账号/密码留空表示不修改原值。
        </p>
      </el-alert>

      <div v-loading="loading">
        <!-- 分组卡片 -->
        <div v-for="g in groups" :key="g.group_key" class="group-card mb-16">
          <div class="group-head">
            <div class="group-title">
              <span class="group-name">{{ g.group_name || g.group_key }}</span>
              <el-tag v-if="g.group_key" size="small" type="info" effect="plain">{{ g.group_key }}</el-tag>
              <span v-if="g.base_url" class="group-url">{{ g.base_url }}</span>
              <el-tag v-if="g.has_credentials" size="small" type="success">组凭证已配置</el-tag>
              <el-tag v-else size="small" type="danger">组凭证未配置</el-tag>
              <el-tag size="small" type="info" effect="plain">{{ g.sources.length }} 个源</el-tag>
            </div>
            <div class="group-actions">
              <el-button size="small" type="primary" plain @click="openGroupEdit(g)">统一配置凭证</el-button>
              <el-button size="small" :loading="groupTesting[g.group_key]" @click="testGroupLogin(g)">测试组登录</el-button>
            </div>
          </div>

          <el-table :data="g.sources" border size="small">
            <el-table-column prop="name" label="数据源" width="160" />
            <el-table-column prop="data_type" label="代码" width="130" />
            <el-table-column label="抓取模式" width="130">
              <template #default="{ row }">
                <el-tag v-if="isScrmSource(row)" size="small" :type="scrmModeTagType(row.fetch_mode)" effect="light">{{ scrmModeLabel(row.fetch_mode) }}</el-tag>
                <el-tag v-else-if="row.fetch_mode === 'http'" size="small" type="warning" effect="light">HTTP API</el-tag>
                <el-tag v-else size="small" effect="light">Playwright</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="凭证" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.has_credentials" type="success" size="small">单源已配置</el-tag>
                <el-tag v-else-if="g.has_credentials" type="primary" size="small" effect="plain">继承组</el-tag>
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
            <el-table-column label="操作" width="190" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openEdit(row, g.group_key, g.has_credentials)">编辑</el-button>
                <el-button link size="small" :loading="singleTesting[row.data_type]" @click="testSingleLogin(row)">测试</el-button>
                <template v-if="row.requires_captcha">
                  <el-button link type="warning" size="small" :loading="loginStates[row.data_type]?.busy" @click="openCaptchaLogin(row)">
                    {{ loginStates[row.data_type]?.has_storage_state ? '重新登录' : '验证码登录' }}
                  </el-button>
                  <el-button v-if="loginStates[row.data_type]?.has_storage_state" link type="danger" size="small" @click="handleLogout(row)">退出</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <el-empty v-if="!loading && !groups.length" description="暂无同步源配置" :image-size="60" />
      </div>
    </el-card>

    <!-- 组配置弹窗(统一凭证) -->
    <el-dialog v-model="groupDialogVisible" :title="`统一配置凭证 - ${groupEdit.group_name || groupEdit.group_key}`" width="480px">
      <el-alert type="info" :closable="false" show-icon class="mb-12" size="small">
        组级账号密码对该组下<strong>未单独配置凭证</strong>的源生效(同域共享登录态)。单个源单独配置后优先于组。
      </el-alert>
      <el-form :model="groupEdit" label-width="90px">
        <el-form-item label="组名">
          <el-input v-model="groupEdit.group_name" />
        </el-form-item>
        <el-form-item label="域名">
          <el-input v-model="groupEdit.base_url" placeholder="如 https://wifi.cloud-cat.cn/bms" />
        </el-form-item>
        <el-form-item label="组账号">
          <el-input v-model="groupEdit.username" :placeholder="groupEdit.has_credentials ? '已配置, 留空=不改' : '组级登录账号'" />
        </el-form-item>
        <el-form-item label="组密码">
          <el-input v-model="groupEdit.password" type="password" show-password :placeholder="groupEdit.has_credentials ? '已配置, 留空=不改' : '组级登录密码'" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="groupDialogVisible = false">取消</el-button>
        <el-button :loading="groupSaving" @click="saveGroup">保存</el-button>
        <el-button type="primary" :loading="groupTesting[groupEdit.group_key]" @click="saveGroup(true)">保存并测试</el-button>
      </template>
    </el-dialog>

    <!-- 单源编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="`编辑同步源 - ${editForm.name || editForm.data_type}`" width="560px">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="显示名">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="基础地址">
          <el-input
            v-model="editForm.base_url"
            :placeholder="isScrmSource(editForm) ? '如 https://your-scrm.example.com' : '如 https://support.cloud-cat.cn/bms'"
          />
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
        <el-form-item v-if="isScrmSource(editForm)" label="抓取模式">
          <el-select v-model="editForm.fetch_mode" style="width: 100%" @change="onFetchModeChange">
            <el-option label="OpenAPI (默认,稳定)" value="openapi" />
            <el-option label="Playwright 网页(数学验证码兜底)" value="playwright" />
            <el-option label="双模式(OpenAPI优先,失败自动回退网页)" value="dual" />
          </el-select>
          <div class="form-hint mt-8">
            切换到 Playwright/双模式 后,需保存配置并点击「登录」完成网页验证码一次性登录,
            账号密码也会被解释为网页登录凭证。
          </div>
        </el-form-item>
        <el-form-item label="默认同步">
          <el-switch v-model="editForm.default_sync" active-text="纳入定时/默认勾选" inactive-text="不纳入" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item :label="scrmLabel[0]">
          <el-input v-model="editForm.username" :placeholder="scrmUsernamePlaceholder" />
        </el-form-item>
        <el-form-item :label="scrmLabel[1]">
          <el-input v-model="editForm.password" type="password" show-password :placeholder="scrmPasswordPlaceholder" />
        </el-form-item>
        <!-- D6(2026-09-05, #19): 分组凭证复用提示 + 单源凭证同步到组 -->
        <el-form-item v-if="!isScrmSource(editForm) && editForm.group_key" label="分组凭证">
          <div style="width: 100%">
            <el-alert
              :type="editForm.group_has_credentials ? 'success' : 'info'"
              :closable="false"
              show-icon
              class="mb-8"
              size="small"
            >
              <template #title>
                已归组 <strong>{{ editForm.group_key }}</strong>
                <span v-if="editForm.group_has_credentials">(组凭证已配置,本源可留空自动复用)</span>
                <span v-else>(组凭证未配置)</span>
              </template>
            </el-alert>
            <el-checkbox v-model="editForm.sync_to_group">
              同时设为组凭证(作用于同组所有源, 免逐源配置)
            </el-checkbox>
          </div>
        </el-form-item>
        <template v-if="isScrmSource(editForm) && editForm.fetch_mode === 'dual'">
          <el-divider content-position="left">Playwright 网页登录凭证(双模式下独立配置)</el-divider>
          <el-alert type="warning" :closable="false" show-icon class="mb-8" size="small">
            下方是 SCRM 后台管理页面实际的「登录账号/密码」(手机号/邮箱 + 登录密码),<strong>不要填 ClientID/ClientSecret</strong>。
            留空表示不修改之前保存过的值。
          </el-alert>
          <el-form-item label="SCRM 网页账号">
            <el-input v-model="editForm.web_username" :placeholder="webUsernamePlaceholder" />
          </el-form-item>
          <el-form-item label="SCRM 网页密码">
            <el-input v-model="editForm.web_password" type="password" show-password :placeholder="webPasswordPlaceholder" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button :loading="saving" @click="handleSave(false)">保存</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave(true)">保存并测试</el-button>
      </template>
    </el-dialog>

    <!-- 验证码登录对话框(不切换页面,直接在此输入验证码) -->
    <el-dialog
      v-model="captchaDialogVisible"
      :title="`${captchaRow?.name || captchaRow?.data_type} - ${isMathCaptcha ? '数学验证码' : '验证码'}登录`"
      width="480px"
      :close-on-click-modal="false"
      @closed="resetCaptchaForm"
    >
      <el-alert :type="isMathCaptcha ? 'warning' : 'info'" :closable="false" show-icon class="mb-16" size="small">
        <template v-if="isMathCaptcha">
          SCRM 登录使用<strong>数学计算式</strong>验证码。下方图片显示的是如「7*0=?」的算式,
          请在输入框中填入<strong>计算结果数字</strong>(如 0、63)。<br />
          若 OCR 自动识别出了答案,已经帮你填好了,请核对后直接点「提交并登录」。
        </template>
        <template v-else>
          请读取图片中的验证码后填入下方输入框,点击提交后会自动填账号密码并登录。
          登录成功后会自动保存登录态,后续同步无需再次输入验证码。
        </template>
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
            title="点击图片重试(注意:后端会话会重新生成)"
            @click="refreshCaptcha"
          />
          <div v-else class="captcha-empty">验证码加载失败</div>
        </div>
        <el-button link type="primary" size="small" :loading="captchaLoading" @click="refreshCaptcha">
          看不清,换一张
        </el-button>
      </div>
      <el-form :model="captchaForm" label-width="80px" class="mt-16">
        <el-form-item :label="isMathCaptcha ? '算式答案' : '验证码'">
          <el-input
            v-model="captchaForm.captcha"
            :placeholder="isMathCaptcha ? '请输入计算结果(整数),OCR已自动填充' : '请输入图片中的验证码'"
            :maxlength="isMathCaptcha ? 16 : 8"
            clearable
            @keyup.enter="submitCaptcha"
          />
        </el-form-item>
        <el-form-item v-if="captchaHint" label="提示">
          <span class="hint-text" v-html="captchaHintHtml"></span>
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
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'

defineProps({
  embedded: { type: Boolean, default: false },
})
import { listSyncSources, updateSyncSource } from '@/api/system'
import { listSourceGroups, updateSourceGroup, testSourceLogin } from '@/api/sourceGroup'
import {
  captchaLoginCheck,
  captchaLoginStart,
  captchaLoginSubmit,
  captchaLoginLogout,
} from '@/api/fetch'

const loading = ref(false)
const saving = ref(false)
const sources = ref([])
const groups = ref([])
const dialogVisible = ref(false)
const groupDialogVisible = ref(false)
const groupSaving = ref(false)
const groupTesting = reactive({})
const singleTesting = reactive({})

const editForm = reactive({
  data_type: '',
  name: '',
  base_url: '',
  page_path: '',
  time_labels_text: '',
  default_sync: true,
  enabled: true,
  fetch_mode: 'openapi',
  username: '',
  password: '',
  web_username: '',
  web_password: '',
  // D6(2026-09-05, #19): 分组凭证增强
  group_key: '',
  group_has_credentials: false,
  sync_to_group: false,
})
const groupEdit = reactive({
  group_key: '',
  group_name: '',
  base_url: '',
  username: '',
  password: '',
  has_credentials: false,
})

// ============== 工具: SCRM / math 验证码 ==============
function isScrmSource(row) {
  return !!(row && typeof row.data_type === 'string' && row.data_type.startsWith('scrm_'))
}
function scrmModeLabel(mode) {
  return { openapi: 'OpenAPI', playwright: '网页', dual: '双模式' }[mode] || '—'
}
function scrmModeTagType(mode) {
  return { openapi: '', playwright: 'warning', dual: 'success' }[mode] || 'info'
}
const scrmLabel = computed(() => {
  if (!isScrmSource(editForm)) return ['BMS 账号', 'BMS 密码']
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') return ['Client ID', 'Client Secret']
  if (mode === 'playwright') return ['SCRM 网页账号', 'SCRM 网页密码']
  return ['OpenAPI Client ID', 'OpenAPI Client Secret']
})
const _credPlaceholder = (label, configured, emptyHint) => {
  if (configured) return `${label}(已配置,留空=不修改,输入新值覆盖)`
  return emptyHint || `请输入${label},留空表示不修改`
}
const scrmUsernamePlaceholder = computed(() => {
  if (!isScrmSource(editForm)) return _credPlaceholder('BMS 登录账号', credentialsState.username_configured, '留空表示不修改原值')
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') return _credPlaceholder('SCRM OpenAPI Client ID', credentialsState.username_configured, 'SCRM OpenAPI Client ID,留空表示不修改')
  if (mode === 'playwright') return _credPlaceholder('SCRM 网页登录账号', credentialsState.username_configured, 'SCRM 后台真实登录账号(手机号/邮箱),不要填 ClientID')
  return _credPlaceholder('OpenAPI Client ID', credentialsState.username_configured, '双模式主字段填 OpenAPI Client ID,SCRM 网页账号请填下面独立的 web_ 字段')
})
const scrmPasswordPlaceholder = computed(() => {
  if (!isScrmSource(editForm)) return _credPlaceholder('BMS 登录密码', credentialsState.password_configured, '留空表示不修改原值')
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') return _credPlaceholder('SCRM OpenAPI Client Secret', credentialsState.password_configured, 'SCRM OpenAPI Client Secret,留空表示不修改')
  if (mode === 'playwright') return _credPlaceholder('SCRM 网页登录密码', credentialsState.password_configured, 'SCRM 后台登录密码,不要填 ClientSecret')
  return _credPlaceholder('OpenAPI Client Secret', credentialsState.password_configured, '双模式主字段填 OpenAPI Client Secret,SCRM 网页密码请填下面独立的 web_ 字段')
})
const webUsernamePlaceholder = computed(() =>
  _credPlaceholder('SCRM 网页登录账号(手机号/邮箱)', credentialsState.web_username_configured, 'SCRM 后台管理页面实际登录用的账号,不要填 ClientID;留空=不改'))
const webPasswordPlaceholder = computed(() =>
  _credPlaceholder('SCRM 网页登录密码', credentialsState.web_password_configured, 'SCRM 后台管理页面实际登录用的密码;留空=不改'))
function onFetchModeChange() {}

// ============== 验证码登录态 ==============
const isMathCaptcha = computed(
  () => !!(captchaRow.value && isScrmSource(captchaRow.value) && captchaRow.value?.captcha_type === 'math'),
)
const captchaHintHtml = computed(() => {
  const t = captchaHint.value || ''
  if (!t) return ''
  const escaped = t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped.replace(/'(\d+)'/g, ' <b style="color:#409eff;font-weight:600">\'$1\'</b> ')
})
const loginStates = reactive({})
const captchaDialogVisible = ref(false)
const captchaRow = ref(null)
const captchaLoading = ref(false)
const captchaSubmitting = ref(false)
const captchaImage = ref('')
const captchaHint = ref('')
const captchaForm = reactive({ sessionId: '', captcha: '' })

onMounted(() => {
  loadSources()
})

async function loadSources() {
  loading.value = true
  try {
    const [groupsRes, sourcesRes] = await Promise.all([listSourceGroups(), listSyncSources()])
    const srcMap = {}
    ;(sourcesRes.data || []).forEach(s => { srcMap[s.data_type] = s })
    // 合并: 组内源补充完整配置字段(供编辑弹窗)
    groups.value = (groupsRes.data || []).map(g => ({
      ...g,
      sources: (g.sources || []).map(s => ({ ...s, ...(srcMap[s.data_type] || {}) })),
    }))
    sources.value = sourcesRes.data || []
    await Promise.all(
      (sources.value || []).filter(r => r.requires_captcha).map(r => loadLoginState(r.data_type)),
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
  } catch (e) {
    loginStates[dataType] = loginStates[dataType] || { has_storage_state: false, busy: false }
  } finally {
    loginStates[dataType].busy = false
  }
}

const credentialsState = reactive({
  username_configured: false,
  password_configured: false,
  web_username_configured: false,
  web_password_configured: false,
})

// ============== 组配置 ==============
function openGroupEdit(g) {
  groupEdit.group_key = g.group_key
  groupEdit.group_name = g.group_name || g.group_key
  groupEdit.base_url = g.base_url || ''
  groupEdit.username = ''
  groupEdit.password = ''
  groupEdit.has_credentials = !!g.has_credentials
  groupDialogVisible.value = true
}

async function saveGroup(andTest = false) {
  groupSaving.value = true
  try {
    const payload = {}
    if (groupEdit.group_name) payload.group_name = groupEdit.group_name
    if (groupEdit.base_url) payload.base_url = groupEdit.base_url
    if (groupEdit.username) payload.username = groupEdit.username
    if (groupEdit.password) payload.password = groupEdit.password
    await updateSourceGroup(groupEdit.group_key, payload)
    ElMessage.success('组配置已保存')
    groupDialogVisible.value = false
    await loadSources()
    if (andTest) {
      // 组内第一个有凭证的源做代表测试
      const g = groups.value.find(x => x.group_key === groupEdit.group_key)
      const src = g?.sources?.find(s => !s.requires_captcha) || g?.sources?.[0]
      if (src) await runTest(src.data_type, groupTesting)
    }
  } catch (e) {
    ElMessage.error(`保存失败: ${e.message}`)
  } finally {
    groupSaving.value = false
  }
}

async function testGroupLogin(g) {
  const src = g.sources.find(s => !s.requires_captcha) || g.sources[0]
  if (!src) return
  await runTest(src.data_type, groupTesting, g.group_key)
}

async function testSingleLogin(row) {
  await runTest(row.data_type, singleTesting)
}

async function runTest(dataType, stateMap, key) {
  const k = key || dataType
  stateMap[k] = true
  try {
    const res = await testSourceLogin(dataType, {})
    if (res.data?.success) {
      ElMessage.success(res.message || '登录成功')
    } else {
      ElMessageBox.alert(res.message || '登录失败', '验证结果', { type: 'warning', confirmButtonText: '确定' })
    }
  } catch (e) {
    ElMessage.error(`验证失败: ${e.message}`)
  } finally {
    stateMap[k] = false
  }
}

// ============== 单源编辑 ==============
function openEdit(row, groupKey = '', groupHasCreds = false) {
  editForm.data_type = row.data_type
  editForm.name = row.name
  editForm.base_url = row.base_url
  editForm.page_path = row.page_path
  editForm.time_labels_text = (row.time_labels || []).join(',')
  editForm.default_sync = row.default_sync
  editForm.enabled = row.enabled
  editForm.fetch_mode = isScrmSource(row) ? (row.fetch_mode || 'openapi') : 'playwright'
  credentialsState.username_configured = !!(row.username || row.has_credentials)
  credentialsState.password_configured = !!(row.password || row.has_credentials)
  // D6: 记录归属组 + 组凭证状态(从组卡片上下文传入)
  editForm.group_key = groupKey || row.group_key || ''
  editForm.group_has_credentials = !!(groupHasCreds || row.group_has_credentials)
  editForm.sync_to_group = false
  const opts = (typeof row.options === 'object' && row.options) ? row.options : {}
  credentialsState.web_username_configured = !!(opts.web_username_configured || opts.web_username)
  credentialsState.web_password_configured = !!(opts.web_password_configured || opts.web_password)
  editForm.username = ''
  editForm.password = ''
  editForm.web_username = ''
  editForm.web_password = ''
  dialogVisible.value = true
}

async function handleSave(andTest = false) {
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
    // D6: 填了凭证且勾选"同步到组" → 后端把凭证写入该源所属组(作用于同组所有源)
    if (editForm.username && editForm.sync_to_group) payload.sync_to_group = true
    if (isScrmSource(editForm) && editForm.fetch_mode) payload.fetch_mode = editForm.fetch_mode
    if (editForm.username) payload.username = editForm.username
    if (editForm.password) payload.password = editForm.password
    if (isScrmSource(editForm) && editForm.fetch_mode === 'dual') {
      const optsPatch = {}
      if (editForm.web_username) optsPatch.web_username = editForm.web_username
      if (editForm.web_password) optsPatch.web_password = editForm.web_password
      if (Object.keys(optsPatch).length > 0) payload.options = optsPatch
    }
    await updateSyncSource(editForm.data_type, payload)
    ElMessage.success(editForm.sync_to_group ? '保存成功,并已同步到同组凭证' : '保存成功')
    dialogVisible.value = false
    await loadSources()
    if (andTest) await runTest(editForm.data_type, singleTesting)
  } catch (e) {
    ElMessage.error(`保存失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}

// ============== 验证码登录流程 ==============
function openCaptchaLogin(row) {
  if (!row?.has_credentials) {
    ElMessage.warning('请先配置账号密码,再进行验证码登录')
    openEdit(row)
    return
  }
  captchaRow.value = row
  captchaDialogVisible.value = true
  resetCaptchaForm()
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
    if (d?.auto_login_success) {
      ElMessage.success(d?.auto_login_message || d?.message || '后端已自动识别并登录成功')
      captchaDialogVisible.value = false
      if (captchaRow.value) await loadLoginState(captchaRow.value.data_type)
      return
    }
    const captchaType = d?.captcha_type || (isScrmSource(captchaRow.value) ? 'math' : 'text')
    const recognized = d?.ocr_recognized_text
    if (captchaType === 'math' && recognized && !captchaForm.captcha) {
      captchaForm.captcha = recognized
      const extra = `识别出的答案已自动填入: '${recognized}' —— 请核对图片是否一致再提交。`
      captchaHint.value = captchaHint.value ? captchaHint.value + ' | ' + extra : extra
    }
  } catch (e) {
    ElMessage.error(`获取验证码失败: ${e.message}`)
  } finally {
    captchaLoading.value = false
  }
}

function refreshCaptcha() {
  startCaptchaLogin()
}

async function submitCaptcha() {
  if (!captchaForm.sessionId) {
    ElMessage.warning('会话已失效,请重新获取验证码')
    return
  }
  if (!captchaForm.captcha.trim()) {
    ElMessage.warning(isMathCaptcha.value ? '请输入数学计算的答案' : '请输入验证码')
    return
  }
  captchaSubmitting.value = true
  try {
    const res = await captchaLoginSubmit(captchaForm.sessionId, captchaForm.captcha.trim())
    const d = res.data
    if (d?.success) {
      ElMessage.success(d?.message || '登录成功')
      captchaDialogVisible.value = false
      if (captchaRow.value) await loadLoginState(captchaRow.value.data_type)
    } else {
      ElMessageBox.alert(
        d?.message || '登录失败,可能是验证码错误或账号密码错误',
        '登录失败',
        { type: 'warning', confirmButtonText: '确定' },
      )
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
  line-height: 1.6;
}
.form-hint {
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}
.mb-16 {
  margin-bottom: 16px;
}
.mb-12 {
  margin-bottom: 12px;
}
.mt-8 {
  margin-top: 8px;
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
/* 分组卡片 */
.group-card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}
.group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e5e7eb;
  flex-wrap: wrap;
}
.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.group-name {
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
}
.group-url {
  font-size: 12px;
  color: #9ca3af;
}
.group-actions {
  display: flex;
  gap: 8px;
}
.group-card .el-table {
  --el-table-header-bg-color: #fbfcfe;
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
