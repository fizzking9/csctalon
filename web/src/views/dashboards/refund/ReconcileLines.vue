<!--
  ┌─────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：替对账复核人（财务/主管）逐笔确认「每笔 E 商户号真实退款对应哪一路运营登记、是否一致」，   │
  │       并以人工打标闭环差异 —— 回答「差异单具体是哪几笔、谁确认的、什么理由」。                   │
  │ 逻辑：先 4 张摘要卡看进度（总数/已核对/待核对/差异排除），再按路别与状态过滤，落到下方逐笔表；   │
  │       E 为财务基准，复核只「打标不改源」。                                                     │
  │ 口径：来源 GET /reconcile/check/{check_id}/lines 与 /lines-summary（服务                        │
  │       backend/app/services/reconcile_check.py，仅写 reconcile_matches，不改任何源表）。         │
  │       E 笔 = 批次窗口内的商户号真实退款流水（退款记录报表，采集延迟 1~2 天）；                   │
  │       自动归类 = 按链路匹配判 A线上/B钱包/C闪猫/D手动/未登记；                                   │
  │       「已核对」= 确认状态≠待核对的笔数（含 差异已确认/存疑待查/已排除重复）；                   │
  │       「差异/排除」= 差异已确认 + 存疑待查 + 已排除重复 三态之和。                              │
  │       时点 = 随所属复核批次固化（批次生成时选定 E 路窗口）；打标后立即生效。                     │
  │       详见 docs/arch/A-20260906-002-退款五路手动对账复核功能.md。                               │
  │ 取舍：① 「已核对」「差异/排除」两卡不做一键下钻 —— 二者为多状态并集，逐笔列表仅支持单状态筛选，  │
  │          一键下钻会得到与卡数字对不上的子集（误导），请用「确认状态」下拉逐状态核对；            │
  │       ② 不在列表内修改 E 源数据 —— 复核语义是打标留痕，改源会破坏对账基准；                     │
  │       ③ 「候选匹配」只读展示 —— auto_route 由批次生成时判定，改归类须在批次上重采。              │
  │ 备注：本组件当前未被任何页面挂载（预留给「逐笔勾稽明细核对」场景，A-20260906-002 v1.1 #22）。    │
  │ 工单：#205                                                                                     │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div v-loading="loading" class="reconcile-lines">
    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <!-- 标题行 + 页面层可见设计说明（界面设计说明规范 §二·方案A，含「取舍」段） -->
    <div class="section-head mb-12">
      <span class="section-title">E 路逐笔勾稽明细
        <el-tooltip effect="dark" placement="top-start" raw-content
          content="<b>这个页面为什么这样设计</b><br/><b>给谁</b>：对账复核人（财务/主管）<br/><b>回答什么</b>：每笔 E 商户号真实退款对应哪路登记、是否一致；差异单是哪几笔、谁确认的<br/><b>组织逻辑</b>：摘要 4 卡看进度 → 路别/状态过滤 → 逐笔表打标闭环；E 为财务基准，只打标不改源<br/><b>口径</b>：E 笔=批次窗口内商户号真实退款流水（采集延迟 1~2 天）；自动归类 A线上/B钱包/C闪猫/D手动/未登记；「已核对」=状态≠待核对；「差异/排除」=差异已确认+存疑待查+已排除重复<br/>详见 docs/arch/A-20260906-002-退款五路手动对账复核功能.md<br/><br/><b>⚠️ 取舍（不做什么及原因）</b><br/>① 「已核对」「差异/排除」两卡不做一键下钻 —— 多状态并集，列表仅支持单状态筛选，下钻会得到对不上卡数字的子集<br/>② 不在列表内改 E 源数据 —— 复核只打标留痕，改源破坏对账基准<br/>③ 候选匹配只读 —— 改归类须在批次上重采">
          <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-2px;font-size:13px"><QuestionFilled /></el-icon>
        </el-tooltip>
      </span>
    </div>

    <!-- 进度卡
         ★ #205 看板卡片双强制（规范 §二/§三）: 每卡 ① 有 ⓘ 说明 ② 可下钻或显式声明不下钻。
           E笔总数/待核对 → 下方逐笔表（同页 §3.2③，携批次+状态筛选）;
           已核对/差异排除 → 多状态并集, 不下钻（理由见各卡ⓘ与文件头取舍①）。 -->
    <el-row v-if="summary" :gutter="12" class="mb-12">
      <el-col :xs="12" :sm="6">
        <!-- ★ #205 E笔总数卡: 全批次口径, 下钻=清空路别/状态筛选后看全部逐笔 -->
        <div class="kpi-card kpi-drillable" style="background:#eff6ff"
             title="点击查看批次内全部逐笔" @click="drillToLines('')">
          <div class="kpi-label" style="color:#2563eb">E 笔总数
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/reconcile/check/{check_id}/lines-summary 的 total<br/><b>口径</b>：所属复核批次内全部 E 笔（商户号真实退款流水逐笔），含全部确认状态与路别<br/><b>时点</b>：随批次固化（批次生成时选定 E 路窗口；E 采集延迟 1~2 天）<br/><b>「空」的含义</b>：0 = 批次为空或批次未生成（未选批次时整个摘要不显示）<br/><b>下钻</b>：点卡片 → 下方逐笔表（清空路别/状态筛选，逐字对应本卡数字）">
              <span class="kpi-info" @click.stop>
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
              </span>
            </el-tooltip>
          </div>
          <div class="kpi-value">{{ summary.total }}<span class="unit">笔</span></div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <!-- ★ #205 已核对卡: 四种已确认状态并集, 列表仅支持单状态筛选 → 不下钻(规范 §3.4 声明) -->
        <div class="kpi-card" style="background:#f0fdf4">
          <div class="kpi-label" style="color:#16a34a">已核对
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/reconcile/check/{check_id}/lines-summary 的 confirmed<br/><b>口径</b>：确认状态 ≠「待核对」的笔数之和（已核对一致 + 差异已确认 + 存疑待查 + 已排除重复 四态并集），括号内为占总数百分比<br/><b>时点</b>：随批次固化；打标后立即刷新<br/><b>「空」的含义</b>：0 = 批次尚未开始逐笔确认<br/><b>⚠️ 不下钻</b>：四态并集无法用列表的单状态筛选一次表达（下钻会得到对不上卡数字的子集）——请用下方「确认状态」下拉逐状态核对">
              <span class="kpi-info">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
              </span>
            </el-tooltip>
          </div>
          <div class="kpi-value">{{ summary.confirmed }}<span class="unit">笔</span></div>
          <div class="kpi-sub">{{ summary.total ? Math.round(summary.confirmed / summary.total * 100) : 0 }}%</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <!-- ★ #205 待核对卡: by_status['待核对'], 下钻=状态筛选置「待核对」并清空路别, 数字逐字对应 -->
        <div class="kpi-card kpi-drillable" style="background:#fef2f2"
             title="点击筛出批次内全部「待核对」逐笔" @click="drillToLines('待核对')">
          <div class="kpi-label" style="color:#dc2626">待核对
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/reconcile/check/{check_id}/lines-summary 的 by_status['待核对']<br/><b>口径</b>：确认状态为「待核对」（含从未打标的自动生成行，批次生成时默认该状态）的笔数<br/><b>时点</b>：随批次固化；打标后立即刷新<br/><b>「空」的含义</b>：0 = 批次内已无待核对（全部打过标）<br/><b>下钻</b>：点卡片 → 下方逐笔表（状态=待核对、路别=全部，与卡数字逐字对应）">
              <span class="kpi-info" @click.stop>
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
              </span>
            </el-tooltip>
          </div>
          <div class="kpi-value">{{ (summary.by_status?.['待核对'] || 0) }}<span class="unit">笔</span></div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="6">
        <!-- ★ #205 差异/排除卡: 三状态并集, 列表仅支持单状态筛选 → 不下钻(规范 §3.4 声明) -->
        <div class="kpi-card" style="background:#f5f3ff">
          <div class="kpi-label" style="color:#7c3aed">差异/排除
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/reconcile/check/{check_id}/lines-summary 的 by_status<br/><b>口径</b>：差异已确认 + 存疑待查 + 已排除重复 三态之和（人工判定存在差异或重复、但已处理/待查的笔数）<br/><b>时点</b>：随批次固化；打标后立即刷新<br/><b>「空」的含义</b>：0 = 批次内无差异/重复笔（或尚未打此类标）<br/><b>⚠️ 不下钻</b>：三态并集无法用列表的单状态筛选一次表达——请用下方「确认状态」下拉分别查看「差异已确认/存疑待查/已排除重复」">
              <span class="kpi-info">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
              </span>
            </el-tooltip>
          </div>
          <div class="kpi-value">{{ (summary.by_status?.['差异已确认'] || 0) + (summary.by_status?.['已排除重复'] || 0) + (summary.by_status?.['存疑待查'] || 0) }}<span class="unit">笔</span></div>
        </div>
      </el-col>
    </el-row>

    <!-- 过滤 + 刷新 -->
    <div class="flex-between mb-12">
      <div class="filter-bar">
        <el-radio-group v-model="routeFilter" size="small" @change="load">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="A">A线上</el-radio-button>
          <el-radio-button value="B">B钱包</el-radio-button>
          <el-radio-button value="C">C闪猫</el-radio-button>
          <el-radio-button value="D">D手动</el-radio-button>
          <el-radio-button value="unmatched">未登记</el-radio-button>
        </el-radio-group>
        <el-select v-model="statusFilter" size="small" placeholder="确认状态" clearable style="width:130px;margin-left:8px" @change="load">
          <el-option v-for="s in statusOptions" :key="s" :label="s" :value="s" />
        </el-select>
      </div>
      <el-button size="small" :icon="Refresh" @click="load">刷新逐笔</el-button>
    </div>

    <!-- E 笔逐笔列表（摘要卡「E笔总数/待核对」的同一页下钻落点） -->
    <el-table ref="linesRef" :data="lines" border stripe size="small" max-height="460">
      <el-table-column prop="platform_order_no" label="平台订单号" width="180" fixed show-overflow-tooltip />
      <el-table-column label="E 退款时间" width="140">
        <template #default="{ row }">{{ fmtDate(row.refund_time) }}</template>
      </el-table-column>
      <el-table-column label="金额" width="100" align="right">
        <template #default="{ row }">¥{{ fmtMoney(row.refund_amount) }}</template>
      </el-table-column>
      <el-table-column label="自动归类" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="routeTagType(row.auto_route)">{{ routeLabel(row.auto_route) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="候选匹配" min-width="180">
        <template #default="{ row }">
          <template v-if="row.matched_ref?.length">
            <el-tooltip placement="top">
              <template #content>
                <div v-for="(c, i) in row.matched_ref" :key="i" class="cand-line">
                  [{{ routeLabel(c.route) }}] {{ c.ref_no || c.source_id || '' }} ¥{{ fmtMoney(c.amount) }}{{ c.extra ? ' · ' + c.extra : '' }}
                </div>
              </template>
              <el-tag size="small" type="info" effect="plain">{{ row.matched_ref.length }} 个候选</el-tag>
            </el-tooltip>
          </template>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="确认状态" width="130">
        <template #default="{ row }">
          <el-select v-if="canWrite" v-model="row.confirm_status" size="small" style="width:120px"
                     :disabled="savingLine === row.report_id" @change="(v) => onStatusChange(row, v)">
            <el-option v-for="s in statusOptions" :key="s" :label="s" :value="s" />
          </el-select>
          <el-tag v-else size="small" :type="statusTagType(row.confirm_status)">{{ row.confirm_status || '待核对' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="备注/确认人" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="text-muted">{{ row.confirm_note || '' }}</span>
          <span v-if="row.confirmed_name" class="text-muted"> · {{ row.confirmed_name }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openNote(row)">备注</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager-row">
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
                     layout="total, prev, pager, next" size="small" background @current-change="load" />
    </div>

    <!-- 备注输入 dialog -->
    <el-dialog v-model="noteDialog" title="逐笔备注" width="460px">
      <el-input v-model="noteText" type="textarea" :rows="3" placeholder="填写差异原因/处理说明(可选)" />
      <template #footer>
        <el-button size="small" @click="noteDialog = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingNote" @click="saveNote">保存备注</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, QuestionFilled } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { reconcile } from '@/api/reconcile'

const props = defineProps({
  checkId: { type: Number, default: null },
  canWrite: { type: Boolean, default: false },
})

const loading = ref(false)
const errorMsg = ref('')
const lines = ref([])
const summary = ref(null)
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const routeFilter = ref('')
const statusFilter = ref('')
const savingLine = ref(null)
const noteDialog = ref(false)
const noteText = ref('')
const savingNote = ref(false)
const noteRow = ref(null)

const statusOptions = ['已核对一致', '差异已确认', '存疑待查', '已排除重复', '待核对']

function routeLabel(r) {
  return { A: 'A线上', B: 'B钱包', C: 'C闪猫', D: 'D手动', unmatched: '未登记' }[r] || r || '—'
}
function routeTagType(r) {
  return { A: 'primary', B: 'info', C: 'success', D: 'warning', unmatched: 'danger' }[r] || 'info'
}
function statusTagType(s) {
  return { 已核对一致: 'success', 差异已确认: 'warning', 存疑待查: 'danger', 已排除重复: 'info' }[s] || 'info'
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

async function load() {
  if (!props.checkId) { lines.value = []; summary.value = null; return }
  loading.value = true
  errorMsg.value = ''
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (routeFilter.value) params.route = routeFilter.value
    if (statusFilter.value) params.status = statusFilter.value
    const res = await reconcile.checkLines(props.checkId, params)
    lines.value = res.data.items || []
    total.value = res.data.total || 0
    const sum = await reconcile.checkLinesSummary(props.checkId)
    summary.value = sum.data
  } catch (e) {
    errorMsg.value = '逐笔加载失败: ' + (e?.message || e)
  } finally {
    loading.value = false
  }
}

// ★ #205 下钻（同页 §3.2③）: 摘要卡 → 下方逐笔表。上下文=所属批次（checkId 内含 E 路时间窗）
//   + 路别/状态筛选。摘要卡是「全批次」口径, 故清空路别筛选、按需置状态筛选,
//   保证落地列表 total 与卡上数字逐字对应（不清路别会得到对不上的子集, 属误导）。
const linesRef = ref(null)
function drillToLines(status) {
  if (!props.checkId) return
  routeFilter.value = ''
  statusFilter.value = status || ''
  page.value = 1
  load()
  nextTick(() => linesRef.value?.$el?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }))
}

async function onStatusChange(row, status) {
  if (!props.canWrite) return
  savingLine.value = row.report_id
  try {
    await reconcile.checkLineConfirm(props.checkId, {
      report_id: row.report_id, confirm_status: status, confirm_note: row.confirm_note || '',
    })
    ElMessage.success('已更新')
    load()
  } catch (e) {
    ElMessage.error('更新失败: ' + (e?.message || e))
  } finally {
    savingLine.value = null
  }
}

function openNote(row) {
  noteRow.value = row
  noteText.value = row.confirm_note || ''
  noteDialog.value = true
}

async function saveNote() {
  if (!noteRow.value) return
  savingNote.value = true
  try {
    await reconcile.checkLineConfirm(props.checkId, {
      report_id: noteRow.value.report_id,
      confirm_status: noteRow.value.confirm_status || '待核对',
      confirm_note: noteText.value || '',
    })
    ElMessage.success('备注已保存')
    noteDialog.value = false
    load()
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.message || e))
  } finally {
    savingNote.value = false
  }
}

