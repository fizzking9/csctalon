<template>
  <div class="token-panel">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">外部只读 Token</div>
        <div class="page-sub">外部只读开放平台 · 供 workbuddy 等外部工具用 token+scope 拉取经营数据 / 简报</div>
      </div>
      <div>
        <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">生成 Token</el-button>
      </div>
    </div>

    <el-alert v-if="cfg" type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        接口状态：<el-tag :type="cfg.enabled ? 'success' : 'danger'" size="small" effect="light">{{ cfg.enabled ? '已启用' : '未启用' }}</el-tag>
        <span style="margin-left: 12px">默认限频：{{ cfg.default_rate_limit_per_min }} 次/分钟</span>
        <span style="margin-left: 12px">可用权限范围：{{ (cfg.scopes || []).join(' / ') }}</span>
      </template>
      <p class="alert-text">
        <strong>未启用提示</strong>：需在服务端 <code>.env</code> 设置 <code>EXTERNAL_API_ENABLED=true</code> 后外部接口才可访问（否则返回 503）。
        对外接口地址前缀 <code>/api/v1/external/…</code>，调用方携带 <code>Authorization: Bearer &lt;token&gt;</code>。
      </p>
    </el-alert>

    <el-card v-loading="loading">
      <el-table :data="tokens" border size="small">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="调用方" min-width="140" />
        <el-table-column label="Token 前缀" width="120">
          <template #default="{ row }">
            <code>{{ row.token_prefix }}…</code>
          </template>
        </el-table-column>
        <el-table-column label="权限范围" min-width="200">
          <template #default="{ row }">
            <el-tag v-for="s in (row.scopes || [])" :key="s" size="small" effect="plain" style="margin-right:4px">{{ s }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="限频(次/分)" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.rate_limit_per_min === 0" type="warning" size="small">不限制</el-tag>
            <template v-else>{{ row.rate_limit_per_min ?? '全局默认' }}</template>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.enabled" type="success" size="small">启用</el-tag>
            <el-tag v-else type="danger" size="small">已吊销</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="过期时间" width="120">
          <template #default="{ row }">{{ row.expires_at ? fmt(row.expires_at) : '—' }}</template>
        </el-table-column>
        <el-table-column label="最后使用" width="130">
          <template #default="{ row }">{{ row.last_used_at ? fmt(row.last_used_at) : '—' }}</template>
        </el-table-column>
        <el-table-column label="责任人" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.owner_note" class="owner-note">{{ row.owner_note }}</span>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="创建人" width="90">
          <template #default="{ row }">
            <el-tooltip :content="row.created_by || ''" placement="top" :disabled="!row.created_by">
              <span>{{ row.created_by_display || row.created_by || '—' }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <!-- #43: 操作列扩展 —— 配置包复制 / 换钥 / 恢复 -->
        <el-table-column label="操作" width="270" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="success" size="small" @click="openPackage(row)">配置包</el-button>
            <template v-if="row.enabled">
              <el-popconfirm
                title="换钥后旧 token 立即失效, 需通知调用方更新, 确认?"
                @confirm="rotate(row)"
              >
                <template #reference>
                  <el-button link type="warning" size="small">换钥</el-button>
                </template>
              </el-popconfirm>
              <el-popconfirm title="吊销后该 token 立即失效, 确认吊销?" @confirm="revoke(row)">
                <template #reference>
                  <el-button link type="danger" size="small">吊销</el-button>
                </template>
              </el-popconfirm>
            </template>
            <template v-else>
              <el-button link type="success" size="small" @click="enable(row)">恢复</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 生成 Token 对话框 -->
    <el-dialog v-model="createVisible" title="生成外部访问 Token" width="520px" :close-on-click-modal="false">
      <el-form label-width="110px" label-position="right">
        <el-form-item label="调用方标识" required>
          <el-input v-model="form.name" placeholder="如 workbuddy-日报简报" maxlength="64" />
        </el-form-item>
        <el-form-item label="权限范围" required>
          <el-checkbox-group v-model="form.scopes">
            <el-checkbox v-for="s in (cfg?.scopes || [])" :key="s" :label="s" :value="s">{{ s }}</el-checkbox>
          </el-checkbox-group>
          <div class="sub-tip">reception=接待 / refund=退款 / complaint=投诉 / brief=经营简报 / device=设备行为 / recharge=充值流量 / device_tag=设备标签 / schedule=排班考勤(#237)</div>
        </el-form-item>
        <el-form-item label="限频(次/分)">
          <el-input-number v-model="form.rate_limit_per_min" :min="0" :max="100000" />
          <div class="sub-tip">0=不限制 / 留空=使用全局默认({{ cfg?.default_rate_limit_per_min }} 次/分钟)</div>
        </el-form-item>
        <el-form-item label="过期时间">
          <el-date-picker v-model="form.expires_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss+08:00" placeholder="可选, 留空=不过期" />
        </el-form-item>
        <el-form-item label="责任人/用途">
          <el-input v-model="form.owner_note" placeholder="如: workbuddy-日报-李四跟进" maxlength="255" show-word-limit />
          <div class="sub-tip">可选, 记录该 token 责任人或用途说明</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">生成</el-button>
      </template>
    </el-dialog>

    <!-- 明文展示(生成/换钥共用): 主操作是「复制配置包」, 让调用方一次带走全部物料 -->
    <el-dialog v-model="tokenVisible" :title="tokenTitle" width="640px">
      <el-alert type="warning" :closable="false" show-icon class="mb-16">
        <template #title>
          明文仅此处显示一次（关闭后无法再次查看）。请<b>立即复制配置包</b>——
          配置包已包含接口地址、鉴权方式、端点清单、调用示例与提示词要求，一次带走。
        </template>
      </el-alert>
      <el-input :model-value="createdToken" readonly type="textarea" :rows="3" />
      <div class="sub-tip mb-16">
        若日后需要重新分发物料，可在列表点「配置包」（token 位置为占位符，请粘贴你保存的明文），
        或点「换钥」生成新明文并立即复制配置包。
      </div>
      <template #footer>
        <el-button @click="tokenVisible = false">关闭</el-button>
        <el-button @click="copyRawToken">仅复制 Token</el-button>
        <el-button type="primary" :loading="pkgLoading" @click="copyPackageForCreated">复制配置包</el-button>
      </template>
    </el-dialog>

    <!-- 配置包预览(#43): token 位置为占位符 -->
    <el-dialog v-model="pkgVisible" title="接入配置包（可直接复制给调用方）" width="760px">
      <el-alert type="info" :closable="false" show-icon class="mb-16">
        <template #title>
          用于 <b>{{ pkgTarget?.name }}</b>（{{ (pkgTarget?.scopes || []).join(' / ') }}）。
          内容中 token 位置为占位符 <code>&lt;在此粘贴你的 token&gt;</code>，系统不保存明文，无法回填。
        </template>
      </el-alert>
      <el-input v-model="pkgMarkdown" type="textarea" :rows="18" readonly class="pkg-textarea" />
      <template #footer>
        <el-button @click="pkgVisible = false">关闭</el-button>
        <el-button type="primary" :icon="DocumentCopy" @click="copyWithToast(pkgMarkdown, '配置包')">复制配置包</el-button>
      </template>
    </el-dialog>

    <!-- 编辑 Token 对话框(不含明文) -->
    <el-dialog v-model="editVisible" title="编辑 Token" width="520px">
      <el-form label-width="110px" label-position="right">
        <el-form-item label="调用方标识">
          <el-input v-model="editForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="权限范围">
          <el-checkbox-group v-model="editForm.scopes">
            <el-checkbox v-for="s in (cfg?.scopes || [])" :key="s" :label="s" :value="s">{{ s }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="限频(次/分)">
          <el-input-number v-model="editForm.rate_limit_per_min" :min="0" :max="100000" />
          <div class="sub-tip">0=不限制 / 空=全局默认({{ cfg?.default_rate_limit_per_min }} 次/分钟) / &gt;0=个性化</div>
        </el-form-item>
        <el-form-item label="责任人/用途">
          <el-input v-model="editForm.owner_note" maxlength="255" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh, Plus, DocumentCopy } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import {
  externalConfig,
  listExternalTokens,
  createExternalToken,
  updateExternalToken,
  revokeExternalToken,
  enableExternalToken,
  rotateExternalToken,
  getSetupPackage,
} from '@/api/externalAccess'
import { copyText, copyWithToast } from '@/utils/clipboard'

const loading = ref(false)
const tokens = ref([])
const cfg = ref(null)
// 对外地址用浏览器视角(反向代理下后端 request.base_url 可能是内网地址)
const baseUrl = window.location.origin

const createVisible = ref(false)
const saving = ref(false)
const form = reactive({ name: '', scopes: [], rate_limit_per_min: null, expires_at: null, owner_note: '' })

const tokenVisible = ref(false)
const createdToken = ref('')
const createdTokenId = ref(null)
const tokenTitle = ref('生成成功 — Token 仅显示一次')

const pkgVisible = ref(false)
const pkgMarkdown = ref('')
const pkgTarget = ref(null)
const pkgLoading = ref(false)

const editVisible = ref(false)
const editForm = reactive({ id: null, name: '', scopes: [], rate_limit_per_min: null, owner_note: '' })

function fmt(v) {
  return dayjs(v).format('YYYY-MM-DD HH:mm')
}

async function load() {
  loading.value = true
  try {
    const [cfgRes, tokenRes] = await Promise.all([externalConfig(), listExternalTokens()])
    cfg.value = cfgRes.data
    tokens.value = tokenRes.data || []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.name = ''
  form.scopes = []
  form.rate_limit_per_min = null
  form.expires_at = null
  form.owner_note = ''
  createVisible.value = true
}

async function submitCreate() {
  if (!form.name) { ElMessage.warning('请填写调用方标识'); return }
  if (!form.scopes.length) { ElMessage.warning('请至少选择一个权限范围'); return }
  saving.value = true
  try {
    const payload = { name: form.name, scopes: form.scopes }
    // ⚠️ v1.2: rate_limit_per_min 三值语义(0=不限制/null=全局默认/>0=个性化)。
    //   0 是真值但语义为"不限制", 必须用 !== null/undefined 判断(不能 if(x) 把 0 丢掉);
    //   null/undefined 不发 → 后端落 NULL(全局默认)。
    if (form.rate_limit_per_min !== null && form.rate_limit_per_min !== undefined) payload.rate_limit_per_min = form.rate_limit_per_min
    if (form.expires_at) payload.expires_at = form.expires_at
    if (form.owner_note) payload.owner_note = form.owner_note
    const res = await createExternalToken(payload)
    createdToken.value = res.data.token
    createdTokenId.value = res.data.id
    tokenTitle.value = '生成成功 — Token 仅显示一次'
    createVisible.value = false
    tokenVisible.value = true
    load()
  } catch (e) { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function copyRawToken() {
  await copyWithToast(createdToken.value, 'Token')
}

// 生成/换钥弹窗的「复制配置包」: 拉取配置包(内含占位符)并拼上真实明文
async function copyPackageForCreated() {
  if (!createdTokenId.value) return
  pkgLoading.value = true
  try {
    const res = await getSetupPackage(createdTokenId.value, { base_url: baseUrl })
    // 用真实明文替换占位符, 让用户一次复制即可直接交给调用方
    const withToken = (res.data?.markdown || '').replaceAll('<在此粘贴你的 token>', createdToken.value)
    await copyWithToast(withToken, '配置包(含明文 Token)')
  } catch (e) {
    ElMessage.error('获取配置包失败')
  } finally {
    pkgLoading.value = false
  }
}

async function openPackage(row) {
  pkgTarget.value = row
  pkgMarkdown.value = ''
  pkgVisible.value = true
  try {
    const res = await getSetupPackage(row.id, { base_url: baseUrl })
    pkgMarkdown.value = res.data?.markdown || ''
  } catch (e) {
    ElMessage.error('获取配置包失败')
  }
}

async function rotate(row) {
  try {
    const res = await rotateExternalToken(row.id)
    createdToken.value = res.data.token
    createdTokenId.value = res.data.id
    tokenTitle.value = '换钥成功 — 旧 Token 已立即失效'
    tokenVisible.value = true
    ElMessage.warning(res.data.notice || '旧 token 已失效, 请更新调用方配置')
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '换钥失败')
  }
}

async function enable(row) {
  try {
    const res = await enableExternalToken(row.id)
    if (res.data?.warning) {
      ElMessage.warning(res.data.warning)
    } else {
      ElMessage.success('已恢复启用')
    }
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '恢复失败')
  }
}

function openEdit(row) {
  editForm.id = row.id
  editForm.name = row.name
  editForm.scopes = [...(row.scopes || [])]
  editForm.rate_limit_per_min = row.rate_limit_per_min ?? null
  editForm.owner_note = row.owner_note || ''
  editVisible.value = true
}

async function submitEdit() {
  if (!editForm.name) { ElMessage.warning('请填写调用方标识'); return }
  if (!editForm.scopes.length) { ElMessage.warning('请至少选择一个权限范围'); return }
  saving.value = true
  try {
    // ⚠️ v1.2: rate_limit_per_min 三值语义。
    //   显式 null 表示"清回全局默认"(不能省略——否则后端视为"部分更新不改"); 0=不限制必须显式发送。
    const payload = {
      name: editForm.name,
      scopes: editForm.scopes,
      rate_limit_per_min: editForm.rate_limit_per_min ?? null,
    }
    // 始终发送 owner_note(含空串): 后端对 None 不更新、对空串清空。
    payload.owner_note = editForm.owner_note || ''
    await updateExternalToken(editForm.id, payload)
    ElMessage.success('已保存')
    editVisible.value = false
    load()
  } catch (e) { /* 已提示 */ } finally {
    saving.value = false
  }
}

async function revoke(row) {
  await revokeExternalToken(row.id)
  ElMessage.success('已吊销')
  load()
}

defineExpose({ load })

onMounted(load)
</script>

<style scoped>
.token-panel :deep(.el-card__body) {
  padding: 12px;
}
.flex-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
}
.page-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.alert-text {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
}
.sub-tip {
  color: #909399;
  font-size: 12px;
  line-height: 1.6;
  margin-top: 2px;
}
.dim { color: var(--el-text-color-secondary); }
.owner-note { font-size: 12px; }
.mb-16 { margin-bottom: 16px; }
.pkg-textarea :deep(textarea) {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
}
</style>
