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
            缺SN坏副本: 同订单已由带SN行/E表计, 自动标 dup_residual 排除(可恢复)。仅处理未治理行(pending/空白)。
          </div>
          <el-button type="warning" :icon="Refresh" :loading="disposing" @click="autoDisposeNoSn">
            自动排除缺SN坏副本
          </el-button>
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
import { listRules, createRule, updateRule, deleteRule, autoDisposeDNoSnBadDup } from '@/api/dataQuality'
import QualityAudit from './QualityAudit.vue'
import RejectedRows from './RejectedRows.vue'

const authStore = useAuthStore()
const activeTab = ref('rules')
const rules = ref([])
const loading = ref(false)
const submitting = ref(false)
const disposing = ref(false)
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
