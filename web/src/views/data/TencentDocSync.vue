<template>
  <div class="doc-sync-page">
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      将共享的腾讯文档表格(315投诉 / 卡商投诉)接入系统：填链接 → 点击授权 → 系统按间隔自动监测文档变化并同步入库。
      需要先在<el-link type="primary" href="https://docs.qq.com/open/developers/" target="_blank">腾讯文档开放合作平台</el-link>注册开发者应用，获取 ClientID / ClientSecret，回调域名配置为本系统地址。
    </el-alert>

    <!-- ===== 步骤一：应用凭证 ===== -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="card-header">
          <span>① 开放平台应用凭证</span>
          <el-tag v-if="status.app_configured" type="success" size="small" effect="plain">已配置</el-tag>
          <el-tag v-else type="danger" size="small" effect="plain">未配置</el-tag>
        </div>
      </template>
      <el-form :model="credForm" label-width="110px" class="compact-form" @submit.prevent>
        <el-form-item label="ClientID">
          <el-input v-model="credForm.app_id" placeholder="腾讯文档开放平台应用 ClientID" />
        </el-form-item>
        <el-form-item label="ClientSecret">
          <el-input v-model="credForm.app_secret" type="password" show-password :placeholder="status.app_configured ? '已配置, 留空保持不变' : '应用 ClientSecret(仅保存不展示)'" />
        </el-form-item>
        <el-form-item label="回调地址">
          <el-input v-model="credForm.redirect_uri" placeholder="留空自动按当前访问地址生成" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="savingCred" @click="saveCredentials">保存凭证</el-button>
          <span v-if="status.app_configured" class="hint-text ml-8">已配置；修改 ClientID 或重填 ClientSecret 后点保存即可更新</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- ===== 步骤二：授权 ===== -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="card-header">
          <span>② 账号授权(点击授权后持续监测)</span>
          <el-tag v-if="status.authorized" :type="status.token_expired ? 'warning' : 'success'" size="small" effect="plain">
            {{ status.token_expired ? '令牌已过期' : '已授权' }}
          </el-tag>
          <el-tag v-else type="danger" size="small" effect="plain">未授权</el-tag>
        </div>
      </template>
      <div class="auth-area">
        <template v-if="status.authorized">
          <el-descriptions :column="3" size="small" border class="mb-12">
            <el-descriptions-item label="授权账号">{{ status.open_id || '-' }}</el-descriptions-item>
            <el-descriptions-item label="令牌状态">
              <el-tag v-if="status.token_expired" type="warning" size="small">已过期,将自动刷新</el-tag>
              <el-tag v-else type="success" size="small">有效</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="文档配置">
              <el-tag v-if="status.doc_configured" type="success" size="small">已配置</el-tag>
              <el-tag v-else type="info" size="small">待配置</el-tag>
            </el-descriptions-item>
          </el-descriptions>
          <el-button type="primary" plain :disabled="!status.app_configured" @click="authorize">
            重新授权
          </el-button>
          <el-button type="danger" plain @click="revoke">解除授权</el-button>
        </template>
        <template v-else>
          <p class="hint-text mb-12">
            {{ status.app_configured ? '点击下方按钮跳转腾讯文档授权页, 扫码/登录同意后自动回跳完成授权。' : '请先在上方保存应用凭证。' }}
          </p>
          <el-button type="primary" :loading="authorizing" :disabled="!status.app_configured" @click="authorize">
            前往授权
          </el-button>
        </template>
      </div>
    </el-card>

    <!-- ===== 步骤三：共享文档配置 ===== -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="card-header">
          <span>③ 共享文档链接(监测对象)</span>
          <el-tag v-if="status.doc_configured" type="success" size="small" effect="plain">已配置</el-tag>
          <el-tag v-else type="info" size="small" effect="plain">未配置</el-tag>
        </div>
      </template>
      <el-form :model="docForm" label-width="110px" class="compact-form" @submit.prevent>
        <el-form-item label="共享链接">
          <el-input v-model="docForm.doc_url" placeholder="https://docs.qq.com/sheet/xxxxx 或分享短链 https://docs.qq.com/s/xxxxx" />
        </el-form-item>
        <el-form-item label="315投诉 Sheet">
          <el-input v-model="docForm.external_sheet_name" placeholder="Sheet 名(如 监督局-315投诉), 留空自动取第一张表" />
        </el-form-item>
        <el-form-item label="卡商投诉 Sheet">
          <el-input v-model="docForm.card_sheet_name" placeholder="Sheet 名(如 卡商群投诉), 留空自动取第一张表" />
        </el-form-item>
        <el-form-item label="轮询间隔">
          <el-input-number v-model="docForm.sync_interval_minutes" :min="5" :max="1440" :step="5" /> 分钟
        </el-form-item>
        <el-form-item label="自动同步">
          <el-switch v-model="docForm.enabled" active-text="开启" inactive-text="关闭" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="savingDoc" @click="saveDocConfig">保存配置</el-button>
          <el-button type="success" :loading="syncing" :disabled="!canSync" @click="syncNow">立即同步一次</el-button>
          <span v-if="!canSync" class="hint-text ml-8">需先完成凭证/授权/文档配置</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- ===== 最近同步状态 ===== -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="card-header">
          <span>最近同步</span>
          <el-tag v-if="status.last_sync_status === 'success'" type="success" size="small">成功</el-tag>
          <el-tag v-else-if="status.last_sync_status === 'partial'" type="warning" size="small">部分失败</el-tag>
          <el-tag v-else-if="status.last_sync_status === 'failed'" type="danger" size="small">失败</el-tag>
          <el-tag v-else type="info" size="small">未同步</el-tag>
        </div>
      </template>
      <el-descriptions :column="4" size="small" border>
        <el-descriptions-item label="最近同步时间">{{ fmtTime(status.last_sync_at) || '-' }}</el-descriptions-item>
        <el-descriptions-item label="315投诉">{{ syncText(status.last_sync_summary, 'external') }}</el-descriptions-item>
        <el-descriptions-item label="卡商投诉">{{ syncText(status.last_sync_summary, 'card') }}</el-descriptions-item>
        <el-descriptions-item label="文档">{{ status.file_id ? status.doc_url : (status.doc_url || '-') }}</el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="status.last_error" type="error" :closable="false" show-icon class="mt-12" :title="status.last_error" />
    </el-card>

    <!-- ===== 同步日志 ===== -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>同步日志(最近 10 条)</span>
          <el-button text type="primary" size="small" @click="loadLogs">刷新</el-button>
        </div>
      </template>
      <el-table :data="logs" size="small" border stripe empty-text="暂无同步日志">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="文件" prop="source_file" min-width="180" show-overflow-tooltip />
        <el-table-column label="315投诉" width="150">
          <template #default="{ row }">{{ logStat(row.external) }}</template>
        </el-table-column>
        <el-table-column label="卡商投诉" width="150">
          <template #default="{ row }">{{ logStat(row.card) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : row.status === 'partial' ? 'warning' : 'danger'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getTencentAuthorizeUrl,
  saveTencentDocConfig,
  getTencentDocStatus,
  syncTencentDocNow,
  revokeTencentDoc,
  listComplaintSyncLogs,
} from '@/api/docSync'

