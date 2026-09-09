<template>
  <div v-loading="loading" class="reconcile-check">
    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <!-- ===== 顶部说明 ===== -->
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      <template #title>
        <b>E（商户号真实退款流水）= 财务基准</b>，用 E 调校 ABCD（运营登记台账）：
        每路"已对齐"= 登记与 E 一致（可信）；"差异/空悬/歧义"单列，可下钻定位到具体单。
        ABC 运营登记较准（剔除不相干后应高度对齐 E）；D 手动登记会有误操作（差异最需治理）。
      </template>
    </el-alert>

    <!-- ===== 时间窗选择 ===== -->
    <section class="block-card mb-16">
      <div class="flex-between win-bar">
        <div>
          <span class="win-label">复核时间窗：</span>
          <el-radio-group v-model="quickRange" size="small" class="mr-12">
            <el-radio-button label="today">今日</el-radio-button>
            <el-radio-button label="yesterday">昨日</el-radio-button>
            <el-radio-button label="7">近7天</el-radio-button>
            <el-radio-button label="30">近30天</el-radio-button>
            <el-radio-button label="custom">自定义</el-radio-button>
          </el-radio-group>
          <el-date-picker
            v-model="winRange"
            type="daterange"
            size="small"
            value-format="YYYY-MM-DD"
            :disabled="quickRange !== 'custom'"
            start-placeholder="开始"
            end-placeholder="结束"
            style="width: 240px"
          />
          <el-button type="primary" size="small" :icon="Search" :loading="loading" @click="load">生成预览</el-button>
          <span v-if="winStart && winEnd" class="win-hint">当前预览：{{ winStart }} ~ {{ winEnd }}</span>
        </div>
      </div>
    </section>

    <!-- ===== E 数据截止警示 ===== -->
    <el-alert
      v-if="alignData && eDataUntil && winEnd && winEnd > eDataUntil"
      type="warning" :closable="false" show-icon class="mb-16"
      title="E 商户号实际现金流数据未覆盖完整窗口"
    >
      E 退款记录报表数据仅更新至 <b>{{ eDataUntil }}</b>（持续采集，通常延迟 1~2 天）。
      本窗口（截至 {{ winEnd }}）的 E 侧未采全，各路"差异/空悬"会偏多，请补充采集后复查。
    </el-alert>

    <!-- ===== 逐路对齐卡 ===== -->
    <section v-if="alignData" class="mb-16">
      <div class="grid-cards">
        <div class="align-card">
          <div class="card-head">
            <b>A 线上直退</b>
            <div class="head-ops">
              <el-button v-if="rA.unmatched > 0" size="small" text type="warning" :loading="resyncing==='A'" @click="reSyncRoute('A')">🔄 重采</el-button>
              <span class="tag-ok">线上直退</span>
            </div>
          </div>
          <div class="card-nums">
            <div class="num-main">共 {{ fmt(rA.total) }} 笔</div>
            <div class="num-ok" :style="{color:'#16a34a'}">✅ 已对齐 E {{ fmt(rA.matched_in_e) }}（{{ pctOf(rA.matched_in_e, rA.total) }}%）</div>
            <div v-if="rA.unmatched > 0" class="num-warn clickable" @click="openDetail('A')">
              ⚠️ 差异(E 无此单) {{ fmt(rA.unmatched) }} 笔 → 下钻
            </div>
            <div v-else class="num-ok">✅ 无差异</div>
          </div>
          <div class="card-foot">仅线上直退与 E 对账（转入钱包/线下不比对）</div>
        </div>

        <div class="align-card">
          <div class="card-head">
            <b>B 用户提现</b>
            <div class="head-ops">
              <el-button v-if="rB.chain_break > 0" size="small" text type="warning" :loading="resyncing==='B'" @click="reSyncRoute('B')">🔄 重采</el-button>
              <span class="tag-info">链路对账</span>
            </div>
          </div>
          <div class="card-nums">
            <div class="num-main">共 {{ fmt(rB.total) }} 笔</div>
            <div class="num-ok" :style="{color:'#16a34a'}">✅ 链路到 E {{ fmt(rB.chain_ok) }}（{{ pctOf(rB.chain_ok, rB.total) }}%）</div>
            <div v-if="rB.chain_break > 0" class="num-warn clickable" @click="openDetail('B')">
              ⚠️ 链路断(提现无对应 E 退款) {{ fmt(rB.chain_break) }} 笔 → 下钻
            </div>
            <div v-else class="num-ok">✅ 无断链</div>
          </div>
          <div class="card-foot">链路: 提现 remark RF → A转入钱包单 → E 水单（B转入/系统扣费不比对）</div>
        </div>

        <div class="align-card">
          <div class="card-head">
            <b>C 闪猫退款</b>
            <div class="head-ops">
              <el-button v-if="rC.unmatched > 0 || rC.total > 0" size="small" text type="warning" :loading="resyncing==='C'" @click="reSyncRoute('C')">🔄 重采</el-button>
              <span class="tag-ok">WXALR</span>
            </div>
          </div>
          <div class="card-nums">
            <div class="num-main">共 {{ fmt(rC.total) }} 笔</div>
            <div class="num-ok" :style="{color:'#16a34a'}">✅ 已对齐 E {{ fmt(rC.matched_in_e) }}（{{ pctOf(rC.matched_in_e, rC.total) }}%）</div>
            <div v-if="rC.unmatched > 0" class="num-warn clickable" @click="openDetail('C')">
              ⚠️ 差异(E 无此单) {{ fmt(rC.unmatched) }} 笔 → 下钻
            </div>
            <div v-else class="num-ok">✅ 无差异</div>
          </div>
          <div class="card-foot">闪猫已退款 order_no(WXALR) ↔ E · 注: 闪猫源需手动采集(未纳入定时), 差异多为采集缺, 点🔄重采</div>
        </div>

        <div class="align-card">
          <div class="card-head"><b>D 手动退款</b><span class="tag-warn">人工台账</span></div>
          <div class="card-nums">
            <div class="num-main">共 {{ fmt(rD.total) }} 笔</div>
            <div class="num-ok" :style="{color:'#16a34a'}">✅ 已对齐 E {{ fmt(rD.matched_in_e) }}（{{ pctOf(rD.matched_in_e, rD.total) }}%）</div>
            <div v-if="rD.unmatched > 0" class="num-warn clickable" @click="openDetail('D')">
              ⚠️ 差异(E 无此单) {{ fmt(rD.unmatched) }} 笔 → 下钻
            </div>
            <div v-if="rD.overlap_a > 0" class="num-dup clickable" @click="openAdOverlap()">
              🔶 与 A 线上重叠(同单两路都记) {{ fmt(rD.overlap_a) }} 笔
            </div>
          </div>
          <div class="card-foot">手动登记可信度最低, 差异最需治理(残留/误操作)</div>
        </div>
      </div>

      <div class="grid-cards mt-12">
        <div class="align-card issue">
          <div class="card-head">
            <b>空悬 E（E 有、ABCD 无登记）</b>
            <div class="head-ops">
              <el-button size="small" text type="warning" :loading="resyncing==='E'" @click="reSyncRoute('E')">🔄 重采E</el-button>
              <span class="tag-danger">漏记</span>
            </div>
          </div>
          <div class="card-nums">
            <div v-if="iss.e_orphan > 0" class="num-warn clickable" @click="openDetail('ORPHAN')">
              ⚠️ 窗口内 E 无任何路承接 {{ fmt(iss.e_orphan) }} 笔 → 下钻
            </div>
            <div v-else class="num-ok">✅ E 水单全部被 ABCD 承接</div>
            <div v-if="iss.e_orphan_wxalr > 0" class="num-sub">其中 WXALR {{ fmt(iss.e_orphan_wxalr) }} 笔 = 疑 C(闪猫)漏记/缺源</div>
            <div v-if="(iss.e_orphan - iss.e_orphan_wxalr - iss.e_orphan_recent) > 0" class="num-sub">
              其余历史 {{ fmt(iss.e_orphan - iss.e_orphan_wxalr - iss.e_orphan_recent) }} 笔 = 大概率 D(手动)少登记
            </div>
          </div>
          <div class="card-foot">E 已退款但 A/C/D 均无记录 = 漏记/待承接（窗口 E 共 {{ fmt(iss.e_total_in_window) }} 笔）</div>
        </div>
        <div class="align-card issue">
          <div class="card-head"><b>A∩D 归属歧义</b><span class="tag-warn">同单两记</span></div>
          <div class="card-nums">
            <div v-if="rD.overlap_a > 0" class="num-dup">
              D 路中与 A 线上重叠 {{ fmt(rD.overlap_a) }} 笔（同一订单两路都登记）
            </div>
            <div v-else class="num-ok">✅ 无 A/D 重叠</div>
            <div class="num-sub">处置: 按 A 优先, D 侧建议标 dup_residual（走质量页）</div>
          </div>
          <div class="card-foot">已识别 25 行候选并标 pending, 可在数据质量 → 手动退款 逐条确认</div>
        </div>
      </div>
    </section>
    <el-empty v-else-if="!loading && !errorMsg" description="选择时间窗后点击「生成预览」" :image-size="60" />

    <!-- ===== 差异明细下钻抽屉 ===== -->
    <el-drawer v-model="detailDrawer" :title="detailTitle" size="62%">
      <el-table v-loading="detailLoading" :data="detailItems" border stripe size="small" max-height="480">
        <el-table-column prop="biz_no" label="单号" width="170" fixed show-overflow-tooltip />
        <el-table-column prop="order_no" label="订单号/备注" min-width="150" show-overflow-tooltip />
        <el-table-column prop="device_sn" label="设备SN" width="130" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ 'sn-muted': !row.device_sn }">{{ row.device_sn || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="amount" label="金额" width="95" align="right">
          <template #default="{ row }">¥{{ fmtMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="time" label="时间" width="145" show-overflow-tooltip />
        <el-table-column prop="who" label="经手人/商户" width="120" show-overflow-tooltip />
        <el-table-column prop="quality_status" label="质量" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.quality_status" size="small" type="warning">{{ row.quality_status }}</el-tag>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="detailRoute === 'ORPHAN'" prop="suggest" label="归属建议" min-width="230">
          <template #default="{ row }">
            <span class="suggest-text">{{ row.suggest || '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pag-wrap">
        <el-pagination
          v-model:current-page="detailPage"
          :page-size="50" :total="detailTotal"
          layout="total, prev, pager, next" background small
          @current-change="loadDetail"
        />
      </div>
      <div v-if="detailRoute === 'ORPHAN'" class="detail-tip">
        💡 空悬 E 归属提示：WXALR 前缀 = 疑 C 路(闪猫)漏记/采集缺；近 2 天 = E 采集延迟待复查；
        历史非 WXALR = 大概率 D 路(手动)少登记 → 建议补录到 D。
      </div>
      <div v-else class="detail-tip">
        💡 建议：这些登记在 E 财务流水找不到。若属近 2 天 → E 延迟待复查；若为残留/误操作 → 数据质量页彻底忽略或标重复。
      </div>
    </el-drawer>

    <!-- ===== 历史复核批次(留痕) ===== -->
    <section class="block-card">
      <div class="block-head">
        <span class="block-title">历史复核批次（留痕存档）</span>
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
        <el-table-column prop="note" label="备注" min-width="160" show-overflow-tooltip />
      </el-table>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { reconcile } from '@/api/reconcile'
import { taskSubmit } from '@/api/fetch'

const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  reloadKey: { type: Number, default: 0 },
})