watch(() => props.checkId, () => { page.value = 1; load() })
</script>

<style scoped>
.reconcile-lines { min-height: 100px; }
.error-msg { color: #dc2626; padding: 8px 0; }
/* ★ #205 标题行 + 可下钻卡片交互反馈（看板卡片规范 §3.5）: cursor:pointer + hover 反馈 */
.section-head { display: flex; align-items: center; }
.section-title { font-size: 15px; font-weight: 600; color: #111827; }
.kpi-card { border-radius: 8px; padding: 10px 12px; }
.kpi-drillable { cursor: pointer; transition: box-shadow .15s ease, transform .15s ease; }
.kpi-drillable:hover { box-shadow: 0 2px 10px rgba(0,0,0,0.12); transform: translateY(-1px); }
.kpi-info { display: inline-flex; margin-left: 2px; }
.kpi-label { font-size: 12px; margin-bottom: 4px; }
.kpi-value { font-size: 20px; font-weight: 700; color: #374151; }
.kpi-value .unit { font-size: 12px; font-weight: 400; color: #6b7280; }
.kpi-sub { font-size: 12px; color: #6b7280; margin-top: 2px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.filter-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.mb-12 { margin-bottom: 12px; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 8px; }
.cand-line { font-size: 12px; line-height: 1.7; white-space: nowrap; }
.text-muted { color: #9ca3af; }
</style>
