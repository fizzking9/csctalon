<!--
  ┌──────────────────────── 设计说明（Design Rationale） ─────────────────────────┐
  │ 工单：#157 · 设计：docs/design/AI数据接入助手设计-20260919.md §七             │
  │ 性质：REQ（具体功能）— 非元需求，不进 AGENTS.md 引用链                        │
  │ 目的（替谁/答何决策）：替 admin（数据接入负责人）在新数据源接入时回答           │
  │       「这份样例能不能接、该怎么接、有没有违反规范」——每个新数据源用一次，       │
  │       决定了「接入声明怎么写、能否进入建表/注册流程」。                        │
  │ 逻辑（为何这样组织）：按接入闭环的**动作顺序**单页走完：                        │
  │       ① 提交样例（起草）→ ② 选一份草稿 → ③ 看 AI 建议 + 校验报告 →            │
  │       ④ 人工修正 → ⑤ 确认 → ⑥ 生成落地包。                                   │
  │       详情区把「校验 fail/warn 计数」放在最上（可控闸门前置），字段明细在下 ——  │
  │       因为决定「能不能接」的是 fail 是否清零，而不是字段表有多长。              │
  │ 口径：草稿与 AI 建议存 data_source_specs；校验口径 = 校验器 R1~R14 规则，来源为
  │       docs/standards/数据源命名与分组规范.md（前缀白名单/全树唯一/禁 code 原文）、
  │       docs/standards/数据采集合并去重机制说明.md（去重键红线）、
  │       docs/standards/数据时间字段使用规范.md（时区），详见设计文档 §四；
  │       金额一律以「分」为单位（AGENTS.md 铁律）。
  │ 取舍（不做什么及原因）：                                                     │
  │   ❌ 不做「一键落地」（自动建表/写 registry）—— 落地包只是建议，真正写库/写码   │
  │      必须人工按 issue-workflow 走（设计 §1.3 非目标），这是防 AI 幻觉的底线；   │
  │   ❌ 不做草稿删除 —— 起草与确认动作要留痕可审计（谁在什么时候建议过什么）；     │
  │   ❌ 不做提示词/模板编辑入口 —— 提示词属「AI 管理」配置侧，本页只消费其结果；   │
  │   ❌ 不做自动重试按钮 —— 失败后重新提交即可（样例仍在本页输入框里），          │
  │      避免为「重试」单开一条后端状态机分支。                                   │
  └──────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">数据源接入助手</div>
        <div class="page-sub">样例 → AI 起草接入声明 → 规范校验 → 人工确认 → 落地包（不自动落地）</div>
      </div>
      <div class="flex-center gap-8">
        <el-popover placement="bottom-end" :width="520" trigger="click">
          <template #reference>
            <el-button size="small" :icon="InfoFilled" circle />
          </template>
          <div class="rationale">
            <p><b>替谁：</b>admin（数据接入负责人）。新数据源接入时用，每个源一次。</p>
            <p><b>答何决策：</b>这份样例能不能接、该怎么接（字段语义/命名/分组/去重键）、有没有违反规范。</p>
            <p><b>怎么用：</b>① 粘贴样例起草 → ② 左侧选一份草稿 → ③ 看校验 fail/warn → ④ 人工修正 → ⑤ 确认 → ⑥ 生成落地包。</p>
            <p><b>数据口径：</b>AI 建议与草稿存 <code>data_source_specs</code>；校验口径为规范校验器 R1~R14，来源为
              <code>docs/standards/数据源命名与分组规范.md</code>（前缀白名单/唯一）、
              <code>docs/standards/数据采集合并去重机制说明.md</code>（去重红线）、
              <code>docs/standards/数据时间字段使用规范.md</code>（时区）。详见
              <code>docs/design/AI数据接入助手设计-20260919.md</code> §四。</p>
            <p><b>不做什么（及原因）：</b></p>
            <ul>
              <li>❌ <b>不自动落地</b>：落地包只是 JSON 建议，建表/写 registry/改代码必须人工按 issue-workflow 执行 —— 这是防 AI 幻觉的底线；</li>
              <li>❌ <b>不删草稿</b>：起草与确认要留痕可审计；</li>
              <li>❌ <b>不做提示词编辑</b>：提示词属「AI 管理」配置侧，本页只消费其结果；</li>
              <li>❌ <b>不做自动重试按钮</b>：失败后重新提交即可（样例还在输入框里）。</li>
            </ul>
            <p><b>工单：</b>#157</p>
          </div>
        </el-popover>
        <el-button size="small" :icon="Refresh" @click="reloadAll">刷新</el-button>
      </div>
    </div>

    <!-- ① 起草入口（可折叠：低频动作，不占常驻视野） -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="flex-between">
          <span class="card-title">① 提交样例起草接入声明</span>
          <el-button link type="primary" size="small" @click="composerOpen = !composerOpen">
            {{ composerOpen ? '收起' : '展开' }}
          </el-button>
        </div>
      </template>
      <div v-show="composerOpen">
        <el-form label-width="96px" size="small">
          <el-form-item label="样例数据">
            <el-input
              v-model="form.samplesText"
              type="textarea"
              :rows="6"
              placeholder="粘贴 CSV/TSV（首行表头，可直接从 Excel 复制）或 JSON 数组，最多 50 行。敏感字段（手机号/证件号/邮箱）后端会脱敏后再送模型。"
            />
          </el-form-item>
          <el-form-item label="表名提示">
            <el-input v-model="form.tableHint" placeholder="物理表名，如 bms_order（AI 不负责造表名，此项人工给）" />
          </el-form-item>
          <el-form-item label="业务备注">
            <el-input v-model="form.businessNote" placeholder="这份数据是什么业务、从哪来（可留空）" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="submitDraft">提交起草</el-button>
            <span class="hint">提交后 AI 起草中（后台排队），可继续操作其它草稿。</span>
          </el-form-item>
        </el-form>
      </div>
    </el-card>

    <!-- ② 左列表 + ③ 右详情 -->
    <el-row :gutter="12">
      <el-col :xs="24" :md="9">
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span class="card-title">② 草稿列表</span>
              <el-select v-model="filterStatus" size="small" style="width: 130px" @change="loadDrafts">
                <el-option label="全部状态" value="" />
                <el-option label="起草中" value="drafting" />
                <el-option label="待确认" value="draft" />
                <el-option label="起草失败" value="failed" />
                <el-option label="已确认" value="confirmed" />
                <el-option label="已出落地包" value="applied" />
                <el-option label="已拒绝" value="rejected" />
              </el-select>
            </div>
          </template>
          <el-table
            v-loading="loadingList"
            :data="drafts"
            size="small"
            highlight-current-row
            :current-row-key="current?.id"
            row-key="id"
            @current-change="selectDraft"
          >
            <el-table-column label="声明" min-width="150">
              <template #default="{ row }">
                <div>{{ row.name_cn || '（起草中）' }}</div>
                <div class="dim">{{ row.code || 'code 待 AI 建议' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="92">
              <template #default="{ row }">
                <el-tag size="small" :type="statusType(row.status)" effect="plain">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="校验" width="76">
              <template #default="{ row }">
                <span v-if="row.validator_report" class="dim">
                  <span :class="{ 'txt-fail': failCount(row) > 0 }">F{{ failCount(row) }}</span>
                  / W{{ warnCount(row) }}
                </span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="dim mt-8">共 {{ total }} 条（admin 可见全部；其他人只见自己提交的）</div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="15">
        <el-card v-if="!current" shadow="never">
          <el-empty description="左侧选一份草稿查看 AI 建议与校验报告" :image-size="60" />
        </el-card>

        <div v-else>
          <!-- ③ 状态与闸门（可控指标前置：fail 未清零不许确认） -->
          <el-card shadow="never" class="mb-12">
            <div class="flex-between">
              <div class="flex-center gap-8">
                <el-tag :type="statusType(current.status)" effect="dark">{{ statusText(current.status) }}</el-tag>
                <span class="strong">{{ current.name_cn || '（起草中）' }}</span>
                <span class="dim">{{ current.code || '—' }}</span>
              </div>
              <div class="flex-center gap-8">
                <el-tag v-if="failCount(current)" type="danger" size="small">fail {{ failCount(current) }}（禁止确认）</el-tag>
                <el-tag v-if="warnCount(current)" type="warning" size="small">warn {{ warnCount(current) }}（需逐条备注）</el-tag>
                <el-tag v-if="current.issue_no" type="success" size="small">工单 {{ current.issue_no }}</el-tag>
              </div>
            </div>
            <el-alert v-if="current.status === 'drafting'" type="info" :closable="false" show-icon class="mt-8"
              title="AI 起草中…（后台排队执行，页面每 2 秒自动刷新）" />
            <el-alert v-else-if="current.status === 'failed'" type="error" :closable="false" show-icon class="mt-8"
              :title="`起草失败：${current.error_message || '未知原因'}`"
              description="样例仍在上方输入框，修正后重新提交即可。" />
          </el-card>

          <!-- ④ 声明主体（人工可改；改后自动重跑校验） -->
          <el-card shadow="never" class="mb-12">
            <template #header>
              <div class="flex-between">
                <span class="card-title">④ 接入声明（AI 建议，人工可改）</span>
                <el-button size="small" type="primary" :disabled="!editable" @click="saveSpec">保存并重跑校验</el-button>
              </div>
            </template>
            <el-form label-width="92px" size="small">
              <el-row :gutter="8">
                <el-col :span="12"><el-form-item label="code"><el-input v-model="edit.code" :disabled="!editable" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="中文名"><el-input v-model="edit.name_cn" :disabled="!editable" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="物理表名"><el-input v-model="edit.table_name" :disabled="!editable" /></el-form-item></el-col>
                <el-col :span="12">
                  <el-form-item label="分组">
                    <el-select v-model="edit.category" :disabled="!editable" style="width: 100%">
                      <el-option v-for="c in snapshot.category_order || []" :key="c" :label="c" :value="c" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="12"><el-form-item label="时间字段"><el-input v-model="edit.time_field" :disabled="!editable" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="SN 字段"><el-input v-model="edit.sn_field" :disabled="!editable" /></el-form-item></el-col>
                <el-col :span="12"><el-form-item label="主键字段"><el-input v-model="edit.pk_field" :disabled="!editable" /></el-form-item></el-col>
                <el-col :span="12">
                  <el-form-item label="去重策略">
                    <el-select v-model="edit.pk_strategy" :disabled="!editable" style="width: 100%">
                      <el-option label="primary_key" value="primary_key" />
                      <el-option label="upsert" value="upsert" />
                      <el-option label="none" value="none" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
              <el-form-item label="描述">
                <el-input v-model="edit.description" type="textarea" :rows="2" :disabled="!editable" />
              </el-form-item>
            </el-form>
          </el-card>

          <!-- ⑤ 校验报告（fail 前置，warn 逐条备注） -->
          <el-card shadow="never" class="mb-12">
            <template #header><span class="card-title">⑤ 规范校验报告（R1~R14）</span></template>
            <div v-if="!report" class="dim">暂无报告（起草完成后自动生成）</div>
            <div v-else>
              <div v-if="report.fail?.length" class="mb-8">
                <div class="rep-title txt-fail">fail（{{ report.fail.length }}）— 未清零禁止确认</div>
                <div v-for="(f, i) in report.fail" :key="'f' + i" class="rep-line">
                  <el-tag size="small" type="danger" effect="plain">{{ f.rule }}</el-tag> {{ f.detail }}
                </div>
              </div>
              <div v-if="report.warn?.length" class="mb-8">
                <div class="rep-title txt-warn">warn（{{ report.warn.length }}）— 每条需备注处置理由才能确认</div>
                <div v-for="(w, i) in report.warn" :key="'w' + i" class="rep-line warn-line">
                  <div><el-tag size="small" type="warning" effect="plain">{{ w.rule }}</el-tag> {{ w.detail }}</div>
                  <el-input
                    v-model="warnResolutions[i]"
                    size="small"
                    placeholder="处置理由（如：已与业务确认该列语义，放行）"
                    :disabled="!canConfirm"
                  />
                </div>
              </div>
              <div v-if="!report.fail?.length && !report.warn?.length" class="txt-ok">✓ 全部规则通过</div>
            </div>
          </el-card>

          <!-- ⑥ 字段语义映射（AI 建议明细） -->
          <el-card shadow="never" class="mb-12">
            <template #header>
              <span class="card-title">⑥ 字段语义映射（AI 建议，confidence &lt; 0.7 需重点核）</span>
            </template>
            <el-table :data="current.field_specs || []" size="small" max-height="280">
              <el-table-column prop="field_name" label="字段" min-width="110" />
              <el-table-column prop="name_cn" label="中文名" width="100" />
              <el-table-column prop="semantic_role" label="角色" width="86" />
              <el-table-column label="单位/聚合" width="90">
                <template #default="{ row }">{{ row.unit || row.agg_func || '-' }}</template>
              </el-table-column>
              <el-table-column label="置信" width="66">
                <template #default="{ row }">
                  <span :class="{ 'txt-warn': (row.confidence ?? 1) < 0.7 }">{{ row.confidence }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="evidence" label="判定依据" min-width="180" show-overflow-tooltip />
            </el-table>
            <div v-if="current.dedup_recommendation" class="mt-8 dim">
              <b>去重建议：</b>{{ current.dedup_recommendation.strategy }} ·
              {{ (current.dedup_recommendation.fields || []).join(', ') }} ·
              {{ current.dedup_recommendation.rationale }}
              <el-tag size="small" type="warning" effect="plain">需人工确认</el-tag>
            </div>
          </el-card>

          <!-- ⑦ 动作：确认 / 落地包 -->
          <el-card shadow="never">
            <template #header><span class="card-title">⑦ 确认与落地包</span></template>
            <div class="flex-center gap-8 mb-8">
              <el-input v-model="issueNo" size="small" style="width: 180px" placeholder="落地工单号，如 158" :disabled="!canConfirm" />
              <el-button type="success" size="small" :disabled="!canConfirm" :loading="confirming" @click="doConfirm">
                确认接入声明
              </el-button>
              <el-button size="small" :disabled="current.status !== 'confirmed'" :loading="applying" @click="doApply">
                生成落地包
              </el-button>
              <el-button v-if="current.apply_package" size="small" :icon="CopyDocument" @click="copyPackage">复制落地包 JSON</el-button>
              <el-button v-if="current.apply_package" size="small" :icon="Download" @click="downloadPackage">下载 .json</el-button>
            </div>
            <el-alert v-if="current.apply_package" type="warning" :closable="false" show-icon class="mb-8"
              title="落地包只是建议：本页不会建表、不会写 registry、不会改代码。请按 issue-workflow 人工执行。" />
            <el-input
              v-if="current.apply_package"
              :model-value="JSON.stringify(current.apply_package, null, 2)"
              type="textarea"
              :rows="14"
              readonly
            />
          </el-card>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Download, InfoFilled, Refresh } from '@element-plus/icons-vue'
import {
  applyDraft,
  confirmDraft,
  createDraft,
  getDraft,
  getRegistrySnapshot,
  listDrafts,
  updateDraft,
} from '@/api/aiOnboarding'

const composerOpen = ref(true)
const submitting = ref(false)
const loadingList = ref(false)
const confirming = ref(false)
const applying = ref(false)
const drafts = ref([])
const total = ref(0)
const filterStatus = ref('')
const current = ref(null)
const snapshot = ref({})
const warnResolutions = ref({})
const issueNo = ref('')
const edit = ref({})

const form = ref({ samplesText: '', tableHint: '', businessNote: '' })

const report = computed(() => current.value?.validator_report || null)
const editable = computed(() => ['draft', 'failed'].includes(current.value?.status))
const canConfirm = computed(() => current.value?.status === 'draft')

function failCount(row) {
  return (row?.validator_report?.fail || []).length
}
function warnCount(row) {
  return (row?.validator_report?.warn || []).length
}

const STATUS_TEXT = {
  drafting: '起草中',
  draft: '待确认',
  failed: '起草失败',
  confirmed: '已确认',
  applied: '已出落地包',
  rejected: '已拒绝',
}
const STATUS_TYPE = {
  drafting: 'info',
  draft: 'primary',
  failed: 'danger',
  confirmed: 'success',
  applied: 'success',
  rejected: 'info',
}
function statusText(s) {
  return STATUS_TEXT[s] || s
}
function statusType(s) {
  return STATUS_TYPE[s] || 'info'
}

// 样例解析：支持 JSON 数组，或从 Excel 直接复制的 CSV/TSV（首行表头）
function parseSamples(text) {
  const t = (text || '').trim()
  if (!t) return null
  if (t.startsWith('[')) {
    const arr = JSON.parse(t)
    if (!Array.isArray(arr)) throw new Error('JSON 必须是对象数组')
    return arr
  }
  const lines = t.split(/\r?\n/).filter((l) => l.trim())
  if (lines.length < 2) throw new Error('至少需要表头行 + 一行数据')
  const delim = lines[0].includes('\t') ? '\t' : ','
  const header = lines[0].split(delim).map((h) => h.trim())
  return lines.slice(1).map((line) => {
    const cells = line.split(delim)
    const row = {}
    header.forEach((h, i) => {
      const v = (cells[i] ?? '').trim()
      // 纯数字转 number（金额语义推断需要数字特征），否则留字符串
      row[h] = v !== '' && !Number.isNaN(Number(v)) ? Number(v) : v
    })
    return row
  })
}

async function submitDraft() {
  let samples
  try {
    samples = parseSamples(form.value.samplesText)
  } catch (e) {
    ElMessage.error(`样例解析失败：${e.message}`)
    return
  }
  if (!samples || !samples.length) {
    ElMessage.warning('请先粘贴样例数据')
    return
  }
  if (samples.length > 50) {
    ElMessage.warning('样例最多 50 行（超出部分后端会拒绝）')
    return
  }
  submitting.value = true
  try {
    const res = await createDraft({
      samples,
      table_hint: form.value.tableHint || null,
      business_note: form.value.businessNote || null,
    })
    const id = res.data.draft_id
    ElMessage.success('已提交，AI 起草中…')
    await loadDrafts()
    await openDraft(id)
    pollDraft(id)
  } finally {
    submitting.value = false
  }
}

// 轮询起草状态（drafting → draft/failed），最多 90 次 × 2s
function pollDraft(id) {
  let n = 0
  const timer = setInterval(async () => {
    n += 1
    const res = await getDraft(id)
    const data = res.data
    if (data.status !== 'drafting' || n > 90) {
      clearInterval(timer)
      await loadDrafts()
      if (current.value?.id === id) applyDetail(data)
      if (data.status === 'draft') ElMessage.success('起草完成，请核对校验报告')
      if (data.status === 'failed') ElMessage.error(`起草失败：${data.error_message || '未知原因'}`)
    }
  }, 2000)
}

async function loadDrafts() {
  loadingList.value = true
  try {
    const params = { page: 1, page_size: 100 }
    if (filterStatus.value) params.status = filterStatus.value
    const res = await listDrafts(params)
    drafts.value = res.data.items
    total.value = res.data.total
  } finally {
    loadingList.value = false
  }
}

function applyDetail(data) {
  current.value = data
  edit.value = {
    code: data.code,
    name_cn: data.name_cn,
    table_name: data.table_name,
    category: data.category,
    time_field: data.time_field,
    sn_field: data.sn_field,
    pk_field: data.pk_field,
    pk_strategy: data.pk_strategy,
    description: data.description,
  }
  // 已填过的处置理由保留（按 rule+detail 做键，避免重跑校验后丢失）
  const kept = warnResolutions.value
  const next = {}
  ;(data.validator_report?.warn || []).forEach((w, i) => {
    const key = `${w.rule}|${w.detail}`
    next[i] = kept[key] ?? kept[i] ?? ''
  })
  warnResolutions.value = next
  issueNo.value = data.issue_no || ''
}

async function selectDraft(row) {
  if (!row) return
  await openDraft(row.id)
}

async function openDraft(id) {
  const res = await getDraft(id)
  applyDetail(res.data)
}

async function saveSpec() {
  const res = await updateDraft(current.value.id, edit.value)
  applyDetail(res.data)
  await loadDrafts()
  const f = failCount(res.data)
  ElMessage[f ? 'warning' : 'success'](f ? `已保存，仍有 ${f} 条 fail 需修正` : '已保存，校验通过')
}

async function doConfirm() {
  const warns = report.value?.warn || []
  const missing = warns.filter((_, i) => !(warnResolutions.value[i] || '').trim()).length
  if (missing) {
    ElMessage.warning(`还有 ${missing} 条 warn 未备注处置理由`)
    return
  }
  await ElMessageBox.confirm(
    `确认后该声明冻结（不可再编辑），并按工单 ${issueNo.value || '（未填）'} 进入落地流程。是否确认？`,
    '确认接入声明',
    { type: 'warning' },
  )
  confirming.value = true
  try {
    const res = await confirmDraft(current.value.id, {
      issue_no: issueNo.value || null,
      warn_resolutions: warnResolutions.value,
    })
    applyDetail(res.data)
    await loadDrafts()
    ElMessage.success('已确认，可生成落地包')
  } finally {
    confirming.value = false
  }
}

async function doApply() {
  applying.value = true
  try {
    const res = await applyDraft(current.value.id)
    await openDraft(current.value.id)
    await loadDrafts()
    ElMessage.success('落地包已生成（未写库、未改码，请人工按工单执行）')
    return res.data
  } finally {
    applying.value = false
  }
}

async function copyPackage() {
  await navigator.clipboard.writeText(JSON.stringify(current.value.apply_package, null, 2))
  ElMessage.success('已复制')
}

function downloadPackage() {
  const blob = new Blob([JSON.stringify(current.value.apply_package, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `onboarding-${current.value.code || current.value.id}.json`
  a.click()
  URL.revokeObjectURL(a.href)
}

async function reloadAll() {
  await loadDrafts()
  if (current.value) await openDraft(current.value.id)
}

onMounted(async () => {
  const res = await getRegistrySnapshot()
  snapshot.value = res.data
  await loadDrafts()
})
</script>

<style scoped>
.flex-center { display: flex; align-items: center; }
.gap-8 { gap: 8px; }
.mt-8 { margin-top: 8px; }
.mb-8 { margin-bottom: 8px; }
.mb-12 { margin-bottom: 12px; }
.card-title { font-weight: 600; font-size: 13px; }
.strong { font-weight: 600; }
.dim { color: #909399; font-size: 12px; }
.hint { margin-left: 10px; color: #909399; font-size: 12px; }
.txt-fail { color: #f56c6c; }
.txt-warn { color: #e6a23c; }
.txt-ok { color: #67c23a; }
.rep-title { font-weight: 600; font-size: 12px; margin-bottom: 4px; }
.rep-line { font-size: 12px; line-height: 1.6; }
.warn-line { margin-bottom: 6px; }
.rationale p { margin: 4px 0; font-size: 12px; line-height: 1.6; }
.rationale ul { margin: 4px 0 4px 16px; padding: 0; }
.rationale li { font-size: 12px; line-height: 1.6; }
</style>