const loading = ref(false)
const errorMsg = ref('')
const alignData = ref(null)      // /reconcile/alignment 返回
const eDataUntil = ref('')
const history = ref([])
const histLoading = ref(false)

// ── 复核时间窗(自包含) ──
const quickRange = ref('30')
const winRange = ref([])

const winStart = computed(() => {
  if (quickRange.value === 'custom') return winRange.value?.[0] || ''
  if (quickRange.value === 'today') return dayjs().format('YYYY-MM-DD')
  if (quickRange.value === 'yesterday') return dayjs().subtract(1, 'day').format('YYYY-MM-DD')
  return dayjs().subtract(Number(quickRange.value), 'day').format('YYYY-MM-DD')
})
const winEnd = computed(() => {
  if (quickRange.value === 'custom') return winRange.value?.[1] || ''
  if (quickRange.value === 'today' || quickRange.value === 'yesterday') return winStart.value
  return dayjs().format('YYYY-MM-DD')
})

// ── 逐路卡数据 ──
const rA = computed(() => alignData.value?.routes?.A || { total: 0, matched_in_e: 0, unmatched: 0 })
const rB = computed(() => alignData.value?.routes?.B || { total: 0, chain_ok: 0, chain_break: 0 })
const rC = computed(() => alignData.value?.routes?.C || { total: 0, matched_in_e: 0, unmatched: 0 })
const rD = computed(() => alignData.value?.routes?.D || { total: 0, matched_in_e: 0, unmatched: 0, overlap_a: 0 })
const iss = computed(() => alignData.value?.issues || {
  e_orphan: 0, e_orphan_wxalr: 0, e_orphan_recent: 0, e_orphan_wxalr_recent: 0, e_total_in_window: 0,
})

