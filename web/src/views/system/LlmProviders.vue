<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">LLM 多 Provider</div>
        <div class="page-sub">AI 助手 · 模型接入管理（本地 Talon 网关 / 云端 DeepSeek 等，按优先级降级，连续失败自动摘除）</div>
        <el-alert type="warning" :closable="false" show-icon style="max-width: 860px; margin-top: 8px">
          <template #title>
            网关地址与 Key 在此配置；SN 不在本系统设置 —— SN 是在 <b>Talon 管理面板</b>创建 Key 时指定的身份名（如 SN-CSCTALON），Key 与 SN 一一绑定。
            Talon 重建/重装后 Key 会失配（调用报 401/403），需在 Talon 侧重新签发并把新 Key 填回本页；鉴权故障会在「事务通知」告警。
          </template>
        </el-alert>
      </div>
      <div>
        <el-button size="small" :icon="Refresh" @click="loadAll">刷新</el-button>
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建 Provider</el-button>
      </div>
    </div>

    <!-- ── 设计说明（Design Rationale）───────────────────────────────
      目的: 替**系统管理员**回答三个决策 ——
        ① 出域策略该设成什么（数据允不允许出内网）
        ② 每个角色**现在实际会用哪个模型**（而非"我配了什么"）
        ③ 某模型挂了会退到谁
      逻辑: 按**角色分区**而非扁平列表 —— 提问是"这个角色够用吗"，
        不是"系统里有哪些模型"；扁平列表答不出前者。每区直接显示
        **生效链**（含 DB/常量回退与云端过滤的真实结果），避免"配了没生效"的误解。
      取舍: 不做拖拽排序（上下箭头够用，且移动端不可用）；不做模型自动优选
        （实测数据量不足会引入抖动）；出域确认弹窗归**调用时**交互，不在此处；
        保留原扁平列表作为折叠区（删掉会让调用日志页无入口）。
    ──────────────────────────────────────────────────────────────── -->

    <!-- ① 出域策略 —— 一句话回答"数据到底能不能出内网" -->
    <el-card class="mb-16" v-loading="policyLoading">
      <template #header>
        <div class="flex-between">
          <span>数据出域策略</span>
          <el-tooltip placement="top">
            <template #content>
              云端模型会把 prompt 发往公网（如 api.deepseek.com）。<br />
              本地模型失败时是否允许自动降级到云端，由本策略决定。<br />
              <b>数据边界优先于可用性</b>：选「禁止出域」后本地全挂也不会出网。
            </template>
            <el-icon style="cursor: help"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
      </template>
      <el-radio-group v-model="cloudPolicy.policy" :disabled="policySaving" @change="saveCloudPolicy">
        <el-radio v-for="o in cloudPolicy.options" :key="o.value" :value="o.value" border class="mr-8">
          {{ o.label }}
          <el-tooltip placement="top" :content="o.desc">
            <el-icon style="margin-left: 4px; cursor: help"><QuestionFilled /></el-icon>
          </el-tooltip>
        </el-radio>
      </el-radio-group>
      <div class="dim mt-8">
        策略来源：<el-tag size="small" :type="cloudPolicy.source === 'db' ? 'success' : 'info'">
          {{ cloudPolicy.source === 'db' ? '数据库（界面可改）' : '环境变量 .env（界面保存后转为数据库）' }}
        </el-tag>
        <span v-if="cloudPolicy.updated_by" style="margin-left: 8px">
          最后修改：{{ cloudPolicy.updated_by }}
        </span>
      </div>
      <div class="dim mt-8">
        受此策略约束的云端 Provider：
        <template v-if="cloudPolicy.cloud_providers && cloudPolicy.cloud_providers.length">
          <el-tag v-for="p in cloudPolicy.cloud_providers" :key="p.code" type="warning" size="small" class="mr-8">
            {{ p.model }}
          </el-tag>
        </template>
        <span v-else>无</span>
      </div>
    </el-card>

    <!-- ② 角色分区 —— 每区显示成员与**实际生效链** -->
    <div class="flex-between mb-16" style="margin-top: 8px">
      <div>
        <div class="page-title" style="margin: 0">模型角色</div>
        <div class="page-sub">
          按任务能力需求分组（与「调用优先级 level」正交）。每区内按顺序降级：首选失败 → 备选 → 兜底。
        </div>
      </div>
    </div>

    <el-card v-for="r in roles" :key="r.role_key" class="mb-16" v-loading="rolesLoading">
      <template #header>
        <div class="flex-between">
          <div>
            <b>{{ r.display_name }}</b>
            <code style="margin-left: 8px">{{ r.role_key }}</code>
            <el-tag v-if="!r.enabled" type="info" size="small" style="margin-left: 8px">已停用</el-tag>
          </div>
          <div>
            <el-switch
              v-model="r.enabled" size="small" active-text="启用"
              :loading="roleSaving[r.role_key]" @change="(v) => saveRole(r, { enabled: v })"
            />
          </div>
        </div>
      </template>

      <div class="dim mb-8">{{ r.description }}</div>

      <!-- 生效链：最要紧的信息 —— 当前真正会用谁 -->
      <div class="mb-8">
        <span class="dim">当前生效链：</span>
        <template v-if="r.effective_chain && r.effective_chain.length">
          <el-tag
            v-for="(c, i) in r.effective_chain" :key="c" size="small"
            :type="i === 0 ? 'success' : 'info'" class="mr-8"
          >
            {{ i + 1 }}. {{ c }}
          </el-tag>
        </template>
        <span v-else class="dim">（空：将走默认链）</span>
      </div>

      <el-table :data="r.members" border size="small">
        <el-table-column label="顺序" width="80">
          <template #default="{ row }">
            <el-button-group>
              <el-button size="small" :disabled="row.role_order <= 1" @click="moveMember(r, row, -1)">↑</el-button>
              <el-button size="small" @click="moveMember(r, row, 1)">↓</el-button>
            </el-button-group>
          </template>
        </el-table-column>
        <el-table-column label="模型" min-width="200">
          <template #default="{ row }">
            <div>{{ row.name }}</div>
            <div class="dim">{{ row.model }}</div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.is_cloud" type="warning" size="small">云端</el-tag>
            <el-tag v-else type="success" size="small">本地</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="健康" width="110">
          <template #default="{ row }">
            <el-tag v-if="!row.enabled" type="info" size="small">未启用</el-tag>
            <el-tag v-else-if="row.healthy" type="success" size="small">健康</el-tag>
            <el-tag v-else type="danger" size="small">已降级</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="removeMember(r, row)">移出角色</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="dim mt-8">
        <b>选型依据：</b>{{ r.rationale }}
      </div>

      <div style="margin-top: 8px">
        <el-switch
          v-model="r.allow_cloud" size="small" active-text="允许该角色出域"
          :loading="roleSaving[r.role_key]" @change="(v) => saveRole(r, { allow_cloud: v })"
        />
        <span class="dim" style="margin-left: 8px">
          （与全局策略双重管控：两者都允许才会真正出域）
        </span>
      </div>
    </el-card>

    <!-- ③ 原有扁平列表 —— 保留为折叠区（调用日志等仍依赖 provider 明细） -->
    <el-collapse class="mb-16">
      <el-collapse-item name="providers">
        <template #title>
          <b>全部 Provider（扁平列表）</b>
          <span class="dim" style="margin-left: 8px">共 {{ items.length }} 个</span>
        </template>

        <el-alert type="info" :closable="false" show-icon class="mb-16">
          <template #title>
            调用按 <code>priority</code> 升序遍历启用且健康的 Provider，瞬时错误（5xx/429/超时）降级下一个；
            连续失败 <code>fail_threshold</code> 次自动摘除（<code>healthy=false</code>），需手动恢复或成功一次自愈。
            <code>API Key</code> 加密入库，明文不返回；列表仅显示前 6 位用于识别。
          </template>
        </el-alert>

        <el-table :data="items" border size="small">
          <el-table-column prop="priority" label="优先级" width="80" />
          <el-table-column label="名称" min-width="180">
            <template #default="{ row }">
              <div>{{ row.name }}</div>
              <div class="dim">{{ row.code }}</div>
            </template>
          </el-table-column>
          <el-table-column label="端点" min-width="220">
            <template #default="{ row }">
              <div class="ellipsis">{{ row.base_url }}</div>
              <div class="dim">{{ row.model }}</div>
            </template>
          </el-table-column>
          <el-table-column label="出域" width="80">
            <template #default="{ row }">
              <el-tag v-if="row.is_cloud" type="warning" size="small">云端</el-tag>
              <el-tag v-else type="success" size="small">本地</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="角色" width="100">
            <template #default="{ row }">
              <span v-if="row.role_scope">{{ roleName(row.role_scope) }}</span>
              <span v-else class="dim">不限</span>
            </template>
          </el-table-column>
          <el-table-column label="Key" width="100">
            <template #default="{ row }">
              <span v-if="row.api_key_configured"><code>{{ row.api_key_prefix }}…</code></span>
              <span v-else class="dim">无</span>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="80">
            <template #default="{ row }">
              <el-switch :model-value="row.enabled" size="small" @change="(v) => toggleEnabled(row, v)" />
            </template>
          </el-table-column>
          <el-table-column label="健康" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.healthy" type="success" size="small">健康</el-tag>
              <el-tag v-else type="danger" size="small">已降级</el-tag>
              <div class="dim">失败 {{ row.fail_count }}</div>
            </template>
          </el-table-column>
          <el-table-column label="超时(秒)" width="90">
            <template #default="{ row }">{{ row.timeout_s }}</template>
          </el-table-column>
          <el-table-column label="最近调用" width="160">
            <template #default="{ row }">
              <div class="dim">成功: {{ fmt(row.last_success_at) }}</div>
              <div class="dim">失败: {{ fmt(row.last_fail_at) }}</div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button link type="success" size="small" :loading="testing[row.id]" @click="testExisting(row)">测试</el-button>
              <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button v-if="!row.healthy" link type="warning" size="small" @click="restoreHealthy(row)">恢复</el-button>
              <el-popconfirm title="删除该 Provider? 删除后调用时不再选它" @confirm="remove(row)">
                <template #reference>
                  <el-button link type="danger" size="small">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <div class="flex-between mb-16" style="margin-top: 24px">
      <div>
        <div class="page-title" style="margin: 0">调用审计日志</div>
        <div class="page-sub">每次 LLM 调用一行审计（成功/失败均记，不含 messages/响应正文）</div>
      </div>
      <div>
        <el-select v-model="logFilter.status" placeholder="状态" size="small" clearable style="width: 120px; margin-right: 8px" @change="loadLogs">
          <el-option label="success" value="success" />
          <el-option label="transient" value="transient" />
          <el-option label="permanent" value="permanent" />
        </el-select>
        <el-select v-model="logFilter.source" placeholder="来源" size="small" clearable style="width: 160px" @change="loadLogs">
          <el-option label="ai_analyze" value="ai_analyze" />
          <el-option label="llm_time_parse" value="llm_time_parse" />
        </el-select>
      </div>
    </div>

    <el-card v-loading="logLoading">
      <el-table :data="logs.items" border size="small">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column label="Provider" min-width="160">
          <template #default="{ row }">
            <div>{{ row.provider_code || '—' }}</div>
            <div class="dim">{{ row.model || '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ row.status }}</el-tag>
            <div v-if="row.status_code" class="dim">HTTP {{ row.status_code }}</div>
          </template>
        </el-table-column>
        <el-table-column label="耗时(ms)" width="100">
          <template #default="{ row }">{{ row.duration_ms }}</template>
        </el-table-column>
        <el-table-column label="输入/输出字符" width="140">
          <template #default="{ row }">
            <div>入: {{ row.prompt_chars ?? '—' }}</div>
            <div>出: {{ row.out_chars ?? '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="140">
          <template #default="{ row }">{{ row.source || '—' }}</template>
        </el-table-column>
        <el-table-column label="错误" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.error_message || '—' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 12px; text-align: right">
        <el-pagination
          v-model:current-page="logPage"
          :page-size="logFilter.page_size"
          :total="logs.total"
          layout="total, prev, pager, next"
          @current-change="loadLogs"
        />
      </div>
    </el-card>

    <!-- 新建/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑 Provider' : '新建 Provider'" width="640px" :close-on-click-modal="false">
      <el-form label-width="110px" label-position="right">
        <el-form-item v-if="!editingId" label="code" required>
          <el-input v-model="form.code" placeholder="唯一标识, 如 talon-gmk-qwen3-coder-30b" maxlength="64" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如 Talon·GMK·qwen3-coder:30b" maxlength="128" />
        </el-form-item>
        <el-form-item label="base_url" required>
          <el-input v-model="form.base_url" placeholder="OpenAI 兼容完整前缀; 本地 Ollama/Talon 带 /v1, DeepSeek 不带" />
        </el-form-item>
        <el-form-item label="模型名" required>
          <el-input v-model="form.model" placeholder="如 qwen3-coder:30b / deepseek-v4-flash" maxlength="128" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.api_key" type="password" show-password
                    :placeholder="editingId ? '留空=不改; 传值=重置' : '无鉴权服务(本地无 key)可留空'" />
          <div class="sub-tip">加密入库; 明文不返回</div>
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="form.priority" :min="0" :max="9999" />
          <div class="sub-tip">数字小=优先级高; 调用按此升序遍历</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="思考模式">
          <el-select v-model="form.thinking" style="width: 100%">
            <el-option label="disabled (关闭, 推荐)" value="disabled" />
            <el-option label="low (轻量思考)" value="low" />
            <el-option label="high (深度思考)" value="high" />
          </el-select>
          <div class="sub-tip">DeepSeek 专用; 其它 OpenAI 兼容服务忽略</div>
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="form.timeout_s" :min="1" :max="600" :step="10" />
        </el-form-item>
        <el-form-item label="重试次数">
          <el-input-number v-model="form.retry_max" :min="0" :max="10" />
          <div class="sub-tip">瞬时失败(5xx/429/超时)指数退避重试次数</div>
        </el-form-item>
        <el-form-item label="失败阈值">
          <el-input-number v-model="form.fail_threshold" :min="1" :max="20" />
          <div class="sub-tip">连续失败 N 次自动摘除(healthy=false)</div>
        </el-form-item>
        <el-form-item label="数据出域">
          <el-switch v-model="form.is_cloud" active-text="云端(会发往公网)" />
          <div class="sub-tip">
            标记后受全局出域策略约束；本地服务保持关闭。
          </div>
        </el-form-item>
        <el-form-item label="归属角色">
          <el-select v-model="form.role_scope" clearable placeholder="不限角色" style="width: 100%">
            <el-option v-for="r in roles" :key="r.role_key"
                       :label="`${r.display_name} (${r.role_key})`" :value="r.role_key" />
          </el-select>
          <div class="sub-tip">归属后可在上方角色区按顺序调整兜底优先级</div>
        </el-form-item>
        <el-form-item label="角色内顺序">
          <el-input-number v-model="form.role_order" :min="1" :max="9999" />
          <div class="sub-tip">数字小=先尝试；仅「归属角色」非空时有意义</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" type="textarea" :rows="2" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 独立测试连接对话框 -->
    <el-dialog v-model="testDialogVisible" title="测试任意配置的连通性" width="540px">
      <el-form label-width="100px">
        <el-form-item label="base_url" required>
          <el-input v-model="testForm.base_url" placeholder="http://192.168.10.21:4080/v1" />
        </el-form-item>
        <el-form-item label="模型" required>
          <el-input v-model="testForm.model" placeholder="qwen3-coder:30b" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="testForm.api_key" type="password" show-password placeholder="无鉴权服务可留空" />
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="testForm.timeout_s" :min="5" :max="120" />
        </el-form-item>
      </el-form>
      <el-alert v-if="testResult" :type="testResult.ok ? 'success' : 'error'" :closable="false" show-icon style="margin-top: 8px">
        <template #title>
          <span v-if="testResult.ok">✓ 成功 ({{ testResult.duration_ms }}ms, HTTP {{ testResult.status_code }})</span>
          <span v-else>✗ 失败 ({{ testResult.duration_ms }}ms, {{ testResult.error }})</span>
        </template>
        <div v-if="testResult.sample" style="margin-top: 4px">样例输出: <code>{{ testResult.sample }}</code></div>
      </el-alert>
      <template #footer>
        <el-button @click="testDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="testingAny" @click="testAny">测试</el-button>
      </template>
    </el-dialog>

    <div style="margin-top: 16px">
      <el-button size="small" :icon="Connection" @click="testDialogVisible = true">独立测试任意配置</el-button>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { Refresh, Plus, Connection, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  listLlmProviders, createLlmProvider, updateLlmProvider, deleteLlmProvider,
  testLlmConnection, testExistingLlm, listLlmCallLogs,
  listLlmRoles, updateLlmRole, getCloudPolicy, updateCloudPolicy,
  removeLlmRoleMember, setLlmRoleMemberOrder,
} from '@/api/llmProviders'

const loading = ref(false)
const items = ref([])
const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref(null)
const testing = reactive({})
const form = reactive({
  code: '', name: '', base_url: '', model: '', api_key: '',
  priority: 100, enabled: true, thinking: 'disabled',
  timeout_s: 120, retry_max: 2, fail_threshold: 3, note: '',
  // 2026-09-21 出域管控 + 角色归属
  is_cloud: false, role_scope: null, role_order: 100,
})

// ── 角色（能力档位）与出域策略（2026-09-21）──
const roles = ref([])
const rolesLoading = ref(false)
const roleSaving = reactive({})
const policyLoading = ref(false)
const policySaving = ref(false)
const cloudPolicy = reactive({ policy: 'explicit_only', options: [], cloud_providers: [] })

function roleName(key) {
  const r = roles.value.find((x) => x.role_key === key)
  return r ? r.display_name : key
}

const testDialogVisible = ref(false)
const testingAny = ref(false)
const testForm = reactive({
  base_url: 'http://192.168.10.21:4080/v1', model: 'qwen3-coder:30b',
  api_key: '', timeout_s: 30,
})
const testResult = ref(null)

const logLoading = ref(false)
const logs = ref({ items: [], total: 0 })
const logPage = ref(1)
const logFilter = reactive({ page_size: 30, status: null, source: null })

function statusTag(s) {
  return { success: 'success', transient: 'warning', permanent: 'danger', failed: 'danger' }[s] || 'info'
}

function fmt(dt) {
  if (!dt) return '—'
  try {
    const d = new Date(dt)
    if (isNaN(d.getTime())) return dt
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
  } catch { return dt }
}

async function load() {
  loading.value = true
  try {
    const res = await listLlmProviders()
    items.value = res.data || []
  } catch (e) { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

/** 刷新全部：Provider 列表 + 角色 + 出域策略（三者互相影响，需一起刷新）。 */
async function loadAll() {
  await Promise.all([load(), loadRoles(), loadCloudPolicy()])
}

async function loadRoles() {
  rolesLoading.value = true
  try {
    const res = await listLlmRoles()
    roles.value = res.data || []
  } catch (e) { /* 拦截器已提示 */ } finally {
    rolesLoading.value = false
  }
}

async function loadCloudPolicy() {
  policyLoading.value = true
  try {
    const res = await getCloudPolicy()
    Object.assign(cloudPolicy, res.data || {})
  } catch (e) { /* 拦截器已提示 */ } finally {
    policyLoading.value = false
  }
}

async function saveCloudPolicy() {
  // 2026-09-21 已落库（llm_cloud_policy 单行表）—— 界面改动即时生效，无需重启。
  // ⚠️ 这是数据边界开关：auto 会让本地失败时自动出网。
  policySaving.value = true
  try {
    const res = await updateCloudPolicy({ policy: cloudPolicy.policy })
    Object.assign(cloudPolicy, res.data || {})
    if (cloudPolicy.policy === 'auto') {
      ElMessage.warning('已设为「自动降级」：本地模型失败时会自动把数据发往云端')
    } else if (cloudPolicy.policy === 'never') {
      ElMessage.success('已设为「禁止出域」：数据不会离开内网')
    } else {
      ElMessage.success('已保存（仅显式允许时才用云端）')
    }
  } catch (e) {
    // 保存失败 → 回滚显示为实际生效值，避免"改了但没生效"的错觉
    await loadCloudPolicy()
  } finally {
    policySaving.value = false
  }
}

async function saveRole(role, patch) {
  roleSaving[role.role_key] = true
  try {
    await updateLlmRole(role.role_key, patch)
    ElMessage.success('已保存')
    // 角色变更会影响生效链，需刷新角色与 provider 列表
    await Promise.all([loadRoles(), load()])
  } catch (e) { /* 拦截器已提示 */ } finally {
    roleSaving[role.role_key] = false
  }
}

/** 调整角色内成员顺序（改关联表 sort_order，小=先尝试）。
 *
 * ⚠️ 2026-09-21: 改用关联表接口 —— 同一 provider 可在多个角色中有**独立**顺序
 *   （如 coder-30b 在 assistant 是首选、在 specialist 是备选）。
 *   旧实现改的是 provider.role_order 单值列，会互相覆盖（已实测证伪）。
 */
async function moveMember(role, member, delta) {
  const list = role.members
  const idx = list.findIndex((m) => m.id === member.id)
  const target = idx + delta
  if (target < 0 || target >= list.length) return
  const a = list[idx]
  const b = list[target]
  // 交换两者顺序；若值相同用序号强制拉开
  let newA = b.role_order
  const newB = a.role_order
  if (newA === newB) newA = delta < 0 ? newB - 1 : newB + 1
  try {
    await Promise.all([
      setLlmRoleMemberOrder(role.role_key, a.code, newA),
      setLlmRoleMemberOrder(role.role_key, b.code, newB),
    ])
    ElMessage.success('顺序已调整')
    await loadRoles()
  } catch (e) { /* 拦截器已提示 */ }
}

/** 把成员移出角色（仅解除关联，**不删 Provider**）。 */
async function removeMember(role, member) {
  try {
    await removeLlmRoleMember(role.role_key, member.code)
    ElMessage.success('已移出角色（Provider 本身保留）')
    await loadRoles()
  } catch (e) { /* 拦截器已提示 */ }
}

async function loadLogs() {
  logLoading.value = true
  try {
    const res = await listLlmCallLogs({
      page: logPage.value,
      page_size: logFilter.page_size,
      status: logFilter.status || undefined,
      source: logFilter.source || undefined,
    })
    logs.value = res.data || { items: [], total: 0 }
  } catch (e) { /* 拦截器已提示 */ } finally {
    logLoading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    code: '', name: '', base_url: 'http://192.168.10.21:4080/v1', model: '',
    api_key: '', priority: 100, enabled: true, thinking: 'disabled',
    timeout_s: 120, retry_max: 2, fail_threshold: 3, note: '',
    is_cloud: false, role_scope: null, role_order: 100,
  })
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    code: row.code, name: row.name, base_url: row.base_url, model: row.model,
    api_key: '', priority: row.priority, enabled: row.enabled, thinking: row.thinking,
    timeout_s: row.timeout_s, retry_max: row.retry_max, fail_threshold: row.fail_threshold,
    note: row.note || '',
    // 2026-09-21 出域管控 + 角色归属（缺字段兼容旧后端）
    is_cloud: row.is_cloud ?? false,
    role_scope: row.role_scope ?? null,
    role_order: row.role_order ?? 100,
  })
  dialogVisible.value = true
}

async function save() {
  if (!editingId.value && !form.code) return ElMessage.warning('code 必填')
  if (!form.name || !form.base_url || !form.model) return ElMessage.warning('name/base_url/model 必填')
  saving.value = true
  try {
    if (editingId.value) {
      const payload = { ...form }
      // 留空 api_key 不传(不改 key)
      if (!payload.api_key) delete payload.api_key
      delete payload.code
      // role_scope 置空串 = 移出角色（后端白名单允许空串）
      if (payload.role_scope === null) payload.role_scope = ''
      await updateLlmProvider(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      const payload = { ...form }
      if (payload.role_scope === null) delete payload.role_scope
      await createLlmProvider(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    // 角色归属可能变化，需刷新角色区
    await Promise.all([load(), loadRoles()])
  } catch (e) { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, v) {
  try {
    await updateLlmProvider(row.id, { enabled: v })
    ElMessage.success(v ? '已启用' : '已停用')
    load()
  } catch (e) { /* 拦截器已提示 */ }
}

async function restoreHealthy(row) {
  try {
    await updateLlmProvider(row.id, { healthy: true, fail_count: 0 })
    ElMessage.success('已恢复为健康(下次调用会选用)')
    load()
  } catch (e) { /* 拦截器已提示 */ }
}

async function remove(row) {
  try {
    await deleteLlmProvider(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) { /* 拦截器已提示 */ }
}

async function testExisting(row) {
  testing[row.id] = true
  try {
    const res = await testExistingLlm(row.id)
    const r = res.data || {}
    if (r.ok) ElMessage.success(`连通正常 (${r.duration_ms}ms)`)
    else ElMessage.error(`失败: ${r.error} (${r.duration_ms}ms)`)
  } catch (e) { /* 拦截器已提示 */ } finally {
    testing[row.id] = false
  }
}

async function testAny() {
  if (!testForm.base_url || !testForm.model) return ElMessage.warning('base_url/model 必填')
  testingAny.value = true
  testResult.value = null
  try {
    const res = await testLlmConnection(testForm)
    testResult.value = res.data || {}
  } catch (e) { /* 拦截器已提示 */ } finally {
    testingAny.value = false
  }
}

loadAll()
loadLogs()
</script>

<style scoped>
.dim { color: #909399; font-size: 12px; }
.ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub-tip { color: #909399; font-size: 12px; margin-top: 4px; }
.mr-8 { margin-right: 8px; }
.mt-8 { margin-top: 8px; }
</style>
