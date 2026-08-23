<template>
  <div class="quality-audit">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin:0">{{ tableLabel }}质量审核</div>
        <div class="page-sub">L2 导入自动校验 · 时间异常/枚举脏值/金额异常/必填缺失 · 待人工确认与修正</div>
      </div>
      <div class="ops">
        <el-button v-if="authStore.isAdmin" :icon="Refresh" :loading="scanning" @click="onScan">重新检测</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 状态统计卡(可点击筛选) -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="12" :sm="6" v-for="c in statCards" :key="c.key">
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
      <el-table :data="list" v-loading="loading" border stripe size="small" max-height="520">
        <el-table-column
          v-for="col in columns"
          :key="col.prop"
          :prop="col.prop"
          :label="col.label"
          :width="col.width"
          :fixed="col.fixed || false"
          :align="col.money ? 'right' : 'left'"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span v-if="col.money">¥{{ formatMoney(row[col.prop]) }}</span>
            <el-tag v-else-if="col.tag" size="small" :type="row[col.prop] === '是' ? 'success' : 'info'">{{ row[col.prop] }}</el-tag>
            <span v-else :class="col.prop === timeField && row.quality_status === 'pending' ? 'pending-text' : ''">
              {{ row[col.prop] || '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="质量状态" width="110" fixed="right">
          <template #default="{ row }">
            <el-tag size="small" :type="qualityType(row.quality_status)">{{ qualityLabel(row.quality_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="quality_note" label="质量说明" min-width="220" show-overflow-tooltip>
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
          small
          @current-change="load"
          @size-change="page = 1; load()"
        />
      </div>
    </el-card>

    <!-- 编辑修正弹窗(按后端 fixable_fields 动态生成) -->
    <el-dialog v-model="editVisible" title="人工修正" width="520px">
      <el-form label-width="100px" size="default">
        <el-form-item v-for="(label, field) in fixableFields" :key="field" :label="label">
          <el-date-picker
            v-if="fieldType(field) === 'datetime'"
            v-model="editForm[field]" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
            placeholder="选择时间" style="width: 100%" clearable
          />
          <el-input-number
            v-else-if="fieldType(field) === 'number'"
            v-model="editForm[field]" :precision="2" :step="10" :min="0" style="width: 100%"
          />
          <el-input v-else v-model="editForm[field]" :rows="field === 'remark' ? 2 : 1" :type="field === 'remark' ? 'textarea' : 'text'" placeholder="可选" />
        </el-form-item>
      </el-form>
      <div class="quality-note" v-if="editRow?.quality_note">原质量说明: {{ editRow.quality_note }}</div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onFix">保存修正</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { qualityList, qualityConfirm, qualityFix, qualityScan, listQualityTables } from '@/api/dataQuality'
import { formatMoney } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const props = defineProps({ table: { type: String, required: true } })
const authStore = useAuthStore()

// ── 表配置(前端展示列) ──
const TABLES = {
  refund: {
    label: '退费单', timeField: 'created_at_raw',
    columns: [
      { prop: 'refund_no', label: '退费单号', width: 165, fixed: true },
      { prop: 'device_sn', label: '设备SN', width: 125 },
      { prop: 'refund_method', label: '退款方式', width: 100 },
      { prop: 'status', label: '状态', width: 80 },
      { prop: 'refund_amount', label: '金额', width: 95, money: true },
      { prop: 'created_at_raw', label: '创建时间', width: 155 },
      { prop: 'operator', label: '操作人', width: 95 },
    ],
  },
  ticket: {
    label: '服务工单', timeField: 'register_time',
    columns: [
      { prop: 'ticket_no', label: '工单号', width: 170, fixed: true },
      { prop: 'device_sn', label: '设备SN', width: 125 },
      { prop: 'service_category', label: '服务类别', width: 90 },
      { prop: 'status', label: '状态', width: 90 },
      { prop: 'register_time', label: '登记时间', width: 155 },
      { prop: 'finish_time', label: '完成时间', width: 155 },
      { prop: 'handler', label: '处理人', width: 95 },
    ],
  },
  wallet: {
    label: '钱包流水', timeField: 'op_time',
    columns: [
      { prop: 'id', label: 'ID', width: 80, fixed: true },
      { prop: 'account_id', label: '账户ID', width: 130 },
      { prop: 'op_type', label: '操作类型', width: 105 },
      { prop: 'op_amount', label: '金额', width: 95, money: true },
      { prop: 'op_time', label: '操作时间', width: 155 },
      { prop: 'operator', label: '操作人', width: 95 },
      { prop: 'device_sn', label: '设备SN', width: 125 },
    ],
  },
  shanmao: {
    label: '闪猫退款', timeField: 'apply_time',
    columns: [
      { prop: 'refund_no', label: '退款单号', width: 165, fixed: true },
      { prop: 'device_sn', label: '设备SN', width: 125 },
      { prop: 'status', label: '状态', width: 90 },
      { prop: 'refund_amount', label: '申请金额', width: 95, money: true },
      { prop: 'actual_amount', label: '实退金额', width: 95, money: true },
      { prop: 'apply_time', label: '申请时间', width: 155 },
      { prop: 'audit_time', label: '审核时间', width: 155 },
    ],
  },
  manual_refund: {
    label: '手动退款', timeField: 'register_time',
    columns: [
      { prop: 'id', label: 'ID', width: 70, fixed: true },
      { prop: 'register_time', label: '登记日期', width: 155 },
      { prop: 'op_time', label: '操作时间', width: 155 },
      { prop: 'is_refunded', label: '已退款', width: 80, tag: true },
      { prop: 'refund_amount', label: '金额', width: 95, money: true },
      { prop: 'order_no', label: '订单号', width: 170 },
      { prop: 'device_sn', label: '设备SN', width: 125 },
      { prop: 'registrant', label: '登记人', width: 85 },
    ],
  },
}

const conf = computed(() => TABLES[props.table] || TABLES.manual_refund)
const tableLabel = computed(() => conf.value.label)
const columns = computed(() => conf.value.columns)
const timeField = computed(() => conf.value.timeField)

const loading = ref(false)
const saving = ref(false)
const scanning = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const status = ref('pending')
const summary = reactive({ pending: 0, auto_fixed: 0, ok: 0 })
const editVisible = ref(false)
const editRow = ref(null)
const editForm = reactive({})
const fixableFields = ref({})

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
function fieldType(f) {
  if (/time|_at$|date$/i.test(f)) return 'datetime'
  if (/amount|price|fee/i.test(f)) return 'number'
  return 'text'
}
function toPicker(v) {
  if (!v) return ''
  return String(v).slice(0, 19).replace('T', ' ')
}

function switchStatus(key) {
  status.value = key
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const [res, tabs] = await Promise.all([
      qualityList(props.table, { status: status.value, page: page.value, page_size: pageSize.value }),
      listQualityTables(),
    ])
    const d = res?.data || {}
    list.value = d.list || []
    total.value = d.total || 0
    Object.assign(summary, d.summary || {})
    const t = (tabs?.data || []).find(x => x.code === props.table)
    if (t?.fixable_fields) fixableFields.value = t.fixable_fields
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载质量审核列表失败')
  } finally {
    loading.value = false
  }
}

function onConfirm(row) {
  ElMessageBox.confirm(`确认该记录质量无问题？确认后转为"已确认"。`, '确认通过', { type: 'warning' })
    .then(async () => {
      await qualityConfirm(props.table, row[pkFieldOf(row)])
      ElMessage.success('已确认通过')
      load()
    })
    .catch(() => {})
}

function pkFieldOf(row) {
  const cfg = TABLES[props.table]
  if (props.table === 'refund' || props.table === 'shanmao') return 'refund_no'
  if (props.table === 'ticket') return 'ticket_no'
  return 'id'
}

function openEdit(row) {
  editRow.value = row
  Object.keys(fixableFields.value).forEach(f => {
    editForm[f] = fieldType(f) === 'datetime' ? toPicker(row[f]) : (row[f] ?? '')
  })
  editVisible.value = true
}

async function onFix() {
  saving.value = true
  try {
    const payload = {}
    Object.keys(fixableFields.value).forEach(f => {
      const v = editForm[f]
      if (v !== '' && v != null) payload[f] = v
    })
    await qualityFix(props.table, editRow.value[pkFieldOf(editRow.value)], payload)
    ElMessage.success('已保存修正')
    editVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存修正失败')
  } finally {
    saving.value = false
  }
}

async function onScan() {
  await ElMessageBox.confirm(`对「${tableLabel.value}」全表重新执行质量检测（数据量大时可能耗时较久），继续？`, '重新检测', { type: 'warning' })
  scanning.value = true
  try {
    const res = await qualityScan(props.table)
    const d = res?.data || {}
    ElMessage.success(`检测完成: 共 ${d.scanned} 条, 发现 ${d.pending} 条待确认`)
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重新检测失败')
  } finally {
    scanning.value = false
  }
}

onMounted(load)
watch(() => props.table, () => { status.value = 'pending'; page.value = 1; load() })
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
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.mb-16 { margin-bottom: 16px; }
.ops { display: flex; gap: 8px; }
</style>
