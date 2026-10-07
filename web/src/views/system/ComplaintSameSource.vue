<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：供数据治理/admin 对判重 v5 自动识别的**同源案件组**做人工复核与留痕。             │
  │       回答的决策：「同一台设备 14 天内这些投诉，是同一事由反复投诉，还是不同投诉？」     │
  │       使用者：数据治理/admin（复核），不是业务决策者（不直接出统计数字）。              │
  │ 逻辑：① 机判打底（同 SN + 文本相似≥0.7 + 14 天窗，连通分量成组）+ 置信度；             │
  │         疑似「再次来电内嵌前文」的组自动降级 needs_review → 人看整组（⑤A）。           │
  │       ② 组为操作单位：展开看成员与命中证据（相似度/间隔/旁证），确认/驳回写审计字段。   │
  │       ③ 组级取「登记日=最早投诉日、处理人/分类=最新有效行」（④C），避免误取早期行。    │
  │ 口径：判据与阈值单源在后端 app.services.complaint_same_source（RULE_VERSION=v5）；      │
  │       跨渠道簇仅作旁证不单独成判；无 SN/无日期/dup_residual、ignored 行不参与。         │
  │       规则与已拍板项见 docs/design/20260909-外部投诉同源判重合并核算-草案.md §5/§7。   │
  │ 取舍：❌ 不做"删除/合并原始投诉行"——只打组标、原行全保留，可完整回溯；                 │
  │       ❌ 不在本页改任何统计口径——本页只产出"哪些是同源"的人工结论；                    │
  │       ❌ confirmed/rejected 已终审组页面内不可改（要改需重算解散重建），防终审被覆盖；  │
  │       ❌ 不做自动定时重算入口——重算为离线小批量，由本页手动触发，调度归 talon 侧。      │
  │ 工单：#208（③A 渠道簇同簇 / ④C 登记日最早·处理人取最新 / ⑤A 自动打标+置信度）。       │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div>
    <!-- 页面可见说明：本页是什么、不是什么（含取舍，界面设计纪律第 2 层） -->
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        <b>同源案件复核（判重 v5 · 人工复核闭环）</b>
      </template>
      <div style="font-size:12px;line-height:1.7;margin-top:4px">
        机判把<b>同一台设备、14 天内、内容高度相似（≥{{ thresholdPct }}）</b>的投诉聚成「同源组」，
        疑似「再次来电把前文粘进正文」的组会标为 <el-tag size="small" type="warning">待人工</el-tag>。
        本页只做<b>人工确认/驳回并留痕</b>，<b>不删除、不合并不改原始投诉行</b>，也不直接改统计口径。<br>
        口径单源：后端 <code>complaint_same_source</code>（规则版本 {{ ruleVersion }}）；
        跨渠道簇仅作旁证；无 SN / 无日期 / 已打 dup_residual·ignored 的行不参与。
      </div>
    </el-alert>

    <!-- 筛选与操作 -->
    <div class="filter-bar mb-12">
      <el-select v-model="statusFilter" size="small" placeholder="全部状态" clearable
                 style="width:140px" @change="onFilterChange">
        <el-option label="待人工 needs_review" value="needs_review" />
        <el-option label="自动 auto" value="auto" />
        <el-option label="已确认 confirmed" value="confirmed" />
        <el-option label="已驳回 rejected" value="rejected" />
      </el-select>
      <el-button size="small" :icon="Refresh" :loading="loading" @click="loadGroups">刷新</el-button>
      <div style="flex:1"></div>
      <el-button size="small" :loading="running" @click="previewRun">预览重算（不落库）</el-button>
      <el-button size="small" type="primary" plain :loading="running" @click="confirmRun">
        执行重算
      </el-button>
    </div>

    <el-table v-loading="loading" :data="groups" size="small" border stripe max-height="600">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand-wrap">
            <div class="expand-sub">命中证据（{{ row.evidence.length }} 对）</div>
            <el-table :data="evidenceRows(row)" size="small" border>
              <el-table-column label="投诉A" width="80">
                <template #default="{ row: e }">#{{ e.a }}</template>
              </el-table-column>
              <el-table-column label="投诉B" width="80">
                <template #default="{ row: e }">#{{ e.b }}</template>
              </el-table-column>
              <el-table-column prop="sim" label="相似度" width="90">
                <template #default="{ row: e }">{{ pct(e.sim) }}</template>
              </el-table-column>
              <el-table-column prop="daydiff" label="间隔(天)" width="85" />
              <el-table-column label="旁证" min-width="180">
                <template #default="{ row: e }">
                  <el-tag v-for="c in e.clues" :key="c" size="small" effect="plain"
                          style="margin-right:4px">{{ c }}</el-tag>
                  <el-tag v-if="e.embed" size="small" type="warning">疑似内嵌前文</el-tag>
                  <span v-if="!e.clues.length && !e.embed" style="color:#94a3b8">—</span>
                </template>
              </el-table-column>
            </el-table>

            <div class="expand-sub" style="margin-top:12px">组成员（{{ row.member_count }} 行原始投诉，均保留）</div>
            <el-table :data="row.members" size="small" border>
              <el-table-column label="#" width="64">
                <template #default="{ row: m }">#{{ m.id }}</template>
              </el-table-column>
              <el-table-column label="投诉日" width="105">
                <template #default="{ row: m }">{{ day(m.complaint_date) }}</template>
              </el-table-column>
              <el-table-column prop="platform" label="渠道" min-width="130" show-overflow-tooltip />
              <el-table-column prop="channel_cluster" label="渠道簇" width="100" />
              <el-table-column prop="complainant" label="投诉人" width="90" />
              <el-table-column prop="complaint_category" label="分类(该行)" min-width="110"
                               show-overflow-tooltip />
              <el-table-column prop="follower" label="处理人(该行)" width="100" />
              <el-table-column prop="result" label="结果(该行)" width="100" />
              <el-table-column label="正文(截200)" min-width="240">
                <template #default="{ row: m }">
                  <span>{{ m.problem }}</span>
                  <el-tag v-if="m.embed_suspect" size="small" type="warning"
                          style="margin-left:6px">疑似内嵌前文</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="device_sn" label="设备SN" min-width="150" show-overflow-tooltip />
      <el-table-column label="状态" width="104">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType[row.status]">{{ statusLabel[row.status] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="92" align="center">
        <template #default="{ row }">{{ pct(row.confidence) }}</template>
      </el-table-column>
      <el-table-column prop="member_count" label="成员" width="64" align="center" />
      <el-table-column label="最早投诉日(登记日)" width="132">
        <template #default="{ row }">{{ day(row.first_complaint_date) }}</template>
      </el-table-column>
      <el-table-column label="最新投诉日" width="112">
        <template #default="{ row }">{{ day(row.latest_complaint_date) }}</template>
      </el-table-column>
      <el-table-column prop="latest_handler" label="最新处理人" width="100" />
      <el-table-column prop="latest_category" label="最新分类" min-width="110" show-overflow-tooltip />
      <el-table-column label="组内办结" width="84" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.done_any" size="small" type="success">有</el-tag>
          <span v-else style="color:#94a3b8">否</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="168" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'auto' || row.status === 'needs_review'">
            <el-button size="small" type="success" link @click="openReview(row, 'confirmed')">确认</el-button>
            <el-button size="small" type="danger" link @click="openReview(row, 'rejected')">驳回</el-button>
          </template>
          <span v-else style="font-size:11px;color:#94a3b8">
            {{ row.reviewed_name || '已终审' }}
          </span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !groups.length" description="暂无同源组（可点右上角「执行重算」生成）"
              :image-size="70" />

    <el-pagination
      v-if="total > pageSize"
      class="mb-16"
      layout="prev, pager, next, total"
      :total="total"
      :page-size="pageSize"
      :current-page="page"
      @current-change="onPageChange"
    />

    <!-- 人工终审弹窗 -->
    <el-dialog v-model="reviewVisible" :title="reviewForm.status === 'confirmed' ? '确认同源' : '驳回（非同源）'"
               width="480px">
      <div style="font-size:13px;line-height:1.8;margin-bottom:8px">
        设备 SN：<b>{{ reviewForm.device_sn }}</b>（{{ reviewForm.member_count }} 个成员）<br>
        判定：
        <el-tag size="small" :type="reviewForm.status === 'confirmed' ? 'success' : 'danger'">
          {{ reviewForm.status === 'confirmed' ? '确认同源（同事由反复投诉）' : '驳回（视为不同投诉）' }}
        </el-tag>
      </div>
      <el-input v-model="reviewForm.note" type="textarea" :rows="3"
                placeholder="复核备注（可选，将随判定留痕）" />
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitReview">保存判定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
/**
 * 同源案件复核（#208，判重 v5 ⑤A 最小人工闭环）。
 * 机判结果只读展示 + 人工确认/驳回留痕；重算为手动触发的离线小批量。
 */
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  listSameSourceGroups,
  reviewSameSourceGroup,
  runSameSourceGroups,
} from '@/api/complaintSameSource'

