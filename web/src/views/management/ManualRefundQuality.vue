<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">手动退款质量审核</div>
        <div class="page-sub">登记日期连续/合理性校验 · 高置信自动纠错(auto_fixed) · 无法可靠推断的标记待人工确认(pending)</div>
      </div>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
    </div>

    <!-- 状态统计卡(可点击筛选) -->
    <el-row :gutter="12" class="mb-16">
      <el-col v-for="c in statCards" :key="c.key" :xs="12" :sm="6">
        <div class="stat-card" :class="{ 'stat-active': status === c.key }" @click="switchStatus(c.key)">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value" :style="{ color: c.color }">{{ summary[c.key] ?? 0 }}</div>
        </div>
      </el-col>
    </el-row>

    <el-alert v-if="status === 'pending' && !summary.pending" type="success" :closable="false" show-icon class="mb-16">
      暂无待人工确认的记录，导入异常数据已全部处理或自动纠错。
    </el-alert>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe size="small" max-height="520">
        <el-table-column prop="register_time" label="登记日期" width="150" fixed show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="row.quality_status === 'pending' ? 'pending-text' : ''">{{ row.register_time || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="op_time" label="操作时间" width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.op_time || '—' }}</template>
        </el-table-column>
        <el-table-column prop="is_refunded" label="已退款" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_refunded === '是' ? 'success' : 'info'">{{ row.is_refunded }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="refund_amount" label="退款金额" width="100" align="right">
          <template #default="{ row }">¥{{ formatMoney(row.refund_amount) }}</template>
        </el-table-column>
        <el-table-column prop="order_no" label="订单号" min-width="170" show-overflow-tooltip />
        <el-table-column prop="device_sn" label="设备SN" width="130" show-overflow-tooltip />
        <el-table-column prop="registrant" label="登记人" width="90" show-overflow-tooltip />
        <el-table-column prop="refund_operator" label="退款操作人" width="100" show-overflow-tooltip />
        <el-table-column label="质量状态" width="110" fixed="right">
          <template #default="{ row }">
            <el-tag size="small" :type="qualityType(row.quality_status)">{{ qualityLabel(row.quality_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="quality_note" label="质量说明" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-muted">{{ row.quality_note || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.quality_status === 'pending'" size="small" type="primary" link @click="onConfirm(row)">确认通过</el-button>
            <el-button v-if="row.quality_status" size="small" link @click="openEdit(row)">编辑修正</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pag-wrap">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          background
          size="small"
          @current-change="load"
          @size-change="page = 1; load()"
        />
      </div>
    </el-card>

    <!-- 编辑修正弹窗 -->
    <el-dialog v-model="editVisible" title="人工修正" width="480px">
      <el-form label-width="96px" size="default">
        <el-form-item label="登记日期">
          <el-date-picker v-model="editForm.register_time" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
                          placeholder="选择登记日期" style="width: 100%" />
        </el-form-item>
        <el-form-item label="操作时间">
          <el-date-picker v-model="editForm.op_time" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
                          placeholder="选择操作时间" style="width: 100%" clearable />
        </el-form-item>
        <el-form-item label="是否已退款">
          <el-select v-model="editForm.is_refunded" style="width: 100%">
            <el-option label="是" value="是" />
            <el-option label="否" value="否" />
          </el-select>
        </el-form-item>
        <el-form-item label="退款金额">
          <el-input-number v-model="editForm.refund_amount" :precision="2" :step="10" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="editForm.remark" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </el-form>
      <div v-if="editRow?.quality_note" class="quality-note">原质量说明: {{ editRow.quality_note }}</div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onFix">保存修正</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { manualRefundQuality } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

const loading = ref(false)
const saving = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const status = ref('pending') // all/pending/auto_fixed/ok
const summary = reactive({ pending: 0, auto_fixed: 0, ok: 0 })
const editVisible = ref(false)
const editRow = ref(null)
const editForm = reactive({ register_time: '', op_time: '', is_refunded: '是', refund_amount: 0, remark: '' })

const statCards = [
  { key: 'pending', label: '待人工确认', color: '#f56c6c' },
  { key: 'auto_fixed', label: '自动纠错', color: '#e6a23c' },
  { key: 'ok', label: '已确认/正常', color: '#67c23a' },
  { key: 'all', label: '全部记录', color: '#409eff' },
]

function qualityLabel(s) {
  return { pending: '待确认', auto_fixed: '已自动纠错', ok: '已确认', none: '正常' }[s || 'none'] || s
}
function qualityType(s) {
  return { pending: 'danger', auto_fixed: 'warning', ok: 'success', none: 'info' }[s || 'none'] || 'info'
}

function switchStatus(key) {
  status.value = key
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const res = await manualRefundQuality.list({ status: status.value, page: page.value, page_size: pageSize.value })
    const d = res?.data || {}
    list.value = d.list || []
    total.value = d.total || 0
    Object.assign(summary, d.summary || {})
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载质量审核列表失败')
  } finally {
    loading.value = false
  }
}

function onConfirm(row) {
  ElMessageBox.confirm(`确认该记录（${row.order_no}）质量无问题？确认后转为"已确认"。`, '确认通过', { type: 'warning' })
    .then(async () => {
      await manualRefundQuality.confirm(row.id)
      ElMessage.success('已确认通过')
      load()
    })
    .catch(() => {})
}

function openEdit(row) {
  editRow.value = row
  editForm.register_time = row.register_time || ''
  editForm.op_time = row.op_time || ''
  editForm.is_refunded = row.is_refunded || '是'
  editForm.refund_amount = Number(row.refund_amount || 0)
  editForm.remark = row.remark || ''
  editVisible.value = true
}

async function onFix() {
  saving.value = true
  try {
    const payload = {}
    if (editForm.register_time) payload.register_time = editForm.register_time
    if (editForm.op_time) payload.op_time = editForm.op_time
    if (editForm.is_refunded) payload.is_refunded = editForm.is_refunded
    payload.refund_amount = editForm.refund_amount
    payload.remark = editForm.remark
    await manualRefundQuality.fix(editRow.value.id, payload)
    ElMessage.success('已保存修正')
    editVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存修正失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.stat-card {
  border-radius: 8px;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #ebeef5;
  cursor: pointer;
  transition: all 0.2s;
}
.stat-card:hover { border-color: #409eff; }
.stat-active { border-color: #409eff; background: #f0f7ff; }
.stat-label { font-size: 12px; color: #6b7280; }
.stat-value { font-size: 22px; font-weight: 700; margin-top: 2px; }
.pending-text { color: #f56c6c; font-weight: 600; }
.text-muted { color: #909399; }
.pag-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }
.quality-note { font-size: 12px; color: #909399; padding: 8px 12px; background: #f8fafc; border-radius: 6px; }
</style>