// ── 差异下钻抽屉 ──
const detailDrawer = ref(false)
const detailRoute = ref('')
const detailTitle = ref('')
const detailLoading = ref(false)
const detailItems = ref([])
const detailPage = ref(1)
const detailTotal = ref(0)

const ROUTE_LABEL = { A: 'A 线上直退差异(E 无此单)', B: 'B 用户提现链路断', C: 'C 闪猫差异(E 无此单)', D: 'D 手动退款差异(E 无此单)', ORPHAN: '空悬 E(无任何路承接)' }

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
function pctOf(n, total) {
  if (!total) return '0.0'
  return ((Number(n) / total) * 100).toFixed(1)
}
function conclusionType(c) {
  return { 一致: 'success', 差异已确认: 'warning', 存疑待查: 'danger' }[c] || 'info'
}

// ── 加载对齐汇总 ──
async function load() {
  if (!winStart.value || !winEnd.value) {
    errorMsg.value = '请选择复核时间窗后点击「生成预览」'
    alignData.value = null
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await reconcile.alignment({ start: `${winStart.value}T00:00:00`, end: `${winEnd.value}T23:59:59` })
    alignData.value = res.data
    eDataUntil.value = res.data?.e_data_until || ''
  } catch (e) {
    errorMsg.value = '加载失败: ' + (e?.message || e)
  } finally {
    loading.value = false
  }
}

