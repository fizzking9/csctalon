<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">AI 报告模板</div>
        <div class="page-sub">预生成报告的 prompt 模板, 关键词路由, 调度时点, 多模型分工</div>
      </div>
      <div>
        <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
        <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建模板</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        模板同源: <code>question_template</code> 是 prompt 内容, 前台实时与后台预生成共用同一份;
        <code>question_keywords</code> 用于匹配用户问句(#122 匹配层);
        后台预生成用 <code>max_tokens</code> 上限 + <code>timeout_s=180</code>。
      </template>
    </el-alert>

    <el-card v-loading="loading">
      <el-table :data="items" border size="small">
        <el-table-column prop="priority" label="优先级" width="80" />
        <el-table-column label="名称" min-width="180">
          <template #default="{ row }">
            <div>{{ row.name_cn }}</div>
            <div class="dim">{{ row.code }}</div>
          </template>
        </el-table-column>
        <el-table-column label="Prompt" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="ellipsis">{{ row.question_template }}</div>
          </template>
        </el-table-column>
        <el-table-column label="关键词" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="kw in (row.question_keywords || [])" :key="kw" size="small" style="margin: 2px">{{ kw }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="周期/频率" width="140">
          <template #default="{ row }">
            <div>{{ row.period }}</div>
            <div class="dim">{{ row.frequency }} @ {{ row.trigger_at || '-' }}</div>
            <el-tag size="small" :type="row.refresh_on_sync ? 'success' : 'info'" effect="plain" style="margin-top:2px">
              {{ row.refresh_on_sync ? '随同步刷新' : '每日定时' }}
            </el-tag>
          </template>
        </el-table-column>
        <!-- #181 专项分桶: scope(专项域)/format(体裁) 展示 -->
        <el-table-column label="专项/体裁" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="scopeTagType(row.scope)" effect="plain" style="margin-right:4px">
              {{ scopeLabel(row.scope) }}
            </el-tag>
            <el-tag size="small" :type="row.format === 'detail' ? 'warning' : 'info'" effect="plain">
              {{ row.format === 'detail' ? '明细' : '简报' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="模型" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="providerTagType(row.llm_provider_code)" effect="plain">
              {{ providerDisplay(row.llm_provider_code) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="max_tokens" width="100">
          <template #default="{ row }">{{ row.max_tokens }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.is_active" size="small" @change="(v) => toggleActive(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="success" size="small" :loading="triggering[row.id]" @click="trigger(row)">触发</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm title="删除该模板? 关联报告会保留(template_id 置 NULL)" @confirm="remove(row)">
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新增/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑模板' : '新建模板'" width="640px">
      <el-form :model="form" label-width="120px" size="small">
        <el-form-item label="code">
          <el-input v-model="form.code" :disabled="!!form.id" placeholder="如 daily_general_morning" />
        </el-form-item>
        <el-form-item label="name_cn">
          <el-input v-model="form.name_cn" placeholder="如 每日晨会·整体经营" />
        </el-form-item>
        <el-form-item label="question_template">
          <el-input v-model="form.question_template" type="textarea" :rows="3" placeholder="prompt 内容, 可含 {{date}} {{period}}" />
        </el-form-item>
        <el-form-item label="question_keywords">
          <el-input v-model="keywordsInput" placeholder="逗号分隔, 如 今天,今日,整体" />
        </el-form-item>
        <el-form-item label="period">
          <el-select v-model="form.period" style="width: 100%" @change="autoRefreshByCycle">
            <el-option label="daily" value="daily" />
            <el-option label="weekly" value="weekly" />
            <el-option label="monthly" value="monthly" />
            <el-option label="range" value="range" />
          </el-select>
        </el-form-item>
        <el-form-item label="frequency">
          <el-select v-model="form.frequency" style="width: 100%" @change="autoRefreshByCycle">
            <el-option label="sync-triggered(同步后触发)" value="sync-triggered" />
            <el-option label="daily" value="daily" />
            <el-option label="weekly" value="weekly" />
            <el-option label="monthly" value="monthly" />
          </el-select>
        </el-form-item>
        <!-- #181 专项分桶: 生成侧按 scope 切片 feed(退款桶含涉诉/投诉桶三源), 匹配侧按问句专项路由 -->
        <el-form-item label="专项 scope">
          <el-select v-model="form.scope" style="width: 100%">
            <el-option label="overview·整体经营" value="overview" />
            <el-option label="refund·退款(含涉诉)" value="refund" />
            <el-option label="complaint·投诉(三源)" value="complaint" />
            <el-option label="reception·接待专项" value="reception" />
          </el-select>
          <span class="field-hint">整体经营=全维度(单桶会因 feed 超长截断致涉诉等答暂无); 专项桶只喂对应数据段, 命中按问句专项路由优先。</span>
        </el-form-item>
        <el-form-item label="体裁 format">
          <el-select v-model="form.format" style="width: 100%">
            <el-option label="brief·简报(结论导向)" value="brief" />
            <el-option label="detail·明细(逐维度展开)" value="detail" />
          </el-select>
          <span class="field-hint">简报只给核心结论与关键数字(每个维度 ≤2 行); 明细按维度逐项展开可含表格。</span>
        </el-form-item>
        <el-form-item label="trigger_at">
          <el-input v-model="form.trigger_at" placeholder="daily=HH:MM(09:00); weekly=wday-HH:MM(1-09:00=周一9点); monthly=day-HH:MM(1-09:00=每月1号9点)" />
        </el-form-item>
        <el-form-item label="随同步刷新">
          <el-switch v-model="form.refresh_on_sync" />
          <span class="field-hint">开启=每次高频数据同步成功后刷新（日报类时效强，建议开）；关闭=仅按 trigger_at 每日定时（周报/月报建议关）。新建时按周期自动识别，可手动改。</span>
        </el-form-item>
        <el-form-item label="max_tokens">
          <el-input-number v-model="form.max_tokens" :min="200" :max="8000" :step="100" />
        </el-form-item>
        <el-form-item label="llm_provider_code">
          <el-input v-model="form.llm_provider_code" placeholder="留空走多 provider 优先级" />
        </el-form-item>
        <el-form-item label="priority">
          <el-input-number v-model="form.priority" :min="1" :max="999" />
        </el-form-item>
        <el-form-item label="visibility_scope">
          <el-select v-model="form.visibility_scope" style="width: 100%">
            <el-option label="public(全员可看)" value="public" />
            <el-option label="admin_only(仅 admin)" value="admin_only" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Plus } from '@element-plus/icons-vue'
import { aiTemplates, createAiTemplate, updateAiTemplate, deleteAiTemplate, triggerAiTemplate } from '@/api/adminAi'

const loading = ref(false)
const items = ref([])
const triggering = reactive({})

// #146 模板绑定模型的可读显式标识(替代裸 provider_code 展示, 便于日常识别用哪个模型):
//   - 27b(实时问答主力, pregen_only=False) → 绿
//   - 122b(预生成慢模型, pregen_only=True) → 橙(非日常链)
//   - 留空 → 灰(走多 provider 优先级, 按 priority 取 first)
const PROVIDER_LABELS = {
  'talon-gmk-qwen3.8-27b': { name: '27b·实时问答', type: 'success' },
  'talon-gmk-qwen3.5-122b': { name: '122b·预生成', type: 'warning' },
  'talon-gmk-qwen2.5-7b': { name: '7b·语义', type: 'primary' },
}
const providerDisplay = (code) => (code ? (PROVIDER_LABELS[code]?.name || code) : '自动(优先级)')
const providerTagType = (code) => (code ? (PROVIDER_LABELS[code]?.type || 'info') : 'info')

// #181 专项分桶: scope 中文标签 + tag 颜色(overview 中性, 专项按色区分)
const SCOPE_LABELS = {
  overview: '整体经营', refund: '退款', complaint: '投诉', reception: '接待',
}
const scopeLabel = (s) => (s ? (SCOPE_LABELS[s] || s) : '整体经营')
const scopeTagType = (s) => (s === 'refund' ? 'danger' : s === 'complaint' ? 'warning' : s === 'reception' ? 'primary' : 'info')

const dialogVisible = ref(false)
const saving = ref(false)
const keywordsInput = ref('')
const form = reactive({
  id: null, code: '', name_cn: '', question_template: '',
  question_keywords: [], period: 'daily', frequency: 'daily',
  trigger_at: '', refresh_on_sync: true, max_tokens: 2000, llm_provider_code: '',
  priority: 100, visibility_scope: 'public', is_active: true,
  scope: 'overview', format: 'brief',
})

async function load() {
  loading.value = true
  try {
    const d = await aiTemplates()
    items.value = d.data || []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, {
    id: null, code: '', name_cn: '', question_template: '',
    question_keywords: [], period: 'daily', frequency: 'daily',
    trigger_at: '', refresh_on_sync: true, max_tokens: 2000, llm_provider_code: '',
    priority: 100, visibility_scope: 'public', is_active: true,
    scope: 'overview', format: 'brief',
  })
  keywordsInput.value = ''
  dialogVisible.value = true
}

// #130 自动识别: 新建(未保存)时切换周期/频率, 联动「随同步刷新」开关;
// 判据与后端 _auto_refresh_on_sync 一致(日报类→开)。编辑已有模板时不联动, 尊重手动选择。
function autoRefreshByCycle() {
  if (form.id != null) return
  form.refresh_on_sync =
    form.period === 'daily' || ['daily', 'sync-triggered'].includes(form.frequency)
}

function openEdit(row) {
  Object.assign(form, row, { id: row.id })
  keywordsInput.value = (row.question_keywords || []).join(',')
  dialogVisible.value = true
}

async function save() {
  if (!form.code || !form.name_cn || !form.question_template || !form.period || !form.frequency) {
    ElMessage.warning('code/name_cn/question_template/period/frequency 必填')
    return
  }
  form.question_keywords = keywordsInput.value.split(',').map(s => s.trim()).filter(Boolean)
  saving.value = true
  try {
    if (form.id) {
      await updateAiTemplate(form.id, { ...form })
      ElMessage.success('已更新')
    } else {
      await createAiTemplate({ ...form })
      ElMessage.success('已新建')
    }
    dialogVisible.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function toggleActive(row, v) {
  await updateAiTemplate(row.id, { is_active: v })
  ElMessage.success(v ? '已启用' : '已停用')
  load()
}

async function trigger(row) {
  triggering[row.id] = true
  try {
    const d = await triggerAiTemplate(row.id)
    ElMessage.success(`预生成成功: report_id=${d.data.report_id} total_ms=${d.data.total_ms}`)
    load()
  } catch (e) {
    ElMessage.error(`触发失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    triggering[row.id] = false
  }
}

async function remove(row) {
  await deleteAiTemplate(row.id)
  ElMessage.success('已删除')
  load()
}

load()
</script>

<style scoped>
/* #130 随同步刷新开关旁的口径说明: 帮 admin 判断日报类该开、周报/月报该关 */
.field-hint {
  margin-left: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.4;
}
</style>
