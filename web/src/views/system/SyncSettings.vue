<template>
  <div :class="embedded ? 'embedded-wrap' : 'page-container'">
    <div v-if="!embedded" class="page-title">同步设置</div>

    <el-card>
      <template #header>
        <div class="flex justify-between items-center">
          <span>BMS / SCRM 同步源配置</span>
          <el-button v-if="!embedded" text type="primary" @click="$router.push('/data/fetch')">返回 BMS 同步</el-button>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="mb-16">
        <p class="alert-text">
          每个数据源独立配置地址与账号密码(加密存储)。<br/>
          • <strong>BMS</strong>: 不同子域名(wifi/support.cloud-cat.cn)可能用不同账号。Playwright 页面抓取模式。<br/>
          • <strong>SCRM</strong>: 支持三种抓取模式:
          <span style="color:#2f6feb">OpenAPI (默认,稳定)</span> /
          <span style="color:#e6a23c">Playwright 网页(数学验证码兜底)</span> /
          <span style="color:#67c23a">双模式(OpenAPI优先,失败自动回退网页)</span>。<br/>
          「默认同步」控制是否纳入定时同步与手动同步的默认勾选。账号/密码留空表示不修改原值。
        </p>
      </el-alert>

      <el-table :data="sources" border size="small" v-loading="loading">
        <el-table-column prop="name" label="数据源" width="130" />
        <el-table-column prop="data_type" label="代码" width="110" />
        <el-table-column label="抓取模式" width="150">
          <template #default="{ row }">
            <template v-if="isScrmSource(row)">
              <el-tag
                v-if="row.fetch_mode === 'openapi'"
                size="small"
                effect="light"
              >OpenAPI</el-tag>
              <el-tag
                v-else-if="row.fetch_mode === 'playwright'"
                type="warning"
                size="small"
                effect="light"
              >网页(数学验证码)</el-tag>
              <el-tag
                v-else-if="row.fetch_mode === 'dual'"
                type="success"
                size="small"
                effect="light"
              >双模式兜底</el-tag>
              <el-tag v-else size="small" effect="light">—</el-tag>
            </template>
            <el-tag v-else size="small" effect="light">Playwright</el-tag>
          </template>
        </el-table-column>
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
        <!-- 主账号密码: SCRM=ClientID/ClientSecret 或 OpenAPI; BMS=BMS 网页登录账号 -->
        <el-form-item :label="scrmLabel[0]">
          <el-input v-model="editForm.username" :placeholder="scrmUsernamePlaceholder" />
        </el-form-item>
        <el-form-item :label="scrmLabel[1]">
          <el-input v-model="editForm.password" type="password" show-password :placeholder="scrmPasswordPlaceholder" />
        </el-form-item>
        <!-- Playwright / Dual 模式下需要独立再配置一对「网页登录账号密码」(SCRM 专属,和 OpenAPI 凭证分离) -->
        <!-- ⚠️ 2026-08-06: 对齐用户心智模型(和 SyncDashboard.vue 完全一致):
             - Playwright 模式: 主字段 username/password 直接就是「SCRM 网页账号/SCRM 网页密码」,不再额外加一对 web_ 字段
             - Dual 模式(OpenAPI+Playwright 双模式): 主字段是 OpenAPI ClientID/ClientSecret, 下方 web_username/web_password 才是独立的网页登录凭证 -->
        <template v-if="isScrmSource(editForm) && editForm.fetch_mode === 'dual'">
          <el-divider content-position="left">Playwright 网页登录凭证(双模式下独立配置)</el-divider>
          <el-alert type="warning" :closable="false" show-icon class="mb-8" size="small">
            下方是 SCRM 后台管理页面实际的「登录账号/密码」(手机号/邮箱 + 登录密码),<strong>不要填 ClientID/ClientSecret</strong>。
            留空表示不修改之前保存过的值。
          </el-alert>
          <el-form-item label="SCRM 网页账号">
            <el-input
              v-model="editForm.web_username"
              :placeholder="webUsernamePlaceholder"
            />
          </el-form-item>
          <el-form-item label="SCRM 网页密码">
            <el-input
              v-model="editForm.web_password"
              type="password"
              show-password
              :placeholder="webPasswordPlaceholder"
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
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
          请在输入框中填入<strong>计算结果数字</strong>(如 0、63)。<br/>
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
  fetch_mode: 'openapi', // SCRM 模式: openapi / playwright / dual
  username: '',         // SCRM: ClientID 或 OpenAPI 主账号; BMS: BMS 网页登录账号
  password: '',         // SCRM: ClientSecret 或 OpenAPI 主密码; BMS: BMS 网页登录密码
  web_username: '',     // SCRM Playwright/Dual 模式: 网页登录账号(真实手机号/邮箱)
  web_password: '',     // SCRM Playwright/Dual 模式: 网页登录密码
})

