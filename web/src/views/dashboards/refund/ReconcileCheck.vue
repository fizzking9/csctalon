<template>
  <div v-loading="loading" class="reconcile-check">
    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <!-- ===== 顶部说明 + 复核操作 ===== -->
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        对账复核：按所选时间窗对比 <b>经营四路 A/B/C/D</b>（经营估算）与 <b>E 退款记录报表</b>（商户号实际现金流，对账基准）。
        复核结论仅<u>留痕存档</u>，不改变任何统计口径。D 路重复/脏数据仅作辅助提示。
      </template>
    </el-alert>

    <section class="block-card mb-16">
      <div class="flex-between">
        <div class="block-head" style="border:none;padding:0">
          <span class="block-title">五路对比 · 复核结论</span>
          <span class="block-hint">{{ winLabel }}</span>
        </div>
        <div class="check-actions">
          <el-radio-group v-model="conclusion" size="small" class="mr-12">
            <el-radio-button label="一致">一致</el-radio-button>
            <el-radio-button label="差异已确认">差异已确认</el-radio-button>
            <el-radio-button label="存疑待查">存疑待查</el-radio-button>
          </el-radio-group>
          <el-input v-model="note" placeholder="复核备注(差异原因等, 可选)" size="small" style="width:260px;margin-right:8px" clearable />
          <el-button type="primary" size="small" :icon="Check" :disabled="!canWrite || !snapshot" @click="confirm">确认复核落库</el-button>
          <el-button size="small" :icon="Refresh" @click="load">刷新预览</el-button>
        </div>
      </div>

      <!-- 五路合计 KPI -->
      <el-row v-if="snapshot" :gutter="12" class="mt-12 mb-12">
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#eff6ff">
            <div class="kpi-label" style="color:#2563eb">A 线上直退</div>
            <div class="kpi-value">{{ fmt(snap.route_a?.count) }}<span class="unit">笔</span></div>
            <div class="kpi-sub">¥{{ fmtMoney(snap.route_a?.amount) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#eef2ff">
            <div class="kpi-label" style="color:#4f46e5">B 钱包提现</div>
            <div class="kpi-value">{{ fmt(snap.route_b?.count) }}<span class="unit">笔</span></div>
            <div class="kpi-sub">¥{{ fmtMoney(snap.route_b?.amount) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#ecfeff">
            <div class="kpi-label" style="color:#0891b2">C 闪猫退款</div>
            <div class="kpi-value">{{ fmt(snap.route_c?.count) }}<span class="unit">笔</span></div>
            <div class="kpi-sub">¥{{ fmtMoney(snap.route_c?.amount) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#fef2f2">
            <div class="kpi-label" style="color:#dc2626">D 手动退款</div>
            <div class="kpi-value">{{ fmt(snap.route_d?.count) }}<span class="unit">笔</span></div>
            <div class="kpi-sub">¥{{ fmtMoney(snap.route_d?.amount) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#fff7ed">
            <div class="kpi-label" style="color:#ea580c">经营估算 A+B+C+D</div>
            <div class="kpi-value strong">{{ fmt(snap.operational?.count) }}<span class="unit">笔</span></div>
            <div class="kpi-sub">¥{{ fmtMoney(snap.operational?.amount) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" style="background:#f0fdfa">
            <div class="kpi-label" style="color:#0f766e">E 商户号实际(对账基准)</div>
            <div class="kpi-value strong">{{ fmt(snap.actual_cashflow?.count) }}<span class="unit">笔</span></div>
            <div class="kpi-sub">¥{{ fmtMoney(snap.actual_cashflow?.amount) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-card" :style="{ background: diffColor }">
            <div class="kpi-label" style="color:#374151">对账偏差 经营−E</div>
            <div class="kpi-value strong" :style="{ color: (snap.diff?.amount ?? 0) >= 0 ? '#dc2626' : '#16a34a' }">
              {{ (snap.diff?.amount ?? 0) >= 0 ? '+' : '' }}{{ fmtMoney(snap.diff?.amount) }}
            </div>
            <div class="kpi-sub">{{ (snap.diff?.count ?? 0) >= 0 ? '+' : '' }}{{ fmt(snap.diff?.count) }} 笔</div>
          </div>
        </el-col>
      </el-row>

      <!-- 差异辅助说明 + D 重复检出 -->
      <el-alert
        v-if="snapshot && dupeWarn"
        type="warning" :closable="false" show-icon class="mb-12"
        title="D 路重复/脏数据提示（辅助复核，请先治理 #21 再作对账结论）"
      >
        <div class="dupe-line">同订单号多行组 {{ dupeWarn.dup_order_groups }} 个 · 超额金额估算 ¥{{ fmtMoney(dupeWarn.dup_amount_est) }}</div>
        <div class="dupe-line">窗口内无SN+年份异常残留行 {{ dupeWarn.no_sn_weird_rows_in_win }} 行 / ¥{{ fmtMoney(dupeWarn.no_sn_weird_amt_in_win) }}</div>
      </el-alert>
      <el-alert v-else-if="snapshot && !dupeWarn" type="success" :closable="false" show-icon class="mb-12">
        本窗口未检出 D 路重复/脏数据残留。
      </el-alert>
    </section>

    <!-- ===== 明细: E 水单勾稽归属 ===== -->
    <section class="block-card mb-16">
      <div class="block-head">
        <span class="block-title">E 水单勾稽归属（逐笔匹配经营路）</span>
        <span class="block-hint">E 退款记录报表每笔 → A 线上直退 / B 钱包 / C 闪猫 / D 手动 / 未登记</span>
      </div>
      <el-table v-if="byRouteRows.length" :data="byRouteRows" border stripe size="small" class="mt-12">
        <el-table-column prop="label" label="归属" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="row.tagType">{{ row.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="笔数" align="right">
          <template #default="{ row }">{{ fmt(row.count) }} 笔</template>
        </el-table-column>
        <el-table-column prop="amount" label="金额" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="占比(按金额)" align="right">
          <template #default="{ row }">{{ pct(row.amount) }}%</template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="本窗口无 E 水单数据" :image-size="60" />
    </section>

    <!-- ===== 历史复核批次 ===== -->
    <section class="block-card">
      <div class="block-head">
        <span class="block-title">历史复核批次（留痕）</span>
        <el-button size="small" text type="primary" @click="loadHistory">刷新</el-button>
      </div>
      <el-table v-loading="histLoading" :data="history" border stripe size="small" class="mt-12" max-height="300">
        <el-table-column prop="check_no" label="复核单号" width="140" fixed />
        <el-table-column label="时间窗" min-width="200">
          <template #default="{ row }">{{ fmtDate(row.window_start) }} ~ {{ fmtDate(row.window_end) }}</template>
        </el-table-column>
        <el-table-column label="结论" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.conclusion" size="small" :type="conclusionType(row.conclusion)">{{ row.conclusion }}</el-tag>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="复核人" width="100">
          <template #default="{ row }">{{ row.reviewed_name || '—' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="viewDetail(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 复核批次详情抽屉 -->
    <el-drawer v-model="drawer" title="复核批次详情" size="52%">
      <div v-if="detail">
        <el-descriptions :column="2" border size="small" class="mb-16">
          <el-descriptions-item label="复核单号">{{ detail.check_no }}</el-descriptions-item>
          <el-descriptions-item label="时间窗">{{ fmtDate(detail.window_start) }} ~ {{ fmtDate(detail.window_end) }}</el-descriptions-item>
          <el-descriptions-item label="结论">
            <el-tag v-if="detail.conclusion" size="small" :type="conclusionType(detail.conclusion)">{{ detail.conclusion }}</el-tag>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="复核人">{{ detail.reviewed_name || '—' }} {{ detail.reviewed_at ? '(' + fmtDate(detail.reviewed_at) + ')' : '' }}</el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">{{ detail.note || '—' }}</el-descriptions-item>
        </el-descriptions>
        <div class="detail-sub">五路合计快照（落库冻结时点口径）</div>
        <el-table :data="detailRouteRows" border stripe size="small" max-height="220">
          <el-table-column prop="label" label="路线" width="160" />
          <el-table-column prop="count" label="笔数" align="right" />
          <el-table-column prop="amount" label="金额(元)" align="right" />
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'
import { reconcile } from '@/api/reconcile'

const props = defineProps({
  start: { type: String, default: '' },   // YYYY-MM-DD
  end: { type: String, default: '' },
  reloadKey: { type: Number, default: 0 },
})

const auth = useAuthStore()
const loading = ref(false)
const errorMsg = ref('')
const snapshot = ref(null)
const conclusion = ref('')
const note = ref('')
const history = ref([])
const histLoading = ref(false)
const drawer = ref(false)
const detail = ref(null)

// 复核写权限: 仅 admin/supervisor(与后端 AdminOrSupervisor 一致)。
// 用 Pinia auth store 的 role(auth.role = user.role, 来自登录态), 响应式随登录态变化。
const canWrite = computed(() => ['admin', 'supervisor'].includes(auth.role))

function winLabel() {
  if (!props.start && !props.end) return ''
  return `${props.start || '全量'} ~ ${props.end || '至今'}`
}

function fmt(n) {
  return n == null ? 0 : Number(n).toLocaleString('zh-CN')
}
function fmtMoney(n) {
  if (n == null || isNaN(Number(n))) return '0.00'
  return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtDate(s) {
  if (!s) return '—'
  const t = s.includes('T') ? s : `${s}T00:00:00`
  return dayjs(t).format('YYYY-MM-DD HH:mm')
}
function pct(amt) {
  const total = snapshot.value?.actual_cashflow?.amount || 0
  if (!total) return '0.0'
  return ((Number(amt) / total) * 100).toFixed(1)
}

const snap = computed(() => snapshot.value || null)
const diffColor = computed(() => {
  const d = snapshot.value?.diff?.amount ?? 0
  return d === 0 ? '#f0fdf4' : (d > 0 ? '#fef2f2' : '#eff6ff')
})
const dupeWarn = computed(() => {
  const d = snapshot.value?.dupe_detect
  if (!d) return null
  return (d.dup_order_groups > 0 || d.no_sn_weird_rows_in_win > 0) ? d : null
})

const byRouteRows = computed(() => {
  const b = snapshot.value?.by_route_of_e
  if (!b) return []
  const meta = { A: ['A 线上直退', 'primary'], B: ['B 钱包提现', 'info'], C: ['C 闪猫退款', 'success'], D: ['D 手动退款', 'warning'], unmatched: ['未登记(经营未记账)', 'danger'] }
  return Object.entries(b).map(([k, v]) => ({ label: meta[k]?.[0] || k, tagType: meta[k]?.[1] || 'info', count: v?.count || 0, amount: v?.amount || 0 }))
})

function conclusionType(c) {
  return { 一致: 'success', 差异已确认: 'warning', 存疑待查: 'danger' }[c] || 'info'
}

async function load() {
  if (!props.start || !props.end) {
    errorMsg.value = '请先在上方选择时间区间后点击「分析」'
    snapshot.value = null
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await reconcile.checkRun({ start: `${props.start}T00:00:00`, end: `${props.end}T23:59:59` })
    snapshot.value = res.data
    conclusion.value = ''
    note.value = ''
  } catch (e) {
    errorMsg.value = '预览失败: ' + (e?.message || e)
  } finally {
    loading.value = false
  }
}

async function confirm() {
  if (!snapshot.value) return
  try {
    const params = { start: `${props.start}T00:00:00`, end: `${props.end}T23:59:59` }
    if (conclusion.value) params.conclusion = conclusion.value
    if (note.value) params.note = note.value
    const res = await reconcile.checkConfirm(params)
    ElMessage.success(`复核已落库: ${res.data.check_no}`)
    note.value = ''
    conclusion.value = ''
    loadHistory()
  } catch (e) {
    ElMessage.error('落库失败: ' + (e?.message || e))
  }
}

async function loadHistory() {
  histLoading.value = true
  try {
    const res = await reconcile.checkList({ page: 1, page_size: 20 })
    history.value = res.data.items || []
  } catch (e) {
    // 忽略历史加载失败(不影响主预览)
  } finally {
    histLoading.value = false
  }
}

async function viewDetail(row) {
  try {
    const res = await reconcile.checkDetail({ id: row.id })
    detail.value = res.data
    drawer.value = true
  } catch (e) {
    ElMessage.error('详情加载失败: ' + (e?.message || e))
  }
}

const detailRouteRows = computed(() => {
  const s = detail.value?.route_snapshot
  if (!s) return []
  const meta = { route_a: 'A 线上直退', route_b: 'B 钱包提现', route_c: 'C 闪猫退款', route_d: 'D 手动退款', operational: '经营估算 A+B+C+D', actual_cashflow: 'E 商户号实际(对账基准)', diff: '对账偏差(经营−E)' }
  return Object.entries(meta).map(([k, label]) => ({
    label,
    count: s[k]?.count ?? 0,
    amount: k === 'diff' ? `${(s[k]?.amount ?? 0) >= 0 ? '+' : ''}${Number(s[k]?.amount ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}` : Number(s[k]?.amount ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  }))
})

// 权限提示: 仅 admin/supervisor 可写(与后端一致; 后端仍强制校验)
watch([() => props.start, () => props.end, () => props.reloadKey], () => {
  if (props.start && props.end) load()
})
onMounted(() => {
  loadHistory()
  if (props.start && props.end) load()
})
</script>

<style scoped>
.reconcile-check { min-height: 200px; }
.error-msg { color: #dc2626; padding: 12px; }
.block-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.block-head { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f3f4f6; padding-bottom: 10px; margin-bottom: 4px; }
.block-title { font-size: 15px; font-weight: 600; color: #374151; }
.block-hint { font-size: 12px; color: #9ca3af; }
.flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.check-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.mt-12 { margin-top: 12px; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mr-12 { margin-right: 12px; }
.kpi-card { border-radius: 8px; padding: 10px 12px; height: 100%; }
.kpi-label { font-size: 12px; margin-bottom: 6px; }
.kpi-value { font-size: 20px; font-weight: 700; color: #374151; }
.kpi-value .unit, .kpi-sub .unit { font-size: 12px; font-weight: 400; color: #6b7280; }
.kpi-sub { font-size: 12px; color: #6b7280; margin-top: 4px; }
.kpi-value.strong, .kpi-sub.strong { font-weight: 700; }
.dupe-line { font-size: 12px; line-height: 1.8; }
.detail-sub { font-size: 13px; font-weight: 600; color: #374151; margin: 12px 0 8px; }
.text-muted { color: #9ca3af; }
.num { font-size: 12px; color: #6b7280; margin-right: 6px; }
.amt { font-weight: 600; color: #374151; }
.num.strong { font-size: 13px; font-weight: 700; color: #374151; }
.amt.strong { font-weight: 700; }
</style>