// ── 差异下钻 ──
function openDetail(route) {
  detailRoute.value = route
  detailTitle.value = ROUTE_LABEL[route] || route
  detailPage.value = 1
  detailItems.value = []
  detailTotal.value = 0
  detailDrawer.value = true
  loadDetail()
}
function openAdOverlap() {
  ElMessage.info('A/D 重叠治理: 25 行候选已标 pending, 请到「系统管理 → 数据质量 → 手动退款」逐条确认(保留D 或 标dup_residual剔除)')
}
async function loadDetail() {
  detailLoading.value = true
  try {
    const res = await reconcile.alignmentDetail({
      route: detailRoute.value,
      start: `${winStart.value}T00:00:00`, end: `${winEnd.value}T23:59:59`,
      page: detailPage.value, page_size: 50,
    })
    const d = res?.data || {}
    detailItems.value = d.items || []
    detailTotal.value = d.count || detailItems.value.length
  } catch (e) {
    ElMessage.error('明细加载失败: ' + (e?.message || e))
  } finally {
    detailLoading.value = false
  }
}

// ── 对账差异 → 重采该路数据源(2026-09-09 用户) ──
// 差异可能源于上游采集未到位(如 C 闪猫 default_sync=false 需手动采),
// 提供"对当前时间窗重采对应 BMS 源"入口, 复用 /fetch/task/submit。
const ROUTE_RESYNC_SOURCE = {
  A: { data_type: 'refund', label: 'A 线上直退', src: 'BMS 退费单管理(refund)' },
  B: { data_type: 'wallet', label: 'B 用户提现', src: 'BMS 提现操作记录(wallet)' },
  C: { data_type: 'shanmao_refund', label: 'C 闪猫退款', src: 'BMS 闪猫退款管理(shanmao_refund)' },
  E: { data_type: 'refund_record_report', label: 'E 商户号现金流', src: 'BMS 退款记录报表(refund_record_report)' },
}
const resyncing = ref('')   // 正在重采的路(A/B/C/E)

