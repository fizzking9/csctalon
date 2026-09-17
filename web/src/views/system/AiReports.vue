<!--
  ┌──────────────── 设计说明（Design Rationale） ────────────────────────────────┐
  │ 工单：#141（AI 报告历史独立成页 + 报告内容查看入口）                          │
  │ 性质：REQ（具体功能/集成）— 非元需求，不进 AGENTS.md 引用链                  │
  │ 范围：报告列表 + 报告内容查看抽屉                                             │
  │ 目的（替谁/答何决策）：admin 一站式查看 AI 报告落库快照——                    │
  │       今天哪些报告生成了？内容是什么？模板/模型/耗时多少？是否被命中复用？    │
  │ 逻辑（为何这样组织）：列表按 created_at DESC，列含模板/问句/快照日期/可见性/   │
  │       模型/耗时/tokens/生成时间，操作列「查看」+「删除」。点查看弹抽屉显示     │
  │       完整 content(markdown 渲染) + 元信息侧栏。                              │
  │ 口径：复用 /admin/ai/reports 列表 + /admin/ai/reports/{id} 详情(#141 新增),   │
  │       列表只返摘要(content 不返防过大), 详情按需拉取。                          │
  │ 取舍（不做什么及原因）：
  │   ❌ 不做新建/触发预生成——这些操作放在 AI 管理 → 模板管理 Tab(单一真身)        │
  │   ❌ 不做报告编辑——AI 报告是 LLM 落库快照, 不应人为修改(修改即破坏可审计性)   │
  │   ❌ 不做 user_id 隔离——admin 全可见, 与原 #125 list_admin_reports 一致       │
  │   ❌ 不缓存详情接口——LLM content 几 KB, 每次按需拉取即可, Redis 不为单条缓存   │
  │ 设计文档：本文件头部（代码层）+ ⓘ 气泡（页面层）+                             │
  │           docs/standards/review-checklist.md（审查层）                        │
  └──────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="ai-reports">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">AI 报告历史</div>
        <div class="page-sub">按 created_at DESC 排序；admin 全可见，不做 user_id 隔离</div>
      </div>
      <el-popover placement="bottom-end" :width="460" trigger="click">
        <template #reference>
          <el-button size="small" :icon="InfoFilled" circle />
        </template>
        <div class="rationale">
          <p><b>替谁：</b>admin。需要查看 AI 报告落库内容(预生成/实时问答的快照)。</p>
          <p><b>答何决策：</b>① 今天哪些报告生成了？② 报告内容是什么？③ 哪条被命中复用了(matched_count)？④ 模板/模型/耗时是否健康？</p>
          <p><b>怎么用：</b>列表按生成时间倒序；点「查看」弹抽屉看完整 content (markdown 渲染) + 元信息。</p>
          <p><b>不做什么：</b></p>
          <ul>
            <li>❌ 不做新建/触发(在 AI 管理 → 模板管理 Tab)；</li>
            <li>❌ 不做报告编辑(LLM 落库快照不应人为修改)；</li>
            <li>❌ 不做 user_id 隔离(admin 全可见, 与 #125 一致)。</li>
          </ul>
          <p><b>设计文档：</b>本文件头部注释块（代码层）+ 本 ⓘ 气泡（页面层）+ docs/standards/review-checklist.md（审查层）。</p>
          <p><b>工单：</b>#141</p>
        </div>
      </el-popover>
    </div>

    <div class="flex-between mb-16">
      <div>
        <el-select v-model="filter.is_public" placeholder="可见性" size="small" clearable style="width: 120px; margin-right: 8px" @change="onFilter">
          <el-option label="全部" :value="null" />
          <el-option label="公开(public)" :value="true" />
          <el-option label="私有(admin_only)" :value="false" />
        </el-select>
        <el-select v-model="filter.template_id" placeholder="模板" size="small" clearable filterable style="width: 200px" @change="onFilter">
          <el-option v-for="t in templates" :key="t.id" :label="t.name_cn" :value="t.id" />
        </el-select>
      </div>
      <el-button size="small" :icon="Refresh" @click="loadReports">刷新</el-button>
    </div>

    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        看什么：每行 = 一次预生成/实时问答的落库快照。<code>matched_count</code> = 命中次数（用户问句命中本报告）；
        <code>llm_ms</code>/<code>total_ms</code> = LLM 耗时/总耗时。空 = 无该字段。点「查看」看完整内容。
      </template>
    </el-alert>

    <el-card v-loading="reportLoading">
      <el-table :data="reports.items" border size="small">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column label="模板" min-width="160">
          <template #default="{ row }">
            <div>{{ templateName(row.template_id) }}</div>
            <div class="dim">tid={{ row.template_id ?? '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="问句/快照日期" min-width="180">
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
        <el-table-column label="模型" width="140">
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
      <div style="margin-top: 12px; text-align: right">
        <el-pagination
          v-model:current-page="reportPage"
          :page-size="filter.page_size"
          :total="reports.total"
          layout="total, prev, pager, next"
          @current-change="loadReports"
        />
      </div>
    </el-card>

    <!-- ═══ 报告查看抽屉(完整 content + 元信息) ═══ -->
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
        <div class="report-content">
          <div class="dim mb-8">▼ 报告内容 (markdown 渲染)</div>
          <!-- content 是 LLM 生成文本, 非 user-generated, v-html 渲染 markdown 安全 -->
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
import { aiTemplates, aiAdminReports, getAiReport, deleteAiReport } from '@/api/adminAi'

const reportLoading = ref(false)
const reports = ref({ items: [], total: 0 })
const reportPage = ref(1)
const filter = reactive({ page_size: 20, is_public: null, template_id: null })
const templates = ref([])

// 报告详情抽屉
const drawerVisible = ref(false)
const currentReport = ref(null)
const reportDetail = ref(null)
const detailLoading = ref(false)

// #141: marked 同步渲染, 简单 markdown → HTML; content 是 LLM 生成文本, 非 user input
const renderedContent = computed(() => {
  const raw = reportDetail.value?.content || ''
  if (!raw) return '<p class="dim">（无内容）</p>'
  try {
    return marked.parse(raw)
  } catch {
    return `<pre>${raw}</pre>`
  }
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
      is_public: filter.is_public,
      template_id: filter.template_id,
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
  try {
    const d = await getAiReport(row.id)
    reportDetail.value = d.data
  } catch { /* 拦截器已提示 */ } finally {
    detailLoading.value = false
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
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
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
.ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ellipsis-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.mb-8 { margin-bottom: 8px; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.rationale p { margin: 6px 0; line-height: 1.6; }
.rationale ul { margin: 6px 0 6px 18px; padding: 0; }
.rationale li { margin: 2px 0; line-height: 1.6; }
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