const status = ref({})
const credForm = reactive({ app_id: '', app_secret: '', redirect_uri: '' })
const docForm = reactive({ doc_url: '', external_sheet_name: '', card_sheet_name: '', enabled: true, sync_interval_minutes: 20 })
const logs = ref([])

const savingCred = ref(false)
const authorizing = ref(false)
const savingDoc = ref(false)
const syncing = ref(false)

const canSync = computed(() => status.value.app_configured && status.value.authorized && status.value.doc_configured)

async function loadStatus() {
  try {
    const res = await getTencentDocStatus()
    status.value = res.data || {}
  } catch (e) {
    // 401/403 时静默(管理员才有权限)
  }
}

async function loadLogs() {
  try {
    const res = await listComplaintSyncLogs({ page: 1, page_size: 10, status: undefined })
    logs.value = (res.data?.items || []).filter((l) => l.source_file && l.source_file !== 'workbuddy-push')
  } catch (e) {
    logs.value = []
  }
}

async function saveCredentials() {
  if (!credForm.app_id || !credForm.app_secret) {
    ElMessage.warning('请填写 ClientID 与 ClientSecret')
    return
  }
  savingCred.value = true
  try {
    await saveTencentDocConfig({ app_id: credForm.app_id, app_secret: credForm.app_secret, redirect_uri: credForm.redirect_uri })
    ElMessage.success('凭证已保存')
    await loadStatus()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    savingCred.value = false
  }
}