async function reSyncRoute(route) {
  const cfg = ROUTE_RESYNC_SOURCE[route]
  if (!cfg) { ElMessage.warning('该路暂不支持一键重采(D 路来自共享文档, 请在数据采集页触发)'); return }
  if (resyncing.value) { ElMessage.info('已有重采任务进行中'); return }
  if (!winStart.value || !winEnd.value) { ElMessage.warning('请先选择时间窗'); return }
  try {
    await ElMessageBox.confirm(
      `将重采「${cfg.label}」(${cfg.src})，时间范围 ${winStart.value} ~ ${winEnd.value}。\n` +
      '适用于差异疑为上游采集未到位(如闪猫需手动采)。任务后台执行, 完成后刷新本页复查。是否继续?',
      '重采确认', { confirmButtonText: '开始重采', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  resyncing.value = route
  try {
    const res = await taskSubmit({
      time_range: 'custom',
      custom_start: `${winStart.value}T00:00:00`,
      custom_end: `${winEnd.value}T23:59:59`,
      data_types: [cfg.data_type],
      ignore_cooldown: true,
    })
    const taskId = res?.data?.task_id || res?.data?.id
    ElMessage.success(`已提交重采任务${taskId ? `(#${taskId})` : ''}, 完成前请稍候`)
    // 提示刷新(任务后台执行)
    ElMessage.info('重采在后台执行, 可稍后点「生成预览」复查差异是否减少')
  } catch (e) {
    ElMessage.error('重采提交失败: ' + (e?.message || e))
  } finally {
    resyncing.value = ''
  }
}

async function loadHistory() {
  histLoading.value = true
  try {
    const res = await reconcile.checkList({ page: 1, page_size: 20 })
    history.value = res.data.items || []
  } catch (e) {
    // 忽略
  } finally {
    histLoading.value = false
  }
}

// 默认近30天; 父级给"区间"时跟随(单日默认不跟随)
function followParentWindow() {
  if (props.start && props.end && props.start !== props.end) {
    quickRange.value = 'custom'
    winRange.value = [props.start, props.end]
    return true
  }
  return false
}
function setDefault30d() {
  quickRange.value = '30'
  winRange.value = [dayjs().subtract(29, 'day').format('YYYY-MM-DD'), dayjs().format('YYYY-MM-DD')]
}

watch([() => props.start, () => props.end, () => props.reloadKey], () => {
  if (followParentWindow()) load()
})

onMounted(() => {
  if (!followParentWindow()) setDefault30d()
  loadHistory()
  load()
})
</script>

<style scoped>
.reconcile-check { min-height: 200px; }
.error-msg { color: #dc2626; padding: 12px; }
.block-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.block-head { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f3f4f6; padding-bottom: 10px; margin-bottom: 4px; }
.block-title { font-size: 15px; font-weight: 600; color: #374151; }
.flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.win-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.win-label { font-size: 13px; font-weight: 600; color: #374151; margin-right: 4px; }
.win-hint { font-size: 12px; color: #909399; margin-left: 8px; }
.mb-16 { margin-bottom: 16px; }
.mt-12 { margin-top: 12px; }
.text-muted { color: #909399; }
.pag-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }
.detail-tip { font-size: 12px; color: #b45309; background: #fffbeb; padding: 8px 12px; border-radius: 6px; margin-top: 12px; }
.grid-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.align-card { background: #fff; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); border: 1px solid #eef0f3; }
.align-card.issue { border-left: 4px solid #f59e0b; }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.head-ops { display: flex; align-items: center; gap: 4px; }
.head-ops .el-button + .el-button { margin-left: 0; }
.card-head b { font-size: 15px; color: #1f2937; }
.card-nums { display: flex; flex-direction: column; gap: 6px; }
.num-main { font-size: 13px; color: #4b5563; }
.num-ok { font-size: 14px; font-weight: 600; }
.num-warn { font-size: 14px; font-weight: 600; color: #dc2626; }
.num-dup { font-size: 13px; font-weight: 600; color: #d97706; }
.clickable { cursor: pointer; text-decoration: underline dotted; }
.clickable:hover { color: #2563eb; }
.num-sub { font-size: 12px; color: #6b7280; }
.card-foot { font-size: 11px; color: #9ca3af; margin-top: 8px; border-top: 1px dashed #f0f0f0; padding-top: 6px; }
.tag-ok { font-size: 11px; color: #16a34a; background: #f0fdf4; padding: 1px 6px; border-radius: 4px; }
.tag-info { font-size: 11px; color: #0891b2; background: #ecfeff; padding: 1px 6px; border-radius: 4px; }
.tag-warn { font-size: 11px; color: #d97706; background: #fffbeb; padding: 1px 6px; border-radius: 4px; }
.tag-danger { font-size: 11px; color: #dc2626; background: #fef2f2; padding: 1px 6px; border-radius: 4px; }
.sn-muted { color: #c0c4cc; font-style: italic; }
.suggest-text { font-size: 12px; color: #b45309; }
</style>
