<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">外部接入</div>
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
          <template #default="{ row }">{{ row.rate_limit_per_min ?? '全局默认' }}</template>
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
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <template v-if="row.enabled">
              <el-popconfirm title="吊销后该 token 立即失效, 确认吊销?" @confirm="revoke(row)">
                <template #reference>
                  <el-button link type="danger" size="small">吊销</el-button>
                </template>
              </el-popconfirm>
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
          <div class="sub-tip">reception=接待 / refund=退款 / complaint=投诉 / brief=经营简报</div>
        </el-form-item>
        <el-form-item label="限频(次/分)">
          <el-input-number v-model="form.rate_limit_per_min" :min="1" :max="100000" />
          <div class="sub-tip">留空=使用全局默认({{ cfg?.default_rate_limit_per_min }} 次/分钟)</div>
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

    <!-- 明文仅此一次展示 -->
    <el-dialog v-model="tokenVisible" title="生成成功 — Token 仅显示一次" width="560px">
      <el-alert type="warning" :closable="false" show-icon class="mb-16">
        <template #title>请立即复制保存, 关闭后无法再次查看明文。</template>
      </el-alert>
      <el-input :model-value="createdToken" readonly type="textarea" :rows="3" />
      <div class="sub-tip mb-16">调用示例: <code>curl -H "Authorization: Bearer {{ createdToken }}" "{{ baseUrl }}/api/v1/external/reception"</code></div>
      <template #footer>
        <el-button type="primary" @click="copyToken">复制并关闭</el-button>
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
          <el-input-number v-model="editForm.rate_limit_per_min" :min="1" :max="100000" />
          <div class="sub-tip">留空=全局默认</div>
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
import { Refresh, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import {
  externalConfig,
  listExternalTokens,
  createExternalToken,
  updateExternalToken,
  revokeExternalToken,
} from '@/api/externalAccess'

const loading = ref(false)
const tokens = ref([])
const cfg = ref(null)
const baseUrl = window.location.origin

const createVisible = ref(false)
const saving = ref(false)
const form = reactive({ name: '', scopes: [], rate_limit_per_min: null, expires_at: null, owner_note: '' })

const tokenVisible = ref(false)
const createdToken = ref('')

const editVisible = ref(false)
const editForm = reactive({ id: null, name: '', scopes: [], rate_limit_per_min: null, owner_note: '' })
const currentRow = ref(null)

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
    if (form.rate_limit_per_min) payload.rate_limit_per_min = form.rate_limit_per_min
    if (form.expires_at) payload.expires_at = form.expires_at
    if (form.owner_note) payload.owner_note = form.owner_note
    const res = await createExternalToken(payload)
    createdToken.value = res.data.token
    createVisible.value = false
    tokenVisible.value = true
    load()
  } catch (e) { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

function copyToken() {
  navigator.clipboard?.writeText(createdToken.value)
  ElMessage.success('已复制到剪贴板')
  tokenVisible.value = false
}

function openEdit(row) {
  currentRow.value = row
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
    const payload = { name: editForm.name, scopes: editForm.scopes, rate_limit_per_min: editForm.rate_limit_per_min }
    if (editForm.owner_note) payload.owner_note = editForm.owner_note
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