// ============== 工具: SCRM / math 验证码 ==============
function isScrmSource(row) {
  return !!(row && typeof row.data_type === 'string' && row.data_type.startsWith('scrm_'))
}
// SCRM 编辑表单: 账号/密码 标签随抓取模式切换
const scrmLabel = computed(() => {
  if (!isScrmSource(editForm)) return ['BMS 账号', 'BMS 密码']
  const mode = editForm.fetch_mode || 'openapi'
  // Playwright 模式: 主字段直接就是网页登录凭证（用户最直观）
  if (mode === 'openapi') return ['Client ID', 'Client Secret']
  if (mode === 'playwright') return ['SCRM 网页账号', 'SCRM 网页密码']
  // Dual 模式: 主字段是 OpenAPI,下方再额外补一对 web_ 网页凭证
  return ['OpenAPI Client ID', 'OpenAPI Client Secret']
})
// 动态 placeholder, 已配置/未配置时提示不同
const _credPlaceholder = (label, configured, emptyHint) => {
  if (configured) return `${label}(已配置,留空=不修改,输入新值覆盖)`
  return emptyHint || `请输入${label},留空表示不修改`
}
const scrmUsernamePlaceholder = computed(() => {
  if (!isScrmSource(editForm)) return _credPlaceholder('BMS 登录账号', credentialsState.username_configured, '留空表示不修改原值')
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') return _credPlaceholder('SCRM OpenAPI Client ID', credentialsState.username_configured, 'SCRM OpenAPI Client ID,留空表示不修改')
  if (mode === 'playwright') return _credPlaceholder('SCRM 网页登录账号', credentialsState.username_configured, 'SCRM 后台真实登录账号(手机号/邮箱),不要填 ClientID')
  // Dual
  return _credPlaceholder('OpenAPI Client ID', credentialsState.username_configured, '双模式主字段填 OpenAPI Client ID,SCRM 网页账号请填下面独立的 web_ 字段')
})
const scrmPasswordPlaceholder = computed(() => {
  if (!isScrmSource(editForm)) return _credPlaceholder('BMS 登录密码', credentialsState.password_configured, '留空表示不修改原值')
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') return _credPlaceholder('SCRM OpenAPI Client Secret', credentialsState.password_configured, 'SCRM OpenAPI Client Secret,留空表示不修改')
  if (mode === 'playwright') return _credPlaceholder('SCRM 网页登录密码', credentialsState.password_configured, 'SCRM 后台登录密码,不要填 ClientSecret')
  // Dual
  return _credPlaceholder('OpenAPI Client Secret', credentialsState.password_configured, '双模式主字段填 OpenAPI Client Secret,SCRM 网页密码请填下面独立的 web_ 字段')
})
// SCRM 网页凭证的 placeholder(动态) —— 仅 Dual 模式显示
const webUsernamePlaceholder = computed(() =>
  _credPlaceholder('SCRM 网页登录账号(手机号/邮箱)', credentialsState.web_username_configured, 'SCRM 后台管理页面实际登录用的账号,不要填 ClientID;留空=不改'))
const webPasswordPlaceholder = computed(() =>
  _credPlaceholder('SCRM 网页登录密码', credentialsState.web_password_configured, 'SCRM 后台管理页面实际登录用的密码;留空=不改'))
function onFetchModeChange() {
  // 切换模式时,提示用户需要重新配置账号密码
  if (dialogVisible.value && isScrmSource(editForm)) {
    // 不做打断式提示,只在 form-hint 里说明
  }
}

