<!--
  ┌──────────────── 设计说明（Design Rationale） ────────────────────────────────┐
  │ 工单：#137（AI 管理页面 5 Tab 整合入口）                                       │
  │ 性质：REQ（具体功能/集成）— 非元需求，不进 AGENTS.md 引用链                    │
  │ 范围：本页 5 个 Tab 整合入口，各 Tab 内嵌存量独立组件（不改造存量组件本身）     │
  │ 目的（替谁/答何决策）：admin 一站式管理 AI 报告系统；答：今天哪些报告生成了？ │
  │       模板配置如何？模型节点是否健康？提示词是否更新？事务通知有无异常？       │
  │ 逻辑（为何这样组织）：按「日常使用频度」排序——报告列表最常用（每天看）→       │
  │       模板（每周改）→ 模型（每月调）→ 提示词（季度更新）→ 事务通知（异常时查）  │
  │ 口径：各 Tab 内嵌存量独立组件，数据口径同原页面（不重复定义）；Tab1 报告列表   │
  │       复用 /admin/ai/reports API（admin_ai.py L161）                           │
  │ 取舍（不做什么及原因）：
  │   ❌ 不删除旧路由 /system/ai-templates 等——保留书签直达，不破坏既有使用习惯    │
  │   ❌ 不做权限再分级——沿用 admin: true，与各子页面一致                          │
  │   ❌ 不做菜单嵌入主侧栏——入口在「系统管理」分组下，避免侧栏膨胀；admin 通过    │
  │      URL 或侧栏「AI 报告模板」入口跳转后切换 Tab                               │
  │   ❌ 不在 Tab1 实现新建模板/触发预生成——这些操作放在 Tab2 模板管理（单一真身），│
  │      Tab1 只做查看与删除                                                       │
  │ 设计文档：本文件头部（代码层）+ Tab1 上方 ⓘ 气泡（页面层）+                   │
  │           docs/standards/review-checklist.md（审查层）                         │
  └──────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="ai-admin">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">AI 管理</div>
        <div class="page-sub">报告 / 模板 / 模型 / 提示词 / 事务通知 — 一站式管理入口</div>
      </div>
      <el-popover placement="bottom-end" :width="460" trigger="click">
        <template #reference>
          <el-button size="small" :icon="InfoFilled" circle />
        </template>
        <div class="rationale">
          <p><b>替谁：</b>admin（管理员）。AI 报告系统多 provider、模板、调度、提示词、命中链路等能力已全部上线，但管理入口分散在 5 个独立路由（`/system/ai-templates` 等），配置时需多页跳转。</p>
          <p><b>答何决策：</b>① 今天哪些报告生成了？模板配置如何？模型节点是否健康？提示词是否更新？事务通知有无异常？</p>
          <p><b>怎么用：</b>Tab 按日常使用频度排序——① 报告列表（每天看）→ ② 模板（每周改）→ ③ 模型（每月调）→ ④ 提示词（季度更新）→ ⑤ 事务通知（异常时查）。</p>
          <p><b>不做什么：</b></p>
          <ul>
            <li>❌ 不删除旧路由（保留书签直达）；</li>
            <li>❌ 不做权限再分级（沿用 admin: true）；</li>
            <li>❌ 不做菜单嵌入主侧栏（避免侧栏膨胀）；</li>
            <li>❌ Tab1 只做查看与删除，新建/触发在 Tab2（单一真身）。</li>
          </ul>
          <p><b>设计文档：</b>本文件头部注释块（代码层）+ 本 ⓘ 气泡（页面层）+ docs/standards/review-checklist.md（审查层）。</p>
          <p><b>工单：</b>#137</p>
        </div>
      </el-popover>
    </div>

    <el-tabs v-model="tab" type="border-card" class="ai-admin-tabs">
      <!-- ═══ Tab1 报告列表（内联实现，复用 /admin/ai/reports API） ═══ -->
      <el-tab-pane label="① 报告列表" name="reports" lazy>
        <div class="tab-inner">
          <div class="flex-between mb-16">
            <div>
              <div class="page-title" style="margin: 0; font-size: 16px">AI 报告列表</div>
              <div class="page-sub">按 created_at DESC 排序；admin 全可见，不做 user_id 隔离</div>
            </div>
            <div>
              <el-select v-model="filter.is_public" placeholder="可见性" size="small" clearable style="width: 120px; margin-right: 8px" @change="loadReports">
                <el-option label="全部" :value="null" />
                <el-option label="公开(public)" :value="true" />
                <el-option label="私有(admin_only)" :value="false" />
              </el-select>
              <el-select v-model="filter.template_id" placeholder="模板" size="small" clearable filterable style="width: 200px" @change="loadReports">
                <el-option v-for="t in templates" :key="t.id" :label="t.name_cn" :value="t.id" />
              </el-select>
              <el-button size="small" :icon="Refresh" style="margin-left: 8px" @click="loadReports">刷新</el-button>
            </div>
          </div>

          <el-alert type="info" :closable="false" show-icon class="mb-16">
            <template #title>
              看什么：每行 = 一次预生成/实时问答的落库快照。<code>matched_count</code> = 命中次数（用户问句命中本报告）；
              <code>llm_ms</code>/<code>total_ms</code> = LLM 耗时/总耗时。空 = 无该字段。
              不做什么：本 Tab 不做新建/触发，这些操作放在 ② 模板管理（单一真身）。
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
              <el-table-column label="操作" width="100" fixed="right">
                <template #default="{ row }">
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
        </div>
      </el-tab-pane>

      <!-- ═══ Tab2 模板管理（内嵌存量组件，单一真身） ═══ -->
      <el-tab-pane label="② 模板管理" name="templates" lazy>
        <AiTemplates />
      </el-tab-pane>

      <!-- ═══ Tab3 模型/算力节点（内嵌存量组件） ═══ -->
      <el-tab-pane label="③ 模型节点" name="llm" lazy>
        <LlmProviders />
      </el-tab-pane>

      <!-- ═══ Tab4 提示词管理（内嵌存量组件） ═══ -->
      <el-tab-pane label="④ 提示词管理" name="focus" lazy>
        <AiFocusConfigs />
      </el-tab-pane>

      <!-- ═══ Tab5 事务通知（内嵌存量组件，按 ai/sync 类查看异常） ═══ -->
      <el-tab-pane label="⑤ 事务通知" name="events" lazy>
        <SystemEvents />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, InfoFilled } from '@element-plus/icons-vue'
import { aiTemplates, aiAdminReports, deleteAiReport } from '@/api/adminAi'
import AiTemplates from './AiTemplates.vue'
import LlmProviders from './LlmProviders.vue'
import AiFocusConfigs from './AiFocusConfigs.vue'
import SystemEvents from './SystemEvents.vue'

const tab = ref('reports')

// Tab1 报告列表
const reportLoading = ref(false)
const reports = ref({ items: [], total: 0 })
const reportPage = ref(1)
const filter = reactive({ page_size: 20, is_public: null, template_id: null })
const templates = ref([])

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
.ai-admin {
  padding: 16px;
}
.ai-admin-tabs {
  min-height: 400px;
}
.tab-inner {
  padding: 8px 4px;
}
.dim { color: #909399; font-size: 12px; }
.ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rationale p { margin: 6px 0; line-height: 1.6; }
.rationale ul { margin: 6px 0 6px 18px; padding: 0; }
.rationale li { margin: 2px 0; line-height: 1.6; }
</style>