async function authorize() {
  authorizing.value = true
  try {
    const res = await getTencentAuthorizeUrl()
    const url = res.data?.authorize_url
    if (!url) throw new Error('未获取到授权链接')
    window.open(url, '_blank')
    ElMessage.info('已在新窗口打开腾讯文档授权页, 授权完成后自动回跳')
    // 轮询等待授权结果
    pollStatusUntilAuthorized()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '生成授权链接失败')
  } finally {
    authorizing.value = false
  }
}

let pollTimer = null
function pollStatusUntilAuthorized() {
  clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      await loadStatus()
      if (status.value.authorized) {
        clearInterval(pollTimer)
        ElMessage.success('授权成功, 已开始持续监测')
      }
    } catch (e) {
      clearInterval(pollTimer)
    }
  }, 3000)
}

async function revoke() {
  try {
    await ElMessageBox.confirm('解除授权后将停止同步, 需重新授权。确认解除?', '提示', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await revokeTencentDoc()
    ElMessage.success('已解除授权')
    await loadStatus()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

async function saveDocConfig() {
  savingDoc.value = true
  try {
    await saveTencentDocConfig({
      doc_url: docForm.doc_url,
      external_sheet_name: docForm.external_sheet_name,
      card_sheet_name: docForm.card_sheet_name,
      enabled: docForm.enabled,
      sync_interval_minutes: docForm.sync_interval_minutes,
    })
    ElMessage.success('配置已保存')
    await loadStatus()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    savingDoc.value = false
  }
}

async function syncNow() {
  syncing.value = true
  try {
    const res = await syncTencentDocNow()
    status.value = res.data?.status_detail || status.value
    ElMessage.success(`同步完成: 315 ${res.data?.external?.total ?? '-'} 行, 卡商 ${res.data?.card?.total ?? '-'} 行`)
    await loadLogs()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '同步失败')
    await loadStatus()
  } finally {
    syncing.value = false
  }
}

// ── 格式化 ──
function fmtTime(t) {
  if (!t) return ''
  const d = new Date(t)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function syncText(summary, key) {
  const s = summary?.[key]
  if (!s) return '-'
  return `${s.total} 行(+${s.inserted}/~${s.updated})`
}

function logStat(stat) {
  if (!stat) return '-'
  return `${stat.total} 行(+${stat.inserted}/~${stat.updated}${stat.failed ? `/${stat.failed}弃` : ''})`
}

onMounted(async () => {
  await loadStatus()
  // 表单回填
  credForm.app_id = status.value.app_id || ''
  if (status.value.redirect_uri) credForm.redirect_uri = status.value.redirect_uri
  if (status.value.doc_url) {
    docForm.doc_url = status.value.doc_url || ''
    docForm.external_sheet_name = status.value.external_sheet_name || ''
    docForm.card_sheet_name = status.value.card_sheet_name || ''
    docForm.enabled = status.value.enabled !== false
    docForm.sync_interval_minutes = status.value.sync_interval_minutes || 20
  }
  await loadLogs()
})
</script>

<style scoped>
.doc-sync-page {
  max-width: 960px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
.auth-area {
  padding: 4px 0;
}
.hint-text {
  font-size: 12px;
  color: #909399;
}
.compact-form .el-form-item {
  margin-bottom: 14px;
}
.mb-16 {
  margin-bottom: 16px;
}
.mb-12 {
  margin-bottom: 12px;
}
.mt-12 {
  margin-top: 12px;
}
.ml-8 {
  margin-left: 8px;
}
</style>
