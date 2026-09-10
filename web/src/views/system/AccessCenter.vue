<template>
  <div class="access-center">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">接入中心</div>
        <div class="page-sub">对外接入与 AI 配置的统一入口 · 登录态 / 外部 Token / 推送配置包 / AI 与模型</div>
      </div>
      <el-button size="small" :icon="Refresh" @click="refreshAll">刷新</el-button>
    </div>

    <el-tabs v-model="tab" class="access-tabs">
      <!-- ═══ Tab1 登录态 ═══ -->
      <el-tab-pane label="① 登录态" name="states">
        <el-alert
          v-if="!states.display_available"
          type="warning"
          :closable="false"
          show-icon
          class="mb-16"
          title="服务器未安装虚拟显示服务(Xvfb)：无法在页面内启动扫码登录，请用命令行登录（下方各源「命令行」按钮可复制命令）。"
        />
        <el-card shadow="never">
          <template #header><span>采集源登录态健康度</span></template>
          <el-table :data="states.items" size="small" border>
            <el-table-column label="平台" width="110">
              <template #default="{ row }">
                <el-tag :type="row.platform === 'tencent' ? 'primary' : 'warning'" size="small">
                  {{ platformName(row.platform) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="登录态文件" prop="state_file" min-width="150" />
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <el-tag v-if="!row.exists" type="danger" size="small" effect="plain">未登录</el-tag>
                <el-tag v-else-if="row.stale" type="warning" size="small" effect="plain">建议重登</el-tag>
                <el-tag v-else type="success" size="small" effect="plain">正常</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最后更新" width="150">
              <template #default="{ row }">{{ row.mtime ? fmtTime(row.mtime) : '—' }}</template>
            </el-table-column>
            <el-table-column label="已过天数" width="100">
              <template #default="{ row }">{{ row.age_days ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="提示" min-width="180">
              <template #default="{ row }">
                <span :class="row.stale ? 'warn-text' : 'dim'">{{ row.advice || '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="mt-16">
          <template #header>
            <div class="card-header">
              <span>采集源（可对某源发起扫码登录）</span>
              <el-button link type="primary" size="small" @click="loadSources">刷新</el-button>
            </div>
          </template>
          <el-table :data="sources" size="small" border empty-text="暂无采集源（可在 数据采集 → 共享文档采集 中新增）">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column label="平台" width="90">
              <template #default="{ row }">
                <el-tag :type="row.platform === 'tencent' ? 'primary' : 'warning'" size="small">
                  {{ platformName(row.platform) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="doc_name" label="文档名" min-width="150" show-overflow-tooltip />
            <el-table-column prop="doc_url" label="共享链接" min-width="200" show-overflow-tooltip />
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  size="small"
                  :disabled="!states.display_available"
                  @click="startLogin(row)"
                >
                  扫码登录
                </el-button>
                <el-button link type="info" size="small" @click="copyCliCommand(row)">命令行</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ═══ Tab2 外部 Token ═══ -->
      <el-tab-pane label="② 外部 Token" name="tokens">
        <ExternalTokenPanel />
      </el-tab-pane>

      <!-- ═══ Tab3 推送配置包 ═══ -->
      <el-tab-pane label="③ 推送配置包" name="packages">
        <el-alert type="info" :closable="false" show-icon class="mb-16">
          <template #title>
            选好场景、填上共享文档地址，点「生成并复制」即可得到一段完整 Markdown ——
            含接口地址、鉴权方式、字段规范与提示词要求，<b>一次粘贴给 WorkBuddy 即可</b>。
          </template>
        </el-alert>

        <el-row :gutter="16">
          <el-col :span="9">
            <el-card shadow="never">
              <template #header><span>生成参数</span></template>
              <el-form label-width="100px" label-position="right">
                <el-form-item label="接入场景" required>
                  <el-select v-model="pkg.profileId" placeholder="选择场景" style="width: 100%" @change="onProfileChange">
                    <el-option v-for="p in profiles" :key="p.id" :label="p.name" :value="p.id" />
                  </el-select>
                </el-form-item>
                <el-form-item
                  v-for="v in currentVariables"
                  :key="v.key"
                  :label="v.label"
                  :required="v.required"
                >
                  <el-input
                    v-model="pkg.values[v.key]"
                    :placeholder="placeholderFor(v.key)"
                    :maxlength="v.key === 'doc_url' ? 512 : 128"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" :loading="pkgLoading" :icon="Document" @click="renderAndCopy">
                    生成并复制
                  </el-button>
                  <el-button :disabled="!pkgMarkdown" @click="pkgPreviewVisible = true">预览</el-button>
                </el-form-item>
              </el-form>
            </el-card>

            <el-card shadow="never" class="mt-16">
              <template #header>
                <div class="card-header">
                  <span>模板维护（管理员）</span>
                </div>
              </template>
              <div class="dim mb-8">模板可编辑，改错了可一键恢复默认。</div>
              <el-button size="small" :icon="Edit" @click="openEdit">编辑当前模板</el-button>
              <el-button size="small" :icon="RefreshLeft" :disabled="!pkg.profileId" @click="resetCurrent">
                恢复默认
              </el-button>
            </el-card>
          </el-col>

          <el-col :span="15">
            <el-card shadow="never">
              <template #header>
                <div class="card-header">
                  <span>配置包预览</span>
                  <el-button
                    v-if="pkgMarkdown"
                    type="primary"
                    link
                    size="small"
                    :icon="DocumentCopy"
                    @click="copyWithToast(pkgMarkdown, '配置包')"
                  >
                    复制
                  </el-button>
                </div>
              </template>
              <el-input
                v-if="pkgMarkdown"
                v-model="pkgMarkdown"
                type="textarea"
                :rows="28"
                readonly
                class="pkg-textarea"
              />
              <el-empty v-else description="填写左侧参数后点「生成并复制」" />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ═══ Tab4 AI 与模型 ═══ -->
      <el-tab-pane label="④ AI 与模型" name="ai">
        <el-alert type="info" :closable="false" show-icon class="mb-16">
          <template #title>
            AI 相关设置集中在此。其中<b>模型接入</b>由服务端 <code>.env</code> 提供，
            <b>铁律</b>与<b>口径卡</b>是防幻觉底线（只读），只有<b>关注点</b>可配置。
          </template>
        </el-alert>

        <!-- 模型接入(脱敏只读) -->
        <el-card shadow="never" class="mb-16">
          <template #header>
            <div class="card-header">
              <span>模型接入</span>
              <el-tag size="small" effect="plain" type="info">只读（.env 提供）</el-tag>
              <el-tag v-if="aiCfg.enabled" type="success" size="small">已启用</el-tag>
              <el-tag v-else type="danger" size="small">未启用</el-tag>
            </div>
          </template>
          <el-descriptions :column="2" size="small" border>
            <el-descriptions-item label="服务地址">{{ aiCfg.base_url || '—' }}</el-descriptions-item>
            <el-descriptions-item label="模型名">{{ aiCfg.model || '—' }}</el-descriptions-item>
            <el-descriptions-item label="思考模式">{{ aiCfg.thinking || '—' }}</el-descriptions-item>
            <el-descriptions-item label="最大输出 token">{{ aiCfg.max_tokens ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="API Key">
              <el-tag v-if="aiCfg.api_key_configured" type="success" size="small" effect="plain">
                已配置（长度 {{ aiCfg.api_key_length }}）
              </el-tag>
              <el-tag v-else type="danger" size="small" effect="plain">未配置</el-tag>
              <span class="dim ml-8">出于安全，密钥明文不在任何界面展示</span>
            </el-descriptions-item>
          </el-descriptions>
          <el-alert type="warning" :closable="false" show-icon class="mt-12">
            <template #title>
              修改模型配置需运维在服务器更新 <code>.env</code>（<code>LLM_ENABLED / LLM_BASE_URL /
              LLM_MODEL / LLM_API_KEY</code>）并重启后端。API Key 严禁入库、严禁在界面展示明文。
            </template>
          </el-alert>
        </el-card>

        <!-- 提示词四层 -->
        <el-card shadow="never">
          <template #header><span>提示词分层（四层）</span></template>
          <el-table :data="layers" size="small" border>
            <el-table-column label="层级" min-width="150">
              <template #default="{ row }">
                <b>{{ row.name }}</b>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="110">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" type="info">{{ sourceLabel(row.source) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="可编辑" width="100">
              <template #default="{ row }">
                <el-tag :type="row.editable ? 'success' : 'info'" size="small" effect="plain">
                  {{ row.editable ? '可编辑' : '只读' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="规模" width="110">
              <template #default="{ row }">
                <span v-if="row.char_count">{{ row.char_count }} 字</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="说明" min-width="220" show-overflow-tooltip prop="reason" />
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="row.key !== 'model'"
                  link
                  type="primary"
                  size="small"
                  @click="previewLayer(row)"
                >
                  查看全文
                </el-button>
                <el-button
                  v-if="row.editable && row.manage_path"
                  link
                  type="success"
                  size="small"
                  @click="router.push(row.manage_path)"
                >
                  去配置
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- ═══ 扫码登录弹窗 (#45) ═══ -->
    <el-dialog
      v-model="loginVisible"
      :title="`${loginSource?.doc_name || loginSource?.doc_url || ''} — 扫码登录`"
      width="620px"
      :close-on-click-modal="false"
      @closed="onLoginDialogClosed"
    >
      <el-alert type="info" :closable="false" show-icon class="mb-12">
        <template #title>
          请在下方窗口中完成扫码登录；登录成功后点「我已扫码完成」保存登录态。
          同一平台同时只能有一个登录会话。
        </template>
      </el-alert>

      <div class="login-shot">
        <img v-if="loginScreenshot" :src="loginScreenshot" alt="登录页截图" class="shot-img" />
        <div v-else class="shot-empty">正在启动浏览器并加载登录页…</div>
      </div>

      <div class="login-status">
        <el-tag :type="statusType(loginStatus)" size="small">{{ statusText(loginStatus) }}</el-tag>
        <span class="dim ml-8">{{ loginCurrentUrl }}</span>
        <span v-if="loginExpiresIn" class="dim ml-8">剩余 {{ loginExpiresIn }} 秒</span>
      </div>

      <template #footer>
        <el-button @click="cancelLogin">取消</el-button>
        <el-button type="primary" :loading="confirming" @click="confirmLogin">我已扫码完成</el-button>
      </template>
    </el-dialog>

    <!-- 模板编辑弹窗 -->
    <el-dialog v-model="editVisible" title="编辑接入配置包模板" width="860px" :close-on-click-modal="false">
      <el-form label-width="90px" label-position="right">
        <el-form-item label="显示名">
          <el-input v-model="editForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="模板内容">
          <el-input v-model="editForm.content_md" type="textarea" :rows="22" class="pkg-textarea" />
          <div class="sub-tip">
            可用变量：
            <code v-for="v in varHints" :key="v" class="var-chip">{{ v }}</code>
            ；其它未列入的占位符会原样保留。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTemplate">保存</el-button>
      </template>
    </el-dialog>

    <!-- 提示词全文(只读) -->
    <el-dialog v-model="previewVisible" :title="previewTitle" width="760px">
      <el-alert v-if="previewReadonly" type="info" :closable="false" show-icon class="mb-12">
        <template #title>该层为防幻觉底线（代码常量），仅供查看，不可在此修改。</template>
      </el-alert>
      <el-input v-model="previewContent" type="textarea" :rows="22" readonly class="pkg-textarea" />
    </el-dialog>

    <!-- 配置包大图预览 -->
    <el-dialog v-model="pkgPreviewVisible" title="配置包全文" width="820px">
      <el-input v-model="pkgMarkdown" type="textarea" :rows="26" readonly class="pkg-textarea" />
      <template #footer>
        <el-button type="primary" :icon="DocumentCopy" @click="copyWithToast(pkgMarkdown, '配置包')">
          复制配置包
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh, Document, DocumentCopy, Edit, RefreshLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import ExternalTokenPanel from '@/components/ExternalTokenPanel.vue'
import { copyWithToast, copyText } from '@/utils/clipboard'
import {
  listSharedDocSources,
  getSharedDocStates,
  startSharedDocLogin,
  getSharedDocLoginStatus,
  confirmSharedDocLogin,
  cancelSharedDocLogin,
} from '@/api/sharedDoc'
import {
  listProfiles,
  renderProfile,
  updateProfile,
  resetProfile,
} from '@/api/integrationProfile'
import { getAiModelConfig, getAiPromptLayers, getAiPromptPreview } from '@/api/aiModel'

const router = useRouter()

// ── Tab ──
const tab = ref('states')

// ── Tab1 登录态 ──
const states = reactive({ items: [], stale_threshold_days: 7, display_available: true })
const sources = ref([])

const platformName = (p) => ({ tencent: '腾讯文档', kdocs: '金山文档' }[p] || p)
const fmtTime = (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '')

async function loadStates() {
  try {
    const res = await getSharedDocStates()
    Object.assign(states, res.data || {})
  } catch (e) { /* 拦截器已提示 */ }
}

async function loadSources() {
  try {
    const res = await listSharedDocSources()
    sources.value = res.data || []
  } catch (e) { /* 拦截器已提示 */ }
}

// 登录会话
const loginVisible = ref(false)
const loginSource = ref(null)
const loginSessionId = ref('')
const loginScreenshot = ref('')
const loginStatus = ref('pending')
const loginCurrentUrl = ref('')
const loginExpiresIn = ref(0)
const confirming = ref(false)
let _loginPoll = null

const statusText = (s) => ({
  pending: '等待扫码', scanned: '已扫码，待确认', authorized: '已检测到登录',
  expired: '会话已过期', closed: '会话已关闭',
}[s] || s)
const statusType = (s) => ({
  pending: 'info', scanned: 'warning', authorized: 'success',
  expired: 'danger', closed: 'info',
}[s] || 'info')

async function startLogin(row) {
  loginSource.value = row
  loginVisible.value = true
  loginScreenshot.value = ''
  loginStatus.value = 'pending'
  try {
    const res = await startSharedDocLogin(row.id)
    const d = res.data || {}
    loginSessionId.value = d.session_id
    loginScreenshot.value = d.screenshot || ''
    loginCurrentUrl.value = d.current_url || ''
    loginExpiresIn.value = d.expires_in || 0
    startPolling()
  } catch (e) {
    const detail = e?.response?.data?.detail || '启动登录失败'
    ElMessage.error(detail)
    loginVisible.value = false
  }
}

function startPolling() {
  stopPolling()
  // 2 秒轮询会话状态; 截图不重复拉取(避免带宽浪费), 仅状态与 URL
  _loginPoll = setInterval(async () => {
    if (!loginSessionId.value) return
    try {
      const res = await getSharedDocLoginStatus(loginSessionId.value)
      const d = res.data || {}
      loginStatus.value = d.status
      loginCurrentUrl.value = d.current_url || ''
      loginExpiresIn.value = d.expires_in || 0
      if (d.status === 'expired' || d.status === 'closed') stopPolling()
    } catch (e) {
      stopPolling()
    }
  }, 2000)
}

function stopPolling() {
  if (_loginPoll) { clearInterval(_loginPoll); _loginPoll = null }
}

async function confirmLogin() {
  if (!loginSessionId.value) return
  confirming.value = true
  try {
    const res = await confirmSharedDocLogin(loginSessionId.value)
    const d = res.data || {}
    if (d.ok) {
      ElMessage.success(`登录态已保存（cookies ${d.cookies} 条）`)
      loginVisible.value = false
      await loadStates()
    } else {
      // 后端轻量校验未通过 —— 不覆盖旧登录态, 明确告知用户
      ElMessage.warning(d.message || '未检测到登录态，请完成扫码后重试')
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存登录态失败')
  } finally {
    confirming.value = false
  }
}

async function cancelLogin() {
  if (loginSessionId.value) {
    try { await cancelSharedDocLogin(loginSessionId.value) } catch (e) { /* 幂等 */ }
  }
  loginVisible.value = false
}

function onLoginDialogClosed() {
  stopPolling()
  if (loginSessionId.value) {
    // 关闭弹窗即释放会话(避免 chromium 进程泄漏)
    cancelSharedDocLogin(loginSessionId.value).catch(() => {})
    loginSessionId.value = ''
  }
}

async function copyCliCommand(row) {
  const script = row.platform === 'tencent' ? 'tencent_doc_login.py' : 'kdocs_login.py'
  const cmd = `cd /path/to/backend && ./.venv/bin/python ../scripts/${script} --url "${row.doc_url}"`
  await copyWithToast(cmd, '命令行登录命令')
}

// ── Tab3 配置包 ──
const profiles = ref([])
const pkg = reactive({ profileId: null, values: {} })
const pkgMarkdown = ref('')
const pkgLoading = ref(false)
const pkgPreviewVisible = ref(false)

// 变量提示文案在脚本里构造(避免 Vue 模板中 {{ }} 嵌套造成的解析错误)
const varHints = ['base_url', 'doc_url', 'doc_name', 'token', 'target_type', 'sync_interval']
  .map((k) => `{{${k}}}`)

const currentProfile = computed(() => profiles.value.find((p) => p.id === pkg.profileId) || null)
const currentVariables = computed(() => currentProfile.value?.variables || [])

const PLACEHOLDERS = {
  doc_url: 'https://docs.qq.com/sheet/...',
  doc_name: '如：315投诉登记表',
  sync_interval: '如：20',
  target_type: 'complaint 或 manual_refund',
}

function placeholderFor(key) {
  if (PLACEHOLDERS[key]) return PLACEHOLDERS[key]
  if (key === 'base_url') return window.location.origin
  if (key === 'token') return '（留空即可，系统不会回填明文）'
  return ''
}

function onProfileChange() {
  pkg.values = {}
  pkgMarkdown.value = ''
  // 预填默认值: 系统地址 + 同步间隔(减少手工输入)
  const vars = currentVariables.value || []
  if (vars.some((v) => v.key === 'base_url')) pkg.values.base_url = window.location.origin
  if (vars.some((v) => v.key === 'sync_interval')) pkg.values.sync_interval = 20
}

async function renderAndCopy() {
  if (!pkg.profileId) { ElMessage.warning('请先选择接入场景'); return }
  const missing = (currentVariables.value || [])
    .filter((v) => v.required && !pkg.values[v.key])
    .map((v) => v.label)
  if (missing.length) {
    ElMessage.warning(`请填写：${missing.join('、')}`)
    return
  }
  pkgLoading.value = true
  try {
    const payload = { base_url: window.location.origin, ...pkg.values }
    // 数值字段转 int(后端 schema 为 int, 传字符串会 422)
    if (payload.sync_interval) payload.sync_interval = Number(payload.sync_interval)
    const res = await renderProfile(pkg.profileId, payload)
    pkgMarkdown.value = res.data?.markdown || ''
    if (res.data?.unresolved?.length) {
      ElMessage.warning(`以下变量未填值：${res.data.unresolved.join('、')}`)
    }
    await copyWithToast(pkgMarkdown.value, '配置包')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '生成配置包失败')
  } finally {
    pkgLoading.value = false
  }
}

async function loadProfiles() {
  try {
    const res = await listProfiles()
    profiles.value = (res.data || []).filter((p) => p.enabled !== false)
    if (profiles.value.length && !pkg.profileId) {
      pkg.profileId = profiles.value[0].id
      onProfileChange()
    }
  } catch (e) { /* 拦截器已提示 */ }
}

// 模板编辑
const editVisible = ref(false)
const saving = ref(false)
const editForm = reactive({ id: null, name: '', content_md: '' })

function openEdit() {
  if (!currentProfile.value) { ElMessage.warning('请先选择场景'); return }
  editForm.id = currentProfile.value.id
  editForm.name = currentProfile.value.name
  editForm.content_md = currentProfile.value.content_md
  editVisible.value = true
}

async function saveTemplate() {
  saving.value = true
  try {
    await updateProfile(editForm.id, { name: editForm.name, content_md: editForm.content_md })
    ElMessage.success('已保存')
    editVisible.value = false
    await loadProfiles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function resetCurrent() {
  if (!pkg.profileId) return
  try {
    await resetProfile(pkg.profileId)
    ElMessage.success('已恢复默认模板')
    await loadProfiles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '恢复失败')
  }
}

// ── Tab4 AI ──
const aiCfg = reactive({})
const layers = ref([])

const sourceLabel = (s) => ({ env: '.env', code: '代码常量', db: '数据库' }[s] || s)

const previewVisible = ref(false)
const previewTitle = ref('')
const previewContent = ref('')
const previewReadonly = ref(true)

async function loadAi() {
  try {
    const [cfgRes, layerRes] = await Promise.all([getAiModelConfig(), getAiPromptLayers()])
    Object.assign(aiCfg, cfgRes.data || {})
    layers.value = layerRes.data?.layers || []
  } catch (e) { /* 拦截器已提示 */ }
}

async function previewLayer(row) {
  try {
    const res = await getAiPromptPreview(row.key)
    previewTitle.value = res.data?.name || row.name
    previewContent.value = res.data?.content || '（暂无内容）'
    previewReadonly.value = res.data?.readonly !== false
    previewVisible.value = true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  }
}

// ── 全局 ──
async function refreshAll() {
  await Promise.all([loadStates(), loadSources()])
  ElMessage.success('已刷新')
}

onMounted(() => {
  loadStates()
  loadSources()
  loadProfiles()
  loadAi()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style scoped>
.access-center {
  max-width: 1280px;
}
.flex-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-title { font-size: 18px; font-weight: 600; }
.page-sub { font-size: 12px; color: #909399; margin-top: 2px; }
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
.mb-8 { margin-bottom: 8px; }
.mb-16 { margin-bottom: 16px; }
.mt-12 { margin-top: 12px; }
.mt-16 { margin-top: 16px; }
.ml-8 { margin-left: 8px; }
.dim { color: var(--el-text-color-secondary); font-size: 12px; }
.warn-text { color: var(--el-color-warning); font-size: 12px; }
.sub-tip {
  color: #909399;
  font-size: 12px;
  line-height: 1.8;
  margin-top: 4px;
}
.var-chip {
  margin-right: 6px;
  background: #f5f7fa;
  padding: 1px 5px;
  border-radius: 3px;
}
.login-shot {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #f5f7fa;
  min-height: 300px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.shot-img { width: 100%; display: block; }
.shot-empty { color: #909399; font-size: 13px; }
.login-status {
  margin-top: 10px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.pkg-textarea :deep(textarea) {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}
</style>
