<template>
  <div class="shared-doc-sources">
    <!-- 说明 -->
    <el-alert type="info" :closable="false" show-icon class="mb-12">
      共享文档在线采集：系统用登录态打开共享链接 → 官方 UI 导出 xlsx → 解析后复用既有业务管线入库
      (315/卡商投诉 → 投诉台账；商户号退款 → manual_refunds)。<b>增量采集不删除历史数据</b>；
      登录态失效时，点各行的<b>「登录」</b>按钮即可在本页扫码完成授权（无需上服务器跑脚本）。
    </el-alert>

    <!-- 操作栏 -->
    <div class="toolbar mb-12">
      <el-button type="primary" :icon="Plus" @click="openCreate">新增采集源</el-button>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <!-- 列表 -->
    <el-table v-loading="loading" :data="sources" border stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column label="平台" width="90">
        <template #default="{ row }">
          <el-tag :type="row.platform === 'tencent' ? 'primary' : 'warning'" size="small">
            {{ platformName(row.platform) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="doc_name" label="文档名" min-width="160" show-overflow-tooltip />
      <el-table-column prop="doc_url" label="共享链接" min-width="220" show-overflow-tooltip />
      <el-table-column label="目标" width="120">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ targetName(row.target_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            @change="(v) => onToggle(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column label="最近同步" min-width="200">
        <template #default="{ row }">
          <div v-if="row.last_sync_at">
            <el-tag :type="statusType(row.last_sync_status)" size="small">
              {{ statusName(row.last_sync_status) }}
            </el-tag>
            <div class="hint-text">{{ fmtTime(row.last_sync_at) }}</div>
            <div v-if="row.last_sync_summary" class="hint-text">{{ fmtSummary(row) }}</div>
          </div>
          <span v-else class="hint-text">未同步</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :icon="VideoPlay" :loading="syncingId === row.id"
            @click="onSync(row)">同步</el-button>
          <!-- #45: 页面内扫码登录 —— 替代「SSH 上服务器跑脚本」 -->
          <el-button size="small" :icon="Key" @click="startLogin(row)">登录</el-button>
          <el-button size="small" :icon="Edit" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" :icon="Delete" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- ===== 页面内扫码登录弹窗 (#45) ===== -->
    <el-dialog
      v-model="loginVisible"
      :title="`${loginRow?.doc_name || loginRow?.doc_url || ''} — 扫码登录`"
      width="620px"
      :close-on-click-modal="false"
      @closed="onLoginDialogClosed"
    >
      <el-alert type="info" :closable="false" show-icon class="mb-12">
        <template #title>
          请在弹出的浏览器窗口中完成扫码登录；登录成功后点「我已扫码完成」保存登录态。
          同一平台同时只能有一个登录会话。
        </template>
      </el-alert>

      <div class="login-shot">
        <img v-if="loginScreenshot" :src="loginScreenshot" alt="登录页截图" class="shot-img" />
        <div v-else class="shot-empty">正在启动浏览器并加载登录页…</div>
      </div>

      <div class="login-status">
        <el-tag :type="loginStatusType(loginStatus)" size="small">{{ loginStatusText(loginStatus) }}</el-tag>
        <span v-if="loginCurrentUrl" class="hint-text">{{ loginCurrentUrl }}</span>
        <span v-if="loginExpiresIn" class="hint-text">剩余 {{ loginExpiresIn }} 秒</span>
      </div>

      <template #footer>
        <el-button @click="cancelLogin">取消</el-button>
        <el-button type="primary" :loading="confirming" @click="confirmLogin">我已扫码完成</el-button>
      </template>
    </el-dialog>

    <!-- 新建/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑采集源' : '新增采集源'" width="640px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="平台" prop="platform">
          <el-select v-model="form.platform" placeholder="选择平台" :disabled="!!editingId">
            <el-option label="腾讯文档" value="tencent" />
            <el-option label="金山文档" value="kdocs" />
          </el-select>
        </el-form-item>
        <el-form-item label="共享链接" prop="doc_url">
          <el-input v-model="form.doc_url" placeholder="https://docs.qq.com/sheet/... 或 https://www.kdocs.cn/l/..." />
        </el-form-item>
        <el-form-item label="文档名">
          <el-input v-model="form.doc_name" placeholder="显示名(选填)" />
        </el-form-item>
        <el-form-item label="目标业务" prop="target_type">
          <el-select v-model="form.target_type" placeholder="选择数据落点" :disabled="!!editingId">
            <el-option label="投诉(315 + 卡商 2 Sheet)" value="complaint" />
            <el-option label="商户号退款(登记明细)" value="manual_refund" />
          </el-select>
        </el-form-item>
        <el-form-item label="同步间隔">
          <el-input-number v-model="form.sync_interval_minutes" :min="5" :step="5" />
          <span class="hint-text"> 分钟</span>
        </el-form-item>
        <el-form-item v-if="form.target_type === 'complaint'" label="Sheet 规则">
          <div class="hint-text">
            315 文档通常含两个 Sheet：监督局-315投诉 → external_complaint、卡商群投诉 → card_complaint。
            留空则自动按表头匹配。
          </div>
          <div v-for="(r, i) in form.sheet_rules" :key="i" class="sheet-rule-row">
            <el-input v-model="r.sheet" placeholder="Sheet 名(如 监督局-315投诉)" style="width: 200px" />
            <el-select v-model="r.target" style="width: 180px; margin-left: 8px">
              <el-option label="→ external_complaint(315)" value="external_complaint" />
              <el-option label="→ card_complaint(卡商)" value="card_complaint" />
            </el-select>
            <el-button size="small" type="danger" :icon="Delete" @click="removeSheetRule(i)" />
          </div>
          <el-button size="small" :icon="Plus" @click="addSheetRule">加一行 Sheet 映射</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Edit, Delete, VideoPlay, Key } from '@element-plus/icons-vue'
import {
  listSharedDocSources, createSharedDocSource, updateSharedDocSource,
  deleteSharedDocSource, toggleSharedDocSource, syncSharedDocNow,
  startSharedDocLogin, getSharedDocLoginStatus, confirmSharedDocLogin, cancelSharedDocLogin,
} from '@/api/sharedDoc'

const sources = ref([])
const loading = ref(false)
const syncingId = ref(null)
const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const formRef = ref(null)

const emptyForm = () => ({
  platform: 'tencent',
  doc_url: '',
  doc_name: '',
  target_type: 'complaint',
  sync_interval_minutes: 60,
  sheet_rules: [
    { sheet: '监督局-315投诉', target: 'external_complaint' },
    { sheet: '卡商群投诉', target: 'card_complaint' },
  ],
})
const form = reactive(emptyForm())
const rules = {
  platform: [{ required: true, message: '请选择平台', trigger: 'change' }],
  doc_url: [{ required: true, message: '请填写共享链接', trigger: 'blur' }],
  target_type: [{ required: true, message: '请选择目标业务', trigger: 'change' }],
}

const platformName = (p) => ({ tencent: '腾讯', kdocs: '金山' }[p] || p)
const targetName = (t) => ({ complaint: '投诉(315/卡商)', manual_refund: '商户号退款' }[t] || t)
const statusName = (s) => ({ success: '成功', partial: '部分', failed: '失败' }[s] || s || '-')
const statusType = (s) => ({ success: 'success', partial: 'warning', failed: 'danger' }[s] || 'info')

function fmtTime(v) {
  if (!v) return ''
  const d = new Date(v)
  return d.toLocaleString('zh-CN', { hour12: false })
}

function fmtSummary(row) {
  const s = row.last_sync_summary
  if (!s || typeof s !== 'object') return ''
  // complaint: {external:{...}, card:{...}} ; manual_refund: {total, inserted...}
  if (s.external) {
    const e = s.external
    const c = s.card
    return `315 ${e.total}行(+${e.inserted}/~${e.updated}) 卡商 ${c.total}行(+${c.inserted}/~${c.updated})`
  }
  if (typeof s.total === 'number') {
    return `${s.total}行(+${s.inserted}/~${s.updated}/!${s.failed})`
  }
  return JSON.stringify(s).slice(0, 60)
}

async function load() {
  loading.value = true
  try {
    const res = await listSharedDocSources()
    sources.value = res.data || []
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    platform: row.platform,
    doc_url: row.doc_url,
    doc_name: row.doc_name || '',
    target_type: row.target_type,
    sync_interval_minutes: row.sync_interval_minutes || 60,
    sheet_rules: (row.sheet_rules && row.sheet_rules.length) ? row.sheet_rules.map((x) => ({ ...x }))
      : [{ sheet: '', target: 'external_complaint' }],
  })
  dialogVisible.value = true
}

function addSheetRule() {
  form.sheet_rules.push({ sheet: '', target: 'external_complaint' })
}
function removeSheetRule(i) {
  form.sheet_rules.splice(i, 1)
}

async function onSave() {
  await formRef.value.validate()
  saving.value = true
  try {
    const payload = {
      platform: form.platform,
      doc_url: form.doc_url,
      doc_name: form.doc_name,
      target_type: form.target_type,
      sync_interval_minutes: form.sync_interval_minutes,
    }
    if (form.target_type === 'complaint') {
      payload.sheet_rules = form.sheet_rules.filter((r) => r.sheet && r.target)
    }
    if (editingId.value) {
      await updateSharedDocSource(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      await createSharedDocSource(payload)
      ElMessage.success('已新增')
    }
    dialogVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function onToggle(row, v) {
  try {
    await toggleSharedDocSource(row.id, v)
    row.enabled = v
    ElMessage.success(v ? '已启用' : '已停用')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function onSync(row) {
  syncingId.value = row.id
  try {
    const res = await syncSharedDocNow(row.id)
    const d = res.data || {}
    ElMessage.success(`同步完成 status=${d.status || '-'}`)
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '同步失败')
  } finally {
    syncingId.value = null
  }
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除采集源「${row.doc_name || row.doc_url}」？历史同步数据保留。`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteSharedDocSource(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

// ── 页面内扫码登录 (#45) ──
// 替代「SSH 上服务器跑 scripts/tencent_doc_login.py」的唯一路径。
const loginVisible = ref(false)
const loginRow = ref(null)
const loginSessionId = ref('')
const loginScreenshot = ref('')
const loginStatus = ref('pending')
const loginCurrentUrl = ref('')
const loginExpiresIn = ref(0)
const confirming = ref(false)
let _loginPoll = null

const loginStatusText = (v) => ({
  pending: '等待扫码', scanned: '已扫码，待确认', authorized: '已检测到登录',
  expired: '会话已过期', closed: '会话已关闭',
}[v] || v)
const loginStatusType = (v) => ({
  pending: 'info', scanned: 'warning', authorized: 'success',
  expired: 'danger', closed: 'info',
}[v] || 'info')

async function startLogin(row) {
  loginRow.value = row
  loginVisible.value = true
  loginScreenshot.value = ''
  loginStatus.value = 'pending'
  loginCurrentUrl.value = ''
  try {
    const res = await startSharedDocLogin(row.id)
    const d = res.data || {}
    loginSessionId.value = d.session_id
    loginScreenshot.value = d.screenshot || ''
    loginCurrentUrl.value = d.current_url || ''
    loginExpiresIn.value = d.expires_in || 0
    startLoginPolling()
  } catch (e) {
    // 501 = 服务器未装 Xvfb, detail 里带可执行的一键命令, 直接展示给用户
    ElMessage.error(e?.response?.data?.detail || '启动登录失败')
    loginVisible.value = false
  }
}

function startLoginPolling() {
  stopLoginPolling()
  _loginPoll = setInterval(async () => {
    if (!loginSessionId.value) return
    try {
      const res = await getSharedDocLoginStatus(loginSessionId.value)
      const d = res.data || {}
      loginStatus.value = d.status
      loginCurrentUrl.value = d.current_url || ''
      loginExpiresIn.value = d.expires_in || 0
      if (d.status === 'expired' || d.status === 'closed') stopLoginPolling()
    } catch (e) {
      stopLoginPolling()
    }
  }, 2000)
}

function stopLoginPolling() {
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
      await load()
    } else {
      // 后端轻量校验未通过 —— 明确告知, 且旧登录态未被覆盖
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
  stopLoginPolling()
  if (loginSessionId.value) {
    // 关闭弹窗即释放会话(避免 chromium 进程泄漏)
    cancelSharedDocLogin(loginSessionId.value).catch(() => {})
    loginSessionId.value = ''
  }
}

onBeforeUnmount(stopLoginPolling)

onMounted(load)
</script>

<style scoped>
.mb-12 { margin-bottom: 12px; }
.toolbar { display: flex; gap: 8px; }
.hint-text { color: #909399; font-size: 12px; margin-left: 4px; }
.sheet-rule-row { display: flex; align-items: center; margin-bottom: 6px; }
/* #45 扫码登录弹窗 */
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
  gap: 6px;
}
</style>
