<!--
  ┌──────────────── 设计说明（Design Rationale） ────────────────────────────────┐
  │ 工单：#173（AI 提示词评估中心: 规则级+提示词段评估 + 半自动迭代）              │
  │ 性质：REQ（具体功能/集成）— 非元需求，不进 AGENTS.md 引用链                    │
  │ 范围：本页 = 「AI 与分析」分组下的**评估侧**入口                               │
  │ 目的（替谁/答何决策）：admin 回答「AI 报告/提示词的命中链路健不健康？           │
  │       哪里在漏？要不要迭代、迭代成什么？」——把 #153 只落事务通知中心的           │
  │       健康报告升级为决策可见页面，并给出可执行路径。                            │
  │ 逻辑（为何这样组织）：四区块按「先看总览 → 再看模板 → 再查跨链路 →             │
  │       再查规则」的排查顺序；问题优先（命中率最低排最上）；迭代建议走抽屉        │
  │       与各区块并列，避免淹没在数字里。                                         │
  │ 口径：窗口近 N 天北京时间(created_at AT TIME ZONE 'Asia/Shanghai')；            │
  │       命中率=(full+section)/本人私有问答数；模板命中率=窗口内 matched_count     │
  │       合计/快照数(快照级近似, 页面注明)；fp_ratio=误报/(执行+驳回+误报)。       │
  │ 取舍（不做什么及原因）：                                                        │
  │   ❌ 不自动改 _SYSTEM_PROMPT/_EXTRACT_SYSTEM 代码常量（提示词铁律）——           │
  │      锚点漂移只出落地指引(anchor_guide)，不提供写入按钮                         │
  │   ❌ 不做全自动执行——所有写入必须人工点「确认应用」(半自动的「半」字)           │
  │   ❌ 不做治理研判提示词(governance_prompts)迭代入口——那是研判链路, 本期不混入   │
  │   ❌ 新模板(new_template)只记建议不自动建——建模板需人工补 required_dimensions  │
  │ 设计文档：本文件头部注释块(代码层) + ⓘ 气泡(页面层) +                           │
  │           docs/design/AI提示词评估中心-20260920.md + review-checklist(审查层)   │
  └──────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="prompt-eval">
    <!-- ═══ 头部：标题 + 窗口 + 刷新 + 生成建议 ═══ -->
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">提示词评估中心</div>
        <div class="page-sub">执行结果 → 评估分析 → 半自动迭代（规则级 + 提示词段 + 跨链路关联）</div>
      </div>
      <div class="head-tools">
        <el-select v-model="days" size="small" style="width: 90px" @change="loadAll">
          <el-option v-for="d in [1, 7, 30]" :key="d" :label="`近 ${d} 天`" :value="d" />
        </el-select>
        <el-button size="small" @click="loadAll">刷新</el-button>
        <el-button size="small" type="primary" :loading="suggesting" @click="genSuggestions">
          生成迭代建议
        </el-button>
        <el-popover placement="bottom-end" :width="460" trigger="click">
          <template #reference>
            <el-button size="small" :icon="InfoFilled" circle />
          </template>
          <div class="rationale">
            <p><b>替谁：</b>admin（管理员）。AI 报告/提示词链路的「评估侧」。</p>
            <p><b>答何决策：</b>① 命中链路健不健康？② 哪里在漏（不命中归因）？③ 要不要迭代、迭代成什么？</p>
            <p><b>口径：</b>窗口=近 N 天北京时间（created_at AT TIME ZONE 'Asia/Shanghai'）；命中率=(full+section)/本人私有问答数；模板命中率=窗口内 matched_count 合计/快照数（快照级近似值）；fp_ratio=误报/(执行+驳回+误报)。「空」的含义：无问答/无快照 → 显示「-」并注明本期无数据。</p>
            <p><b>不做什么：</b></p>
            <ul>
              <li>❌ 不自动改 _SYSTEM_PROMPT/_EXTRACT_SYSTEM 代码常量（提示词铁律）——锚点漂移只出落地指引，无写入按钮；</li>
              <li>❌ 不做全自动执行——一切写入须人工点「确认应用」；</li>
              <li>❌ 不做治理研判提示词迭代入口（本期）；新模板只记建议不自动建。</li>
            </ul>
            <p><b>设计文档：</b>docs/design/AI提示词评估中心-20260920.md</p>
            <p><b>工单：</b>#173</p>
          </div>
        </el-popover>
      </div>
    </div>

    <!-- ═══ 区块1：总览（4 卡 + 高频兜底问句） ═══ -->
    <el-row :gutter="12" class="mb-12">
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">问答命中率</div>
          <div class="stat-value">{{ pct(summary?.overall?.hit_rate) }}</div>
          <div class="stat-sub">{{ summary?.overall?.questions ?? 0 }} 次问答（{{ modeText }}）</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">实时兜底占比</div>
          <div class="stat-value" :class="{ warn: summary?.overall?.live_ratio_warn }">
            {{ pct(summary?.overall?.live_ratio) }}
          </div>
          <div class="stat-sub">≥50% 提示词层告警</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">模板 0 命中</div>
          <div class="stat-value" :class="{ warn: (summary?.template_hits?.zero_hit_count ?? 0) > 0 }">
            {{ summary?.template_hits?.zero_hit_count ?? 0 }}
          </div>
          <div class="stat-sub">生成快照但从未被问句命中</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">规则健康预警</div>
          <div class="stat-value" :class="{ warn: (summary?.rule_health?.warn_count ?? 0) > 0 }">
            {{ summary?.rule_health?.warn_count ?? 0 }}
          </div>
          <div class="stat-sub">误报率 &gt; 50% 的规则数</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="flex-between">
          <span>高频未答好问题（重复走实时兜底）</span>
          <el-tag size="small" type="info">归因线索</el-tag>
        </div>
      </template>
      <el-table :data="summary?.top_live_qs ?? []" size="small" empty-text="本期无重复兜底问题">
        <el-table-column prop="question" label="问句" min-width="280" show-overflow-tooltip />
        <el-table-column label="窗口" width="200">
          <template #default="{ row }">
            {{ row.start && row.end ? `${row.start} ~ ${row.end}` : (row.start || row.end || '-') }}
          </template>
        </el-table-column>
        <el-table-column prop="count" label="次数" width="80" />
      </el-table>
    </el-card>

    <!-- ═══ 区块2：模板评估（命中率升序，问题在前） ═══ -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="flex-between">
          <span>模板评估（命中率升序 · 0 命中在最前）</span>
          <el-tag size="small" type="warning">规则级</el-tag>
        </div>
      </template>
      <el-table :data="templates" size="small" empty-text="暂无模板">
        <el-table-column prop="name_cn" label="模板" min-width="180" show-overflow-tooltip />
        <el-table-column prop="code" label="code" width="180" show-overflow-tooltip />
        <el-table-column prop="period" label="周期" width="70" />
        <el-table-column label="快照 / 命中" width="110">
          <template #default="{ row }">{{ row.snapshots }} / {{ row.matched }}</template>
        </el-table-column>
        <el-table-column label="命中率" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.hit_rate === null" size="small" type="info">本期未生成</el-tag>
            <el-tag v-else-if="row.hit_rate === 0" size="small" type="danger">0 命中</el-tag>
            <span v-else>{{ pct(row.hit_rate) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="缺失维度快照" width="100">
          <template #default="{ row }">{{ row.with_missing }}</template>
        </el-table-column>
        <el-table-column prop="question_keywords" label="关键词" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="!row.question_keywords?.length" class="muted">（无）</span>
            <span v-else>{{ row.question_keywords.join('、') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="60">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '是' : '停' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ═══ 区块3：跨链路锚点一致性 ═══ -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="flex-between">
          <span>跨链路锚点一致性（生成侧 ↔ 摘取侧 ↔ 报告实际）</span>
          <el-tag size="small" type="warning">提示词段</el-tag>
        </div>
      </template>
      <el-row :gutter="12">
        <el-col :span="8">
          <div class="anchor-block">
            <div class="anchor-title">静态比对</div>
            <el-tag :type="anchor?.static_match ? 'success' : 'danger'" size="small">
              {{ anchor?.static_match ? '锚点一致' : '锚点不一致（铁律漂移）' }}
            </el-tag>
            <div class="anchor-sub">生成侧(_SYSTEM_PROMPT 铁律3) vs 摘取侧(H2_ANCHORS)</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="anchor-block">
            <div class="anchor-title">实际报告漂移（近 N 天周/月报 {{ anchor?.reports_checked ?? 0 }} 份）</div>
            <template v-if="!anchor?.missing_anchors?.length">
              <el-tag type="success" size="small">约定锚点齐全</el-tag>
            </template>
            <div v-else>
              <div v-for="m in anchor.missing_anchors" :key="m.anchor" class="anchor-miss">
                「{{ m.anchor }}」缺 {{ m.missing_count }}/{{ m.total_reports }} 份
              </div>
            </div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="anchor-block">
            <div class="anchor-title">非约定 H2（摘取层不可识别）</div>
            <div v-if="!anchor?.non_anchor_h2?.length" class="anchor-sub">无</div>
            <div v-else>
              <div v-for="[h2, c] in anchor.non_anchor_h2" :key="h2" class="anchor-miss">{{ h2 }} × {{ c }}</div>
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- ═══ 区块4：治理规则健康度 ═══ -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="flex-between">
          <span>治理规则健康度（误报率 &gt; 50% 标红）</span>
          <el-tag size="small" type="warning">规则级</el-tag>
        </div>
      </template>
      <el-table :data="rules" size="small" empty-text="暂无治理规则">
        <el-table-column prop="target_pattern" label="目标" min-width="160" show-overflow-tooltip />
        <el-table-column prop="detector" label="探测器" width="140" />
        <el-table-column prop="verdict" label="判定" width="150" />
        <el-table-column prop="disposition" label="动作" width="140" />
        <el-table-column prop="hit_count" label="命中" width="70" />
        <el-table-column label="执行/驳回/误报" width="140">
          <template #default="{ row }">
            {{ row.effect_executed }} / {{ row.effect_rejected }} / {{ row.effect_fp }}
          </template>
        </el-table-column>
        <el-table-column label="误报率" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.fp_ratio === null" size="small" type="info">无效果</el-tag>
            <el-tag v-else :type="row.fp_warn ? 'danger' : 'success'" size="small">
              {{ pct(row.fp_ratio) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="60">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '是' : '停' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ═══ 区块5：迭代建议抽屉 ═══ -->
    <el-drawer v-model="drawer" title="迭代建议（半自动执行）" size="520px">
      <div class="drawer-sub">
        流程：生成建议(proposed) → 人工确认(confirmed) → 一键应用(applied)；不采纳 → rejected。
        <b>锚点指引与新建模板只记录，不提供应用按钮（提示词铁律 / 需人工补维度）。</b>
      </div>
      <div v-if="!iterations.length" class="drawer-empty">暂无迭代建议 — 点右上「生成迭代建议」从执行结果自动产出。</div>
      <el-card v-for="it in iterations" :key="it.id" shadow="never" class="mb-12 iter-card">
        <div class="flex-between">
          <el-tag size="small" :type="scopeType(it.scope)">{{ scopeLabel(it.scope) }}</el-tag>
          <el-tag size="small" :type="statusType(it.status)">{{ statusLabel(it.status) }}</el-tag>
        </div>
        <div class="iter-target">{{ it.target_name }}</div>
        <div class="iter-suggestion">{{ it.suggestion }}</div>
        <div v-if="it.reason" class="iter-reason">理由：{{ it.reason }}</div>
        <div v-if="it.old_value !== null && it.old_value !== undefined" class="iter-diff">
          <div class="diff-old">旧：{{ it.old_value || '（空）' }}</div>
          <div class="diff-new">新：{{ it.new_value || '（空）' }}</div>
        </div>
        <div v-if="it.result" class="iter-reason">结果：{{ it.result }}</div>
        <div v-if="it.status === 'proposed'" class="flex-between mt-8">
          <el-button size="small" type="primary" @click="onConfirm(it, true)">确认</el-button>
          <el-button size="small" @click="onConfirm(it, false)">不采纳</el-button>
        </div>
        <div v-if="it.status === 'confirmed'" class="flex-between mt-8">
          <el-button v-if="applicable(it.scope)" size="small" type="primary" @click="onApply(it)">应用</el-button>
          <el-tag v-else size="small" type="info">只记录建议（无写入路径）</el-tag>
        </div>
      </el-card>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref, computed } from 'vue'
import { InfoFilled } from '@element-plus/icons-vue'
import {
  getPromptEvalSummary, getPromptEvalTemplates, getAnchorCheck,
  getPromptEvalRules, createSuggestions, listIterations,
  confirmIteration, applyIteration,
} from '@/api/aiPromptEval'

const days = ref(7)
const summary = ref(null)
const templates = ref([])
const anchor = ref(null)
const rules = ref([])
const iterations = ref([])
const drawer = ref(false)
const suggesting = ref(false)

const modeText = computed(() => {
  const m = summary.value?.overall?.by_mode ?? {}
  return `命中 ${(m.full || 0) + (m.section || 0)} / 实时 ${m.live || 0} / 规则 ${m.rule || 0}`
})

function pct(v) {
  if (v === null || v === undefined) return '-'
  return `${(v * 100).toFixed(1)}%`
}

function scopeLabel(s) {
  return { template_keyword: '补关键词', template_question: '改问句', new_template: '新建模板',
    rule_adjust: '规则调整', anchor_guide: '锚点指引' }[s] || s
}
function scopeType(s) {
  return { template_keyword: '', template_question: '', new_template: 'warning',
    rule_adjust: 'warning', anchor_guide: 'info' }[s] || 'info'
}
function statusLabel(s) {
  return { proposed: '待确认', confirmed: '已确认', applied: '已应用', rejected: '不采纳' }[s] || s
}
function statusType(s) {
  return { proposed: 'warning', confirmed: 'primary', applied: 'success', rejected: 'info' }[s] || 'info'
}
// 只记录类建议（anchor_guide / new_template）不提供应用按钮
const RECORD_ONLY = ['anchor_guide', 'new_template']
function applicable(scope) {
  return !RECORD_ONLY.includes(scope)
}

async function loadAll() {
  const params = { days: days.value }
  const [s, t, a, r, its] = await Promise.all([
    getPromptEvalSummary({ params }),
    getPromptEvalTemplates({ params }),
    getAnchorCheck({ params }),
    getPromptEvalRules(),
    listIterations(),
  ])
  summary.value = s.data
  templates.value = t.data
  anchor.value = a.data
  rules.value = r.data
  iterations.value = its.data
}

async function genSuggestions() {
  suggesting.value = true
  try {
    const res = await createSuggestions({ days: days.value })
    iterations.value = res.data.items.concat(iterations.value)
  } finally {
    suggesting.value = false
  }
}

async function onConfirm(it, approved) {
  await confirmIteration(it.id, approved)
  await loadAll()
}

async function onApply(it) {
  await applyIteration(it.id)
  await loadAll()
}

onMounted(loadAll)
</script>

<style scoped>
.prompt-eval { padding: 16px; }
.head-tools { display: flex; gap: 8px; align-items: center; }
.mb-16 { margin-bottom: 16px; }
.mb-12 { margin-bottom: 12px; }
.mt-8 { margin-top: 8px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.stat-card { text-align: left; }
.stat-label { font-size: 12px; color: #909399; }
.stat-value { font-size: 26px; font-weight: 600; line-height: 1.4; }
.stat-value.warn { color: #dc2626; }
.stat-sub { font-size: 12px; color: #909399; }
.anchor-block { border: 1px solid #ebeef5; border-radius: 6px; padding: 10px; min-height: 90px; }
.anchor-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.anchor-sub { font-size: 12px; color: #909399; margin-top: 6px; }
.anchor-miss { font-size: 12px; color: #dc2626; margin-top: 4px; }
.muted { color: #909399; }
.drawer-sub { font-size: 12px; color: #909399; margin-bottom: 12px; }
.drawer-empty { color: #909399; text-align: center; padding: 40px 0; }
.iter-card { border: 1px solid #ebeef5; }
.iter-target { font-weight: 600; margin: 6px 0 4px; }
.iter-suggestion { font-size: 13px; line-height: 1.5; }
.iter-reason { font-size: 12px; color: #909399; margin-top: 4px; }
.iter-diff { margin-top: 6px; font-size: 12px; background: #f5f7fa; padding: 6px 8px; border-radius: 4px; }
.diff-old { color: #dc2626; }
.diff-new { color: #16a34a; }
</style>
