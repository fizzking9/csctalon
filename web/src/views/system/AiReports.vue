<!--
  ┌──────────────── 设计说明（Design Rationale） ────────────────────────────────┐
  │ 工单：#141（独立成页）+ #142（按模板折叠 + 预生成报告池定位）                │
  │ 性质：REQ（具体功能/集成）— 非元需求，不进 AGENTS.md 引用链                  │
  │ 范围：AI 系统预生成报告池（admin 全可见）+ 按模板折叠展示 + 报告内容查看      │
  │ 目的（替谁/答何决策）：admin 一站式查看「AI 系统预生成报告池」——              │
  │       3 个模板各生成了多少条？哪条最新？累计命中多少次？LLM 耗时健康吗？       │
  │       报告内容是什么？是否需要清理？                                          │
  │ 逻辑（为何这样组织）：                                                       │
  │   - 按 template_id 分组折叠展示，因预生成报告天然按模板分批(日/周/月报)；     │
  │     扁平列表会让 admin 翻页找特定模板的最新报告, 折叠后一屏内可见各模板概况。 │
  │   - template_id IS NULL 的实时问答报告归入「实时问答」分组排最后(非预生成,    │
  │     不属于"AI 系统报告池", 仅 admin 排查时 include_private=true 才显示)。     │
  │   - 每个 collapse-item 头部展示该模板的 KPI(报告数/累计命中/最近 LLM 耗时/      │
  │     最近生成时间)，让 admin 一眼看出各模板健康度，不需展开每个折叠。           │
  │ 口径：                                                                       │
  │   - 后端 /admin/ai/reports 默认 WHERE is_public=TRUE (仅预生成报告池);         │
  │     include_private=true 才含他人私有实时问答(admin 排查用)。                  │
  │   - 与「我的待办 /agenda AI 报告历史 Tab」分工:                                │
  │       • 我的待办 → 我自己用 AI 的轨迹 (走 /api/v1/ai/reports, 仅 user_id=本人) │
  │       • AI 与分析 → AI 系统预生成报告池 (走 /admin/ai/reports, is_public=TRUE) │
  │   - 两入口对 admin 不再重复(#141 前两边都看全部 = 重复)。                     │
  │ 取舍（不做什么及原因）：
  │   ❌ 不做新建/触发预生成 → 在 AI 管理 → 模板管理 Tab(单一真身)                │
  │   ❌ 不做报告编辑 → LLM 落库快照不应人为修改(破坏可审计性)                    │
  │   ❌ 不做 user_id 隔离 → admin 全可见, 与 #125 list_admin_reports 一致        │
  │   ❌ 不缓存详情接口 → LLM answer_md 几 KB, 每次按需拉取即可                  │
  │   ❌ 不做按模板筛选的下拉 → 折叠已分组, 直接展开某模板即可, 再加筛选冗余       │
  │ 设计文档：本文件头部（代码层）+ ⓘ 气泡（页面层）+                             │
  │           docs/standards/review-checklist.md（审查层）                        │
  └──────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="ai-reports">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">AI 报告历史</div>
        <div class="page-sub">AI 系统预生成报告池 — 按模板折叠展示</div>
      </div>
      <el-popover placement="bottom-end" :width="500" trigger="click">
        <template #reference>
          <el-button size="small" :icon="InfoFilled" circle />
        </template>
        <div class="rationale">
          <p><b>替谁：</b>admin。查看 AI 系统的预生成报告池(3 个模板各生成了多少条 / 哪条最新 / 累计命中多少次 / LLM 耗时健康吗 / 内容是什么)。</p>
          <p><b>答何决策：</b>① 各模板生成节奏是否正常？② 哪条预生成报告被命中复用最多？③ LLM 耗时是否健康？④ 是否需要清理旧报告？</p>
          <p><b>怎么用：</b>按模板折叠分组, 每组头部展示该模板 KPI(报告数/累计命中/最近 LLM 耗时/最近生成时间)。展开后看该模板的报告列表, 点「查看」弹抽屉看完整 markdown。</p>
          <p><b>口径：</b>默认仅预生成报告池(is_public=TRUE); 勾选「含私有实时问答」可看他人私有报告(admin 排查用)。与「我的待办·AI 报告历史」分工: 那边是我自己用 AI 的轨迹(仅 user_id=本人), 这边是 AI 系统报告池(全部 is_public=TRUE)。</p>
          <p><b>不做什么：</b></p>
          <ul>
            <li>❌ 不做新建/触发(在 AI 管理 → 模板管理 Tab)；</li>
            <li>❌ 不做报告编辑(LLM 落库快照不应人为修改)；</li>
            <li>❌ 不做 user_id 隔离(admin 全可见, 与 #125 一致)；</li>
            <li>❌ 不做按模板筛选下拉(折叠已分组, 直接展开某模板即可)。</li>
          </ul>
          <p><b>设计文档：</b>本文件头部注释块（代码层）+ 本 ⓘ 气泡（页面层）+ docs/standards/review-checklist.md（审查层）。</p>
          <p><b>工单：</b>#141 + #142</p>
        </div>
      </el-popover>
    </div>

    <div class="flex-between mb-16">
      <el-checkbox v-model="filter.include_private" @change="onFilter">含私有实时问答(他人)</el-checkbox>
      <el-button size="small" :icon="Refresh" @click="loadReports">刷新</el-button>
    </div>

    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        看什么：每个折叠 = 一个模板的预生成报告池(按 created_at DESC)。头部 KPI 一眼看健康度(报告数/累计命中/最近 LLM 耗时)。展开看具体报告, 点「查看」看完整 markdown。
      </template>
    </el-alert>

    <el-card v-loading="reportLoading">
      <el-collapse v-if="groupedReports.length" v-model="activeNames">
        <el-collapse-item
          v-for="grp in groupedReports"
          :key="grp.key"
          :name="grp.key"
        >
          <template #title>
            <div class="grp-header">
              <span class="grp-title">{{ grp.title }}</span>
              <el-tag v-if="grp.templateId === null" type="info" size="small" effect="plain">实时问答</el-tag>
              <el-tag v-else-if="grp.template" size="small" effect="plain">{{ grp.template.period || '—' }}</el-tag>
              <span class="grp-meta">
                {{ grp.items.length }} 条 · 累计命中 {{ grp.totalMatched }} · 最近 {{ fmt(grp.lastAt) }}
              </span>
            </div>
          </template>
          <el-table :data="grp.items" border size="small">
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column label="问句/快照日期" min-width="240">
              <template #default="{ row }">
                <div class="ellipsis">{{ row.question || '—' }}</div>
                <div class="dim">{{ row.snapshot_date || '—' }} · {{ row.snapshot_period || row.period || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="可见性" width="100">
              <template #default="{ row }">
                <el-tag :type="row.is_public ? 'success' : 'info'" size="small" effect="plain">
                  {{ row.is_public ? 'public' : 'admin_only' }}
                </el-tag>
                <div class="dim">命中 {{ row.matched_count ?? 0 }}</div>
              </template>
            </el-table-column>
            <el-table-column label="模型" width="160">
              <template #default="{ row }">
                <div class="ellipsis">{{ row.llm_model || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="120">
              <template #default="{ row }">
                <div>LLM {{ row.llm_ms ?? '—' }}ms</div>
                <div class="dim">总 {{ row.total_ms ?? '—' }}ms</div>
              </template>
            </el-table-column>
            <el-table-column prop="total_tokens" label="tokens" width="90" />
            <el-table-column label="生成时间" width="160">
              <template #default="{ row }">{{ fmt(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="viewReport(row)">查看</el-button>
                <el-popconfirm title="删除该报告? 不可恢复" @confirm="removeReport(row)">
                  <template #reference>
                    <el-button link type="danger" size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-else description="暂无预生成报告(勾选「含私有实时问答」可看他人私有报告)" :image-size="80" />
      <div v-if="reports.total > filter.page_size" style="margin-top: 12px; text-align: right">
        <el-pagination
          v-model:current-page="reportPage"
          :page-size="filter.page_size"
          :total="reports.total"
          layout="total, prev, pager, next"
          @current-change="loadReports"
        />
      </div>
    </el-card>

    <!-- ═══ 报告查看抽屉(完整 answer_md + 元信息) ═══ -->
    <el-drawer v-model="drawerVisible" size="60%" :title="`报告 #${currentReport?.id ?? ''} 详情`">
      <div v-if="reportDetail" class="report-detail">
        <el-descriptions :column="2" border size="small" class="mb-16">
          <el-descriptions-item label="模板">{{ templateName(reportDetail.template_id) }}</el-descriptions-item>
          <el-descriptions-item label="快照日期">{{ reportDetail.snapshot_date || '—' }}</el-descriptions-item>
          <el-descriptions-item label="周期">{{ reportDetail.snapshot_period || reportDetail.period || '—' }}</el-descriptions-item>
          <el-descriptions-item label="可见性">
            <el-tag :type="reportDetail.is_public ? 'success' : 'info'" size="small" effect="plain">
              {{ reportDetail.is_public ? 'public' : 'admin_only' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="命中次数">{{ reportDetail.matched_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="模型">{{ reportDetail.llm_model || '—' }}</el-descriptions-item>
          <el-descriptions-item label="LLM 耗时">{{ reportDetail.llm_ms ?? '—' }} ms</el-descriptions-item>
          <el-descriptions-item label="总耗时">{{ reportDetail.total_ms ?? '—' }} ms</el-descriptions-item>
          <el-descriptions-item label="tokens">{{ reportDetail.total_tokens ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="生成时间">{{ fmt(reportDetail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="问句" :span="2">
            <div class="ellipsis-3">{{ reportDetail.question || '—' }}</div>
          </el-descriptions-item>
        </el-descriptions>
        <!-- ═══ 报告评价（采集, 周期汇入评估中心）═══ -->
        <el-divider content-position="left">评价这份报告（用于周期评估归因）</el-divider>
        <div v-if="reportDetail" class="feedback-box">
          <el-rate v-model="feedback.rating" :max="5" allow-half show-score
                   :texts="['未解决/没用','不满意','一般','满意','很满意/很准']" score-template="{value}" />
          <el-input v-model="feedback.opinion" type="textarea" :rows="2" maxlength="200" show-word-limit
                    placeholder="可选：哪里不对/缺什么/建议怎么改（只采集，不实时分析）" class="mt-8" />
          <div class="flex-between mt-8">
            <span class="dim">已收录 {{ feedbackList.length }} 条评价</span>
            <el-button size="small" type="primary" :disabled="!feedback.rating"
                       :loading="feedbackSubmitting" @click="submitFeedback">提交评价</el-button>
          </div>
        </div>
        <div v-if="feedbackList.length" class="feedback-history">
          <div v-for="f in feedbackList" :key="f.id" class="feedback-item">
            <div class="feedback-item-head">
              <el-rate :model-value="f.rating" disabled :max="5" show-score score-template="{value}" />
              <span class="dim">{{ fmt(f.created_at) }}</span>
            </div>
            <div v-if="f.opinion" class="feedback-item-text">{{ f.opinion }}</div>
          </div>
        </div>
        <div class="report-content">
          <div class="dim mb-8">▼ 报告内容 (markdown 渲染)</div>
          <!-- answer_md 是 LLM 生成文本, 非 user-generated, v-html 渲染 markdown 安全 -->
          <div v-html="renderedContent" class="markdown-body"></div>
        </div>
      </div>
      <div v-else v-loading="detailLoading" style="min-height: 200px"></div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, InfoFilled } from '@element-plus/icons-vue'
import { marked } from 'marked'
import { aiTemplates, aiAdminReports, getAiReport, deleteAiReport, postReportFeedback, getReportFeedbacks } from '@/api/adminAi'

const reportLoading = ref(false)
const reports = ref({ items: [], total: 0 })
const reportPage = ref(1)
// #142: include_private 默认 false, 仅看预生成报告池
const filter = reactive({ page_size: 100, include_private: false })
const templates = ref([])
const activeNames = ref([])  // 默认全部折叠, 用户主动展开

// 报告详情抽屉
const drawerVisible = ref(false)
const currentReport = ref(null)
const reportDetail = ref(null)
const detailLoading = ref(false)

// 报告评价（采集）
const feedback = ref({ rating: 0, opinion: '' })
const feedbackList = ref([])
const feedbackSubmitting = ref(false)

const renderedContent = computed(() => {
  const raw = reportDetail.value?.answer_md || ''
  if (!raw) return '<p class="dim">（无内容）</p>'
  try {
    return marked.parse(raw)
  } catch {
    return `<pre>${raw}</pre>`
  }
})

// #142: 按 template_id 分组(NULL 归「实时问答」排最后)
const groupedReports = computed(() => {
  const items = reports.value.items || []
  const groups = new Map()  // key=templateId|'null', value={templateId, template, items}
  for (const r of items) {
    const tid = r.template_id
    const key = tid == null ? '__null__' : String(tid)
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        templateId: tid,
        template: templates.value.find(t => t.id === tid) || null,
        items: [],
      })
    }
    groups.get(key).items.push(r)
  }
  // 排序: 有 template_id 的按 template.priority 升序, NULL 排最后
  const arr = Array.from(groups.values())
  arr.sort((a, b) => {
    if (a.templateId == null) return 1
    if (b.templateId == null) return -1
    const pa = a.template?.priority ?? 99
    const pb = b.template?.priority ?? 99
    return pa - pb
  })
  // 加 KPI 字段
  return arr.map(g => {
    const totalMatched = g.items.reduce((s, r) => s + (r.matched_count || 0), 0)
    const lastAt = g.items[0]?.created_at  // 已按 created_at DESC 排序, 第一条最新
    const title = g.templateId == null
      ? '实时问答(非预生成)'
      : (g.template?.name_cn || `模板 #${g.templateId}`)
    return { ...g, title, totalMatched, lastAt }
  })
})

async function loadTemplates() {
  try {
    const d = await aiTemplates()
    templates.value = d.data || []
  } catch { /* 拦截器已提示 */ }
}

function templateName(tid) {
  if (!tid) return '—'
  const t = templates.value.find(x => x.id === tid)
  return t ? t.name_cn : `#${tid}`
}

async function loadReports() {
  reportLoading.value = true
  try {
    const d = await aiAdminReports({
      page: reportPage.value,
      page_size: filter.page_size,
      include_private: filter.include_private,
    })
    reports.value = d.data || { items: [], total: 0 }
  } catch { /* 拦截器已提示 */ } finally {
    reportLoading.value = false
  }
}

function onFilter() {
  reportPage.value = 1
  loadReports()
}

async function viewReport(row) {
  currentReport.value = row
  drawerVisible.value = true
  reportDetail.value = null
  detailLoading.value = true
  feedback.value = { rating: 0, opinion: '' }
  feedbackList.value = []
  try {
    const d = await getAiReport(row.id)
    reportDetail.value = d.data
  } catch { /* 拦截器已提示 */ } finally {
    detailLoading.value = false
  }
  try {
    const f = await getReportFeedbacks(row.id)
    feedbackList.value = f.data?.items || []
  } catch { /* 拦截器已提示 */ }
}

async function submitFeedback() {
  if (!feedback.value.rating) return
  feedbackSubmitting.value = true
  try {
    await postReportFeedback(currentReport.value.id, {
      rating: feedback.value.rating,
      opinion: feedback.value.opinion?.trim() || undefined,
    })
    feedback.value = { rating: 0, opinion: '' }
    const f = await getReportFeedbacks(currentReport.value.id)
    feedbackList.value = f.data?.items || []
    ElMessage.success('评价已记录，将纳入下次周期评估汇总')
  } catch { /* 拦截器已提示 */ } finally {
    feedbackSubmitting.value = false
  }
}

async function removeReport(row) {
  try {
    await deleteAiReport(row.id)
    ElMessage.success('已删除')
    loadReports()
  } catch { /* 拦截器已提示 */ }
}

function fmt(dt) {
  if (!dt) return '—'
  try {
    const d = new Date(dt)
    if (isNaN(d.getTime())) return dt
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return dt }
}

onMounted(() => {
  loadTemplates()
  loadReports()
})
</script>

<style scoped>
.ai-reports {
  padding: 16px;
}
.dim { color: #909399; font-size: 12px; }
.feedback-box { border: 1px solid #ebeef5; border-radius: 6px; padding: 12px; background: #fafafa; }
.feedback-history { margin-top: 12px; }
.feedback-item { border: 1px solid #ebeef5; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; }
.feedback-item-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.feedback-item-text { font-size: 13px; line-height: 1.5; margin-top: 6px; }
.ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ellipsis-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-clamp: 3;
}
.mb-8 { margin-bottom: 8px; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.rationale p { margin: 6px 0; line-height: 1.6; }
.rationale ul { margin: 6px 0 6px 18px; padding: 0; }
.rationale li { margin: 2px 0; line-height: 1.6; }
.grp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.grp-title {
  font-weight: 600;
  font-size: 14px;
}
.grp-meta {
  color: #909399;
  font-size: 12px;
  margin-left: auto;
}
.markdown-body {
  padding: 12px;
  background: #fafafa;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  line-height: 1.7;
  font-size: 14px;
}
.markdown-body :deep(h1) { font-size: 20px; margin: 16px 0 8px; }
.markdown-body :deep(h2) { font-size: 17px; margin: 14px 0 6px; border-bottom: 1px solid #eee; padding-bottom: 4px; }
.markdown-body :deep(h3) { font-size: 15px; margin: 12px 0 4px; }
.markdown-body :deep(p) { margin: 8px 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { margin: 8px 0 8px 20px; }
.markdown-body :deep(code) { background: #f4f4f4; padding: 2px 4px; border-radius: 3px; font-size: 13px; }
.markdown-body :deep(table) { border-collapse: collapse; margin: 8px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) { border: 1px solid #ddd; padding: 4px 8px; }
</style>
