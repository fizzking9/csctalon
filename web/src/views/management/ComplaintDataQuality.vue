<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：数据治理专员**排查投诉采集质量**（重复判定 / SN 残影），保障统计口径可信。       │
  │       使用者：数据治理/运维，**不是**决策者。                                           │
  │ 逻辑：① 这是"运维自查工具"，不是"决策看板" → 2026-09-11 用户决策(工单 #67 P3)          │
  │         将其从「投诉分析」**外迁到系统管理**，原因：决策页只回答"要不要管、管哪里"，     │
  │         混入治理工具会让非目标用户迷路(原页面 8 个 tab 中有 2 个是治理)；                │
  │       ② 两个子块(重复判定 #55 / 采集质量 #60)同属"采集可信度"，合并在一个页内切换；      │
  │       ③ 默认「只读」—— 两页都**不删原始行**，只打标/列清单，避免治理动作不可逆。        │
  │ 口径：重复判据 = SN+电话+渠道全一致 → 真重复；多渠道 → 归并(非重复)。                   │
  │       残影 = 源文档"先建行后补SN"产生的缺 SN 不完整行(已从统计排除，原行保留)。          │
  │       详见 docs/standards/数据采集合并去重机制说明.md                                   │
  │ 取舍：❌ 不做"一键删除残影" —— 原行必须保留可回溯(用户明确"不能乱删")；                  │
  │       ❌ 不做统计口径调整 —— 本页只呈现事实，口径变更归 #47/#53/#58 体系。              │
  │ 工单：#67 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §七(P3)        │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div>
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        <b>投诉采集质量（治理工具 · 只读）</b>
      </template>
      <div style="font-size:12px;line-height:1.7;margin-top:4px">
        本页用于<b>排查投诉数据的采集质量</b>，不参与业务决策。
        两个子页均为<b>只读清单</b>：程序只打标/列示，<b>不删除任何原始行</b>，可完整回溯。
        <br>决策口径相关请看「投诉分析 → 决策概览」；口径定义见
        <code>docs/standards/投诉渠道口径与对账说明.md</code>。
      </div>
    </el-alert>

    <el-tabs v-model="tab">
      <!-- ① 重复判定 · 人工审核(#55) -->
      <el-tab-pane label="重复判定 · 人工审核" name="review">
        <div class="block-head">
          <span class="block-title">重复判定 · 人工审核</span>
          <span class="block-hint">
            同设备同日出现多个电话 → 程序默认<b>统计为不同投诉</b>, 需人工确认是否同一人 ·
            <b>只读</b>, 不删除任何原始记录
          </span>
        </div>

        <el-alert type="info" :closable="false" show-icon class="mb-12">
          <template #title>
            <b>归并口径(#55)</b>: 同一投诉多渠道呈现(渠道不同) → 归并为 1 笔, <b>不算重复</b>;
            SN + 电话 + 渠道<b>全一致</b>(同渠道) → 才是拷贝复制的<b>真重复</b>。
          </template>
          <div style="font-size:12px;line-height:1.7;margin-top:4px">
            本页列出<b>无法程序判定</b>的情况: 同一 SN 同一天登记了<b>多个不同电话</b>。
            默认按「不同投诉」统计, 但实际可能是同一人换号/填错号 → 人工确认。
          </div>
        </el-alert>

        <div class="filter-bar mb-12">
          <el-select v-model="reviewSource" size="small" placeholder="来源" clearable style="width:130px" @change="loadDedupReview">
            <el-option label="315外部投诉" value="external" />
            <el-option label="(外)卡商" value="card" />
          </el-select>
          <el-date-picker v-model="range" type="daterange" size="small" value-format="YYYY-MM-DD"
                          start-placeholder="开始" end-placeholder="结束" style="width:240px"
                          @change="reloadAll" />
          <el-button size="small" :icon="Refresh" :loading="reviewLoading" @click="loadDedupReview">刷新</el-button>
        </div>

        <el-row :gutter="12" class="mb-16">
          <el-col :xs="12" :sm="8" :md="6">
            <div class="kpi-card" style="background:#fff1f2;border-left:3px solid #e11d48">
              <div class="kpi-label" style="color:#e11d48">待人工判定组</div>
              <div class="kpi-value" style="color:#e11d48">{{ formatNum(reviewList.length) }}</div>
              <div class="kpi-sub">同设备同日多电话</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="6">
            <div class="kpi-card" style="background:#f8fafc;border-left:3px solid #64748b">
              <div class="kpi-label" style="color:#475569">涉及记录</div>
              <div class="kpi-value" style="color:#334155">{{ formatNum(reviewRecordCount) }}</div>
              <div class="kpi-sub">原始行 · 均保留不删</div>
            </div>
          </el-col>
        </el-row>

        <el-table v-loading="reviewLoading" :data="reviewList" size="small" border stripe max-height="520">
          <el-table-column label="来源" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.source_type === 'card' ? 'success' : 'warning'" effect="plain">
                {{ row.source_type === 'card' ? '(外)卡商' : '315外部' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="complaint_day" label="投诉日" width="110" />
          <el-table-column prop="device_sn" label="设备SN" min-width="150" show-overflow-tooltip />
          <el-table-column label="涉及电话" min-width="190">
            <template #default="{ row }">
              <el-tag v-for="p in (row.phones || [])" :key="p" size="small" style="margin-right:4px" effect="plain">{{ p }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="记录数" width="90" align="center">
            <template #default="{ row }">{{ row.record_count ?? (row.record_ids || []).length }}</template>
          </el-table-column>
          <el-table-column label="记录ID" min-width="150">
            <template #default="{ row }">
              <span style="font-size:11px;color:#64748b">{{ (row.record_ids || []).join(', ') }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="触发原因" min-width="200" show-overflow-tooltip />
        </el-table>
        <el-empty v-if="!reviewLoading && !reviewList.length" description="暂无待人工判定记录" :image-size="60" />
      </el-tab-pane>

      <!-- ② 采集质量 · SN 后补残影(#60) -->
      <el-tab-pane label="采集质量 · SN 残影" name="quality">
        <div class="block-head">
          <span class="block-title">采集质量 · SN 后补残影</span>
          <span class="block-hint">
            源文档「先建行、后补 SN」→ 同一投诉产生两条 ·
            <b>已从统计排除</b> · <b>原始行保留可回溯</b>
          </span>
        </div>

        <el-alert type="info" :closable="false" show-icon class="mb-12">
          <template #title>
            <b>这是什么</b>：源文档存在「先建一行、稍后再补 SN」的采集习惯。
            补 SN 后去重指纹变化 → 同一投诉产生两条记录。
          </template>
          <div style="font-size:12px;line-height:1.7;margin-top:4px">
            <b>残影</b>= 缺 SN 的不完整行（入库较早）；<b>完整行</b>= 有 SN 的齐全行（入库较晚）。<br>
            → 残影已打标并从<b>统计口径排除</b>（避免同一条投诉被算两次），
            <b>但行本身完整保留</b>，把标记清空即可完整回滚。
          </div>
        </el-alert>

        <div class="filter-bar mb-12">
          <el-select v-model="qualitySource" size="small" placeholder="来源" clearable style="width:130px" @change="loadSnBackfill">
            <el-option label="315外部投诉" value="external" />
            <el-option label="(外)卡商" value="card" />
          </el-select>
          <el-date-picker v-model="range" type="daterange" size="small" value-format="YYYY-MM-DD"
                          start-placeholder="开始" end-placeholder="结束" style="width:240px"
                          @change="reloadAll" />
          <el-button size="small" :icon="Refresh" :loading="qualityLoading" @click="loadSnBackfill">刷新</el-button>
        </div>

        <el-row :gutter="12" class="mb-16">
          <el-col :xs="12" :sm="8" :md="6">
            <div class="kpi-card" style="background:#f1f5f9;border-left:3px solid #64748b">
              <div class="kpi-label" style="color:#475569">残影总数</div>
              <div class="kpi-value" style="color:#334155">{{ formatNum(qualityList.length) }}</div>
              <div class="kpi-sub">已排除统计 · 原行保留</div>
            </div>
          </el-col>
          <el-col v-for="(n, m) in qualityByMonth" :key="m" :xs="12" :sm="8" :md="6">
            <div class="kpi-card" style="background:#fff7ed;border-left:3px solid #ea580c">
              <div class="kpi-label" style="color:#ea580c">{{ m }}</div>
              <div class="kpi-value" style="color:#ea580c">{{ formatNum(n) }}</div>
              <div class="kpi-sub">该月残影数</div>
            </div>
          </el-col>
        </el-row>

        <el-table v-loading="qualityLoading" :data="qualityList" size="small" border stripe max-height="480">
          <el-table-column label="来源" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="row.source_type === 'card' ? 'success' : 'warning'" effect="plain">
                {{ row.source_type === 'card' ? '(外)卡商' : '315外部' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="complaint_date" label="投诉日" width="110" />
          <el-table-column prop="platform" label="平台" min-width="120" show-overflow-tooltip />
          <el-table-column label="残影ID" width="90" align="center">
            <template #default="{ row }"><span style="color:#e11d48">{{ row.ghost_id }}</span></template>
          </el-table-column>
          <el-table-column label="完整行ID" width="90" align="center">
            <template #default="{ row }"><span style="color:#16a34a">{{ row.full_id ?? '—' }}</span></template>
          </el-table-column>
          <el-table-column label="补齐的SN" min-width="150">
            <template #default="{ row }">
              <span v-if="row.full_device_sn" style="font-size:11px">{{ row.full_device_sn }}</span>
              <el-tag v-else size="small" type="danger" effect="plain">未找到完整行</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="残影入库" min-width="150">
            <template #default="{ row }">
              <span style="font-size:11px;color:#64748b">{{ (row.ghost_created_at || '').slice(0, 19).replace('T', ' ') }}</span>
            </template>
          </el-table-column>
          <el-table-column label="完整行入库" min-width="150">
            <template #default="{ row }">
              <span style="font-size:11px;color:#16a34a">{{ (row.full_created_at || '').slice(0, 19).replace('T', ' ') }}</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!qualityLoading && !qualityList.length" description="暂无 SN 后补残影(采集质量良好)" :image-size="60" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
/**
 * 投诉采集质量（治理工具）—— 由 `ComplaintAnalysis.vue` 的
 * 「人工审核(#55)」「采集质量(#60)」两个 tab 外迁而来（工单 #67 P3）。
 *
 * ★ 为什么外迁：这两页的使用者是数据治理/运维，而投诉分析的使用者是决策者。
 *   混在一个页面会让决策者在 8 个 tab 里找不到主线（原 8 tab 中 2 个是治理）。
 *   用户 2026-09-11 决策：「所有的数据治理都转到系统管理去做」。
 *
 * ⚠️ 本页**只读**：不提供任何删除动作，与用户"不能乱删"的要求一致。
 */
import { ref, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { complaintDedupReview, complaintSnBackfill } from '@/api/complaint'

const tab = ref('review')
const range = ref([])

const reviewList = ref([])
const reviewLoading = ref(false)
const reviewSource = ref('')
const reviewRecordCount = computed(() =>
  reviewList.value.reduce((n, g) => n + (g.record_count ?? (g.record_ids || []).length), 0)
)

const qualityList = ref([])
const qualityLoading = ref(false)
const qualitySource = ref('')
const qualityByMonth = computed(() => {
  const m = {}
  for (const r of qualityList.value) {
    const k = (r.complaint_date || '').slice(0, 7)
    if (k) m[k] = (m[k] || 0) + 1
  }
  return m
})

function params() {
  const p = {}
  if (range.value?.length === 2) {
    p.start = range.value[0]
    p.end = range.value[1]
  }
  return p
}

async function loadDedupReview() {
  reviewLoading.value = true
  try {
    const p = { ...params() }
    if (reviewSource.value) p.source_type = reviewSource.value
    const res = await complaintDedupReview(p)
    reviewList.value = res.data?.items || []
  } catch {
    reviewList.value = []
  } finally {
    reviewLoading.value = false
  }
}

async function loadSnBackfill() {
  qualityLoading.value = true
  try {
    const p = { ...params() }
    if (qualitySource.value) p.source_type = qualitySource.value
    const res = await complaintSnBackfill(p)
    qualityList.value = res.data?.items || []
  } catch {
    qualityList.value = []
  } finally {
    qualityLoading.value = false
  }
}

function reloadAll() {
  loadDedupReview()
  loadSnBackfill()
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  return Number.isNaN(n) ? '0' : n.toLocaleString('zh-CN')
}

onMounted(reloadAll)
</script>

<style scoped>
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.block-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.block-title { font-size: 15px; font-weight: 600; color: #1f2937; }
.block-hint { font-size: 12px; color: #9ca3af; }
.kpi-card { border-radius: 10px; padding: 12px 14px; min-height: 68px; display: flex; flex-direction: column; gap: 4px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 11px; color: #6b7280; }
</style>