// 阈值/版本仅用于页面说明展示，真实判定以后端常量为准（不参与前端计算）
const thresholdPct = '70%'
const ruleVersion = 'v5'

const statusType = {
  auto: 'info',
  needs_review: 'warning',
  confirmed: 'success',
  rejected: 'danger',
}
const statusLabel = {
  auto: '自动',
  needs_review: '待人工',
  confirmed: '已确认',
  rejected: '已驳回',
}

const groups = ref([])
const loading = ref(false)
const running = ref(false)
const statusFilter = ref('needs_review') // 默认看待办（⑤A 复核优先处理 needs_review）
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const reviewVisible = ref(false)
const submitting = ref(false)
const reviewForm = ref({ id: null, device_sn: '', member_count: 0, status: '', note: '' })

function day(iso) {
  return iso ? iso.slice(0, 10) : '—'
}
function pct(v) {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}
function evidenceRows(row) {
  return row.evidence || []
}

async function loadGroups() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (statusFilter.value) params.status = statusFilter.value
    const res = await listSameSourceGroups(params)
    groups.value = res.data?.items || []
    total.value = res.data?.total || 0
  } catch {
    groups.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onFilterChange() {
  page.value = 1
  loadGroups()
}
function onPageChange(p) {
  page.value = p
  loadGroups()
}

function openReview(row, status) {
  reviewForm.value = {
    id: row.id, device_sn: row.device_sn, member_count: row.member_count,
    status, note: '',
  }
  reviewVisible.value = true
}

async function submitReview() {
  submitting.value = true
  try {
    await reviewSameSourceGroup(reviewForm.value.id, {
      status: reviewForm.value.status,
      note: reviewForm.value.note || null,
    })
    ElMessage.success('已保存人工判定')
    reviewVisible.value = false
    loadGroups()
  } finally {
    submitting.value = false
  }
}

// 预览：只统计不落库，给治理者先看规模
async function previewRun() {
  running.value = true
  try {
    const res = await runSameSourceGroups(true)
    const s = res.data || {}
    ElMessageBox.alert(
      `扫描投诉 ${s.scanned ?? '-'} 行；多投诉 SN 组 ${s.sn_multi_groups ?? '-'}；`
      + `命中对 ${s.pairs ?? '-'}；将生成同源组 ${s.groups_total ?? '-'} `
      + `（自动 ${s.groups_auto ?? '-'} / 待人工 ${s.groups_needs_review ?? '-'}）。`
      + `本次为预览，未落库。`,
      '重算预览（dry-run）',
      { confirmButtonText: '知道了' },
    )
  } finally {
    running.value = false
  }
}

// 落库重算：解散 auto/needs_review 旧组重建，confirmed/rejected 人工组保留
async function confirmRun() {
  try {
    await ElMessageBox.confirm(
      '将全量重算同源组：自动/待人工旧组会被解散后重建，已人工确认/驳回的组保留不动。是否继续？',
      '执行判重重算',
      { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  running.value = true
  try {
    const res = await runSameSourceGroups(false)
    const s = res.data || {}
    ElMessage.success(
      res.message || `重算完成：组 ${s.groups_total ?? '-'}（待人工 ${s.groups_needs_review ?? '-'}）`,
    )
    page.value = 1
    loadGroups()
  } finally {
    running.value = false
  }
}

onMounted(loadGroups)
</script>

<style scoped>
.expand-wrap {
  padding: 8px 16px 14px;
  background: #f8fafc;
}
.expand-sub {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 6px;
}
</style>
