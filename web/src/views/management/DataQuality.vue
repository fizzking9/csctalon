<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">数据质量看板</div>
        <div class="page-sub">占位规则: 异常数据模式识别 · 字段先留后扩充 · 数据真实性识别</div>
      </div>
      <el-button v-if="authStore.isAdmin" type="primary" :icon="Plus" @click="openCreate">新建规则</el-button>
    </div>

    <el-tabs v-model="activeTab" class="mb-16">
      <el-tab-pane label="质量规则" name="rules">
        <el-card class="mb-16">
          <el-alert type="info" :closable="false" show-icon>
            <template #title>说明</template>
            当前为「占位规则阶段」(规划设计 §8.4 / §11.6): 规则表可配置、可启用/停用, 检测逻辑随后续迭代逐步落地。
            规则示例: 同一 SN 短时多次退款、单据时间倒挂、字段空值率超阈值、跨表关联不一致。
          </el-alert>
        </el-card>

        <el-card>
          <template #header>
            <div class="flex-between">
              <span style="font-weight: 600">质量规则列表</span>
              <el-button :icon="Refresh" link @click="loadRules">刷新</el-button>
            </div>
          </template>
          <el-table v-loading="loading" :data="rules" border stripe>
            <el-table-column prop="name" label="规则名" min-width="180" show-overflow-tooltip />
            <el-table-column prop="target" label="作用对象" width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.target || '全局' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="condition" label="条件表达式" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <code class="cond-code">{{ row.condition || '-' }}</code>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="text-muted">{{ row.description || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column v-if="authStore.isAdmin" label="操作" width="180" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
                <el-button link type="warning" @click="toggleEnabled(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
                <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!loading && !rules.length" description="暂无规则, 点击右上角新建" />
        </el-card>

        <!-- 新建/编辑 Dialog -->
        <el-dialog v-model="editVisible" :title="editForm.id ? '编辑规则' : '新建规则'" width="560px">
          <el-form :model="editForm" label-width="100px">
            <el-form-item label="规则名" required><el-input v-model="editForm.name" placeholder="如: 同SN短时多次退款" /></el-form-item>
            <el-form-item label="作用对象">
              <el-input v-model="editForm.target" placeholder="表名/字段名/全局, 如 refund.created_at" />
            </el-form-item>
            <el-form-item label="条件表达式">
              <el-input v-model="editForm.condition" type="textarea" :rows="3" placeholder="SQL 片段/JSON Path/正则, 如 count(1) > 5 AND duration < '1h'" />
              <div class="text-muted" style="font-size: 12px; margin-top: 4px">
                当前为占位存储, 检测执行器随后续迭代接入。建议同时写说明。
              </div>
            </el-form-item>
            <el-form-item label="说明">
              <el-input v-model="editForm.description" type="textarea" :rows="2" placeholder="规则意图、触发场景、建议动作" />
            </el-form-item>
            <el-form-item label="启用">
              <el-switch v-model="editForm.enabled" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="editVisible = false">取消</el-button>
            <el-button type="primary" :loading="submitting" @click="handleSave">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- 通用数据质量审核(L3): 各业务表单按 L2 导入自动校验结果人工确认/修正 -->
      <el-tab-pane v-for="t in auditTables" :key="t.code" :label="t.label" :name="t.code">
        <div v-if="t.code === 'manual_refund' && authStore.isAdmin" class="flex-between mb-16">
          <div class="page-sub">
            治理工具: ① 缺SN坏副本(同单已由带SN行/E表计)→ 排除; ② E空悬单(系统/自动原路退, 无ABCD登记)→ 回填D台账(备注溯源, 后续ABC补回以ABC为准)。
          </div>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <el-button type="warning" :icon="Refresh" :loading="disposing" @click="autoDisposeNoSn">
              自动排除缺SN坏副本
            </el-button>
            <el-button type="primary" :icon="Refresh" :loading="backfilling" @click="backfillEOrphan">
              E空悬回填D台账
            </el-button>
            <el-button type="danger" plain :icon="Refresh" :loading="dupDispatching" @click="autoDupSameAmount">
              排除同单同金额重复
            </el-button>
            <el-button type="danger" :icon="Refresh" :loading="noeDispatching" @click="autoDupNoEMatch">
              剔除E无对应D冗余
            </el-button>
          </div>
        </div>
        <QualityAudit :table="t.code" :refresh-tick="refreshTick" />
      </el-tab-pane>

      <!-- 拒收数据(未采纳行): A-20260908-003 数据治理框架 -->
      <el-tab-pane label="拒收数据" name="rejected">
        <RejectedRows />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { listRules, createRule, updateRule, deleteRule, autoDisposeDNoSnBadDup, backfillEOrphanToD, autoDisposeDDupSameAmount, autoDisposeDNoEMatch } from '@/api/dataQuality'
import QualityAudit from './QualityAudit.vue'
import RejectedRows from './RejectedRows.vue'

const authStore = useAuthStore()
const activeTab = ref('rules')
const rules = ref([])
const loading = ref(false)
const submitting = ref(false)
const disposing = ref(false)
const backfilling = ref(false)
const dupDispatching = ref(false)
const noeDispatching = ref(false)
const refreshTick = ref(0)
const editVisible = ref(false)
const editForm = reactive({ id: null, name: '', target: '', condition: '', description: '', enabled: true })

// 通用质量审核支持的业务表单
const auditTables = [
  { code: 'manual_refund', label: '手动退款' },
  { code: 'refund', label: '退费单' },
  { code: 'ticket', label: '服务工单' },
  { code: 'wallet', label: '钱包流水' },
  { code: 'shanmao', label: '闪猫退款' },
]

async function loadRules() {
  loading.value = true
  try {
    const res = await listRules()
    rules.value = res.data || []
  } finally {
    loading.value = false
  }
}

async function autoDisposeNoSn() {
  try {
    await ElMessageBox.confirm(
      '将扫描手动退款中「缺设备SN且同订单已由带SN行/E表真实出款」的已退款行，自动标记为已治理重复(dup_residual)从经营统计排除。\n\n仅处理未治理行(pending/空白)，已治理(auto_fixed 等)不动；可随时恢复。是否继续？',
      '自动排除缺SN坏副本',
      { confirmButtonText: '开始排除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }
  disposing.value = true
  try {
    const res = await autoDisposeDNoSnBadDup(2000, false)
    const d = res.data || {}
    ElMessage.success(`已自动排除 ${d.disposed ?? 0} 行缺SN坏副本(扫描 ${d.scanned ?? 0}, 跳过待人工 ${d.skipped_insufficient ?? 0})`)
    refreshTick.value += 1  // 通知 QualityAudit 刷新
  } finally {
    disposing.value = false
  }
}

// D 同单同金额重复自动排除: 同一订单+同一金额被登记两遍(副本), 剔除副本保留带SN行
async function autoDupSameAmount() {
  try {
    await ElMessageBox.confirm(
      '将扫描手动退款中「同订单号 + 同退款金额」登记了两遍的重复行，自动把副本(SN空/pending/auto_fixed 行)标记为 dup_residual 排除，保留带SN/正常行。\n\n逐单对账发现 D 路此类重复约 ¥1.1万+/月。可随时恢复。是否继续？',
      '排除同单同金额重复',
      { confirmButtonText: '开始排除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }
  dupDispatching.value = true
  try {
    const res = await autoDisposeDDupSameAmount(10000)
    const d = res.data || {}
    ElMessage.success(`已排除 ${d.disposed ?? 0} 行同单同金额重复副本(组 ${d.groups ?? 0}, 留人工 ${d.left_manual ?? 0})`)
    refreshTick.value += 1
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '排除失败')
  } finally {
    dupDispatching.value = false
  }
}

// D 冗余剔除: 该 D 行金额在同单 E 退款里无对应笔(登记冗余/整单额坏行)
// 时序护栏: 仅处理已对账日期(≤E最新日); E 采集滞后窗自动跳过, E补采后重跑恢复
async function autoDupNoEMatch() {
  try {
    await ElMessageBox.confirm(
      '将扫描手动退款中「金额在 E(退款记录报表)同单找不到对应笔」的已退款行，标记为 dup_residual 排除(逐笔对E判定冗余)。\n\n仅处理 E 已采集覆盖的日期(已对账)；E 滞后窗(近2天)自动跳过不误伤，E 补采后重跑自动恢复。备注/类型信息保留可查。是否继续？',
      '剔除E无对应D冗余',
      { confirmButtonText: '开始剔除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }
  noeDispatching.value = true
  try {
    const res = await autoDisposeDNoEMatch(10000)
    const d = res.data || {}
    ElMessage.success(`已剔除 ${d.disposed ?? 0} 行 D 冗余(E无对应, E覆盖至 ${d.e_data_until ?? '?'}; 未对账窗跳过 ${d.skipped_pending_e ?? 0})`)
    refreshTick.value += 1
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '剔除失败')
  } finally {
    noeDispatching.value = false
  }
}

// E空悬 → D台账回填: 收集日期范围(默认上月1日~昨日), 确认后执行
async function backfillEOrphan() {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()  // 0-based
  // 上月 = 若本月1月则去年12月, 否则本月-1
  const py = m === 0 ? y - 1 : y
  const pm = m === 0 ? 11 : m - 1
  const firstOfLast = `${py}-${String(pm + 1).padStart(2, '0')}-01`
  const lastDay = new Date(py, pm + 1, 0).getDate() // 上月天数
  const lastOfLast = `${py}-${String(pm + 1).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`
  try {
    const { value } = await ElMessageBox.prompt(
      'E空悬单(退款记录报表已退、ABCD无登记, 多为系统/自动原路退)将 INSERT 到手动退款台账, is_refunded=是、备注"实际对账回填"溯源。\n\n请输日期范围(YYYY-MM-DD ~ YYYY-MM-DD):',
      'E空悬回填D台账',
      { confirmButtonText: '开始回填', cancelButtonText: '取消', inputValue: `${firstOfLast} ~ ${lastOfLast}`, type: 'info' }
    )
    const parts = (value || '').split(/[~～至\s]+/).filter(Boolean)
    if (parts.length !== 2) return ElMessage.warning('请输入形如 2026-08-01 ~ 2026-08-31 的日期范围')
    backfilling.value = true
    const res = await backfillEOrphanToD(parts[0].trim(), parts[1].trim(), 10000)
    const d = res.data || {}
    ElMessage.success(`已回填 ${d.inserted ?? 0} 笔 E 空悬到 D 台账(跳过已有 ${d.skipped_existing ?? 0})`)
    refreshTick.value += 1
  } catch (err) {
    if (err === 'cancel' || err?.toString?.().includes('cancel')) return
    ElMessage.error(err?.response?.data?.detail || '回填失败')
  } finally {
    backfilling.value = false
  }
}

function openCreate() {
  Object.assign(editForm, { id: null, name: '', target: '', condition: '', description: '', enabled: true })
  editVisible.value = true
}

function openEdit(row) {
  Object.assign(editForm, { ...row })
  editVisible.value = true
}

async function handleSave() {
  if (!editForm.name.trim()) return ElMessage.warning('请输入规则名')
  submitting.value = true
  try {
    if (editForm.id) {
      await updateRule(editForm.id, { ...editForm })
    } else {
      await createRule({ ...editForm })
    }
    ElMessage.success('保存成功')
    editVisible.value = false
    loadRules()
  } finally {
    submitting.value = false
  }
}

async function toggleEnabled(row) {
  await updateRule(row.id, { enabled: !row.enabled })
  ElMessage.success(row.enabled ? '已停用' : '已启用')
  loadRules()
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`删除规则「${row.name}」?`, '提示', { type: 'warning' })
  await deleteRule(row.id)
  ElMessage.success('已删除')
  loadRules()
}

onMounted(loadRules)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.text-muted { color: #909399; font-size: 12px; }
.cond-code {
  font-family: 'SF Mono', Menlo, monospace; font-size: 12px;
  background: #f5f7fa; padding: 2px 6px; border-radius: 3px; color: #606266;
}
</style>