// 当前验证码登录框是否为数学验证码
const isMathCaptcha = computed(
  () => !!(captchaRow.value && isScrmSource(captchaRow.value) && captchaRow.value?.captcha_type === 'math'),
)
// 提示文本的 HTML 版本(把后端 auto_msg 里的答案用 <strong> 包起来)
const captchaHintHtml = computed(() => {
  const t = captchaHint.value || ''
  if (!t) return ''
  // 简单转义 < & >,再把识别出的数字答案用蓝色加粗样式标出来
  const escaped = t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped.replace(/'(\d+)'/g, ' <b style="color:#409eff;font-weight:600">\'$1\'</b> ')
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

// 打开编辑框时, row 里已经拿到"是否配置过凭证"的状态
const credentialsState = reactive({
  username_configured: false,
  password_configured: false,
  web_username_configured: false,
  web_password_configured: false,
})

function openEdit(row) {
  editForm.data_type = row.data_type
  editForm.name = row.name
  editForm.base_url = row.base_url
  editForm.page_path = row.page_path
  editForm.time_labels_text = (row.time_labels || []).join(',')
  editForm.default_sync = row.default_sync
  editForm.enabled = row.enabled
  editForm.fetch_mode = isScrmSource(row) ? (row.fetch_mode || 'openapi') : 'playwright'

  // ---- 凭证回填: 前端拿到的是加密密文(security 考虑), 所以不直接填到表单里 ----
  //   避免: 加密串直接被用户看到或误编辑再次提交 → 重复加密成垃圾值。
  //   正确语义: 留空 = 不修改原值, 有值输入 = 用新明文覆盖加密保存。
  //   但 UI 必须明确告诉用户"已保存过,只是为了安全没回显明文", 否则用户会以为"保存了重新打开又没了"。
  credentialsState.username_configured = !!(row.username || row.has_credentials)
  credentialsState.password_configured = !!(row.password || row.has_credentials)
  // web_username / web_password: 从 row.options(解密后) 拿(后端会把 options 里的 web_* 解密返回给前端编辑态用, 或只返回 boolean 标志位)
  const opts = (typeof row.options === 'object' && row.options) ? row.options : {}
  credentialsState.web_username_configured = !!(opts.web_username_configured || opts.web_username)
  credentialsState.web_password_configured = !!(opts.web_password_configured || opts.web_password)
  // 表单输入框统一清空(留空=不修改)
  editForm.username = ''
  editForm.password = ''
  editForm.web_username = ''
  editForm.web_password = ''
  dialogVisible.value = true
}

// 动态 placeholder, 已配置/未配置时提示不同
const _credPlaceholder = (label, configured, emptyHint) => {
  if (configured) return `${label}(已配置,留空=不修改,输入新值覆盖)`
  return emptyHint || `请输入${label},留空表示不修改`
}
const scrmUsernamePlaceholder = computed(() => {
  if (!isScrmSource(editForm)) return _credPlaceholder('BMS 登录账号', credentialsState.username_configured, '留空表示不修改原值')
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') return _credPlaceholder('SCRM OpenAPI Client ID', credentialsState.username_configured, 'SCRM OpenAPI Client ID,留空表示不修改')
  if (mode === 'playwright') return _credPlaceholder('SCRM OpenAPI Client ID(可选,留空不修改)', credentialsState.username_configured, '仅 OpenAPI/双模式需要; Playwright 模式请在下方填网页登录凭证,留空=不改')
  return _credPlaceholder('Client ID(OpenAPI模式用)', credentialsState.username_configured, 'OpenAPI 模式使用 ClientID; 网页模式请在下方填网页登录凭证,留空=不改')
})
const scrmPasswordPlaceholder = computed(() => {
  if (!isScrmSource(editForm)) return _credPlaceholder('BMS 登录密码', credentialsState.password_configured, '留空表示不修改原值')
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') return _credPlaceholder('SCRM OpenAPI Client Secret', credentialsState.password_configured, 'SCRM OpenAPI Client Secret,留空表示不修改')
  if (mode === 'playwright') return _credPlaceholder('SCRM OpenAPI Client Secret(可选,留空不修改)', credentialsState.password_configured, '仅 OpenAPI/双模式需要; Playwright 模式请在下方填网页登录凭证,留空=不改')
  return _credPlaceholder('Client Secret(OpenAPI模式用)', credentialsState.password_configured, 'OpenAPI 模式使用 ClientSecret; 网页模式请在下方填网页登录凭证,留空=不改')
})
// SCRM 网页凭证的 placeholder(动态)
const webUsernamePlaceholder = computed(() =>
  _credPlaceholder('SCRM 网页登录账号(手机号/邮箱)', credentialsState.web_username_configured, 'SCRM 后台管理页面实际登录用的账号,不要填 ClientID;留空=不改'))
const webPasswordPlaceholder = computed(() =>
  _credPlaceholder('SCRM 网页登录密码', credentialsState.web_password_configured, 'SCRM 后台管理页面实际登录用的密码;留空=不改'))

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
    // SCRM 才提交 fetch_mode
    if (isScrmSource(editForm) && editForm.fetch_mode) {
      payload.fetch_mode = editForm.fetch_mode
    }
    // 主账号密码: 仅当非空时传(后端:空表示不改)
    if (editForm.username) payload.username = editForm.username
    if (editForm.password) payload.password = editForm.password
    // SCRM Dual 模式下: web_username / web_password → 合并进 options patch
    //   (后端会自动把 options.patch 的 key 合并进 DB options 列,加密后保存)
    //   Playwright 模式: 主字段 username/password 直接就是网页登录凭证,不需要 web_*
    if (isScrmSource(editForm) && editForm.fetch_mode === 'dual') {
      const optsPatch = {}
      if (editForm.web_username) optsPatch.web_username = editForm.web_username
      if (editForm.web_password) optsPatch.web_password = editForm.web_password
      if (Object.keys(optsPatch).length > 0) payload.options = optsPatch
    }

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

    // 后端 OCR 自动登录成功 → 直接提示并关对话
    if (d?.auto_login_success) {
      ElMessage.success(d?.auto_login_message || d?.message || '后端已自动识别并登录成功')
      captchaDialogVisible.value = false
      if (captchaRow.value) await loadLoginState(captchaRow.value.data_type)
      return
    }
    // 如果是数学验证码且 OCR 识别出了答案 → 自动填充到输入框,用户只需点提交
    const captchaType = d?.captcha_type || (isScrmSource(captchaRow.value) ? 'math' : 'text')
    const recognized = d?.ocr_recognized_text
    if (captchaType === 'math' && recognized && !captchaForm.captcha) {
      captchaForm.captcha = recognized
      // 追加一个本地提示
      const extra = `识别出的答案已自动填入: '${recognized}' —— 请核对图片是否一致再提交。`
      captchaHint.value = captchaHint.value
        ? captchaHint.value + ' | ' + extra
        : extra
    }
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
