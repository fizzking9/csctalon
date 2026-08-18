<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">发票登记</div>
        <div class="page-sub">上传电子发票二维码/PDF/图片 · 自动识别 · 汇总报销 · 导出清单</div>
      </div>
      <div class="flex gap-8">
        <el-button type="primary" :icon="Plus" @click="openInvoice">登记发票</el-button>
        <el-button :icon="Plus" @click="openReimburse">新建报销单</el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="发票" name="invoices">
        <el-card class="mb-16">
          <div class="flex gap-8">
            <el-select v-model="invStatus" placeholder="状态" clearable style="width: 140px" @change="loadInvoices">
              <el-option v-for="s in ['待识别', '已识别', '待核对', '已入账', '已报销']" :key="s" :label="s" :value="s" />
            </el-select>
            <el-button :icon="Refresh" @click="loadInvoices">刷新</el-button>
          </div>
        </el-card>
        <el-card>
          <el-table :data="invoices" v-loading="loading" border stripe @row-click="openEditInvoice" style="cursor: pointer">
            <el-table-column prop="invoice_no" label="发票号码" width="160" />
            <el-table-column prop="invoice_type" label="类型" width="80" />
            <el-table-column prop="seller_name" label="销方" show-overflow-tooltip />
            <el-table-column label="价税合计" width="110" align="right">
              <template #default="{ row }">¥{{ fmt(row.total_amount) }}</template>
            </el-table-column>
            <el-table-column label="开票日期" width="110">
              <template #default="{ row }">{{ row.invoice_date ? String(row.invoice_date).slice(0, 10) : '-' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag :type="invStatusType(row.status)" size="small">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="center">
              <template #default="{ row }"><el-button link type="danger" @click.stop="handleDeleteInvoice(row)">删除</el-button></template>
            </el-table-column>
          </el-table>
          <div class="mt-16 flex-between">
            <span>共 {{ invTotal }} 条</span>
            <el-pagination v-model:current-page="invPage" :total="invTotal" :page-size="20" layout="prev, pager, next" @current-change="loadInvoices" />
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="报销单" name="reimburse">
        <el-card>
          <el-table :data="reimbursements" v-loading="rbLoading" border stripe>
            <el-table-column prop="batch_no" label="报销单号" width="200" />
            <el-table-column prop="title" label="标题" show-overflow-tooltip />
            <el-table-column label="总金额" width="110" align="right">
              <template #default="{ row }">¥{{ fmt(row.total_amount) }}</template>
            </el-table-column>
            <el-table-column prop="invoice_count" label="张数" width="70" align="right" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag :type="rbStatusType(row.status)" size="small">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="180" align="center">
              <template #default="{ row }">
                <el-button v-if="row.status === '待审核'" link type="primary" @click="handleApprove(row)">审核通过</el-button>
                <el-button link type="primary" @click="handleExport(row)">导出</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 登记发票 -->
    <el-dialog v-model="invVisible" title="登记发票" width="560px">
      <el-alert type="info" :closable="false" class="mb-8" show-icon>
        粘贴电子发票二维码内容(全电发票)可自动识别; 或手工填写字段。
      </el-alert>
      <el-form :model="invForm" label-width="90px">
        <el-form-item label="二维码内容">
          <el-input v-model="invForm.qr_text" type="textarea" :rows="3" placeholder="粘贴发票二维码识别出的文本" />
        </el-form-item>
        <el-form-item label="发票号码"><el-input v-model="invForm.invoice_no" /></el-form-item>
        <el-form-item label="发票代码"><el-input v-model="invForm.invoice_code" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="invForm.invoice_type" style="width: 100%">
            <el-option v-for="t in ['全电', '纸质', '普票', '专票']" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="销方"><el-input v-model="invForm.seller_name" /></el-form-item>
        <el-form-item label="购方"><el-input v-model="invForm.buyer_name" /></el-form-item>
        <el-form-item label="金额"><el-input-number v-model="invForm.amount" :min="0" :precision="2" style="width: 100%" /></el-form-item>
        <el-form-item label="税额"><el-input-number v-model="invForm.tax_amount" :min="0" :precision="2" style="width: 100%" /></el-form-item>
        <el-form-item label="价税合计"><el-input-number v-model="invForm.total_amount" :min="0" :precision="2" style="width: 100%" /></el-form-item>
        <el-form-item label="开票日期"><el-date-picker v-model="invForm.invoice_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="invForm.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="invVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreateInvoice">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新建报销单 -->
    <el-dialog v-model="rbVisible" title="新建报销单" width="560px">
      <el-form label-width="90px">
        <el-form-item label="标题"><el-input v-model="rbForm.title" /></el-form-item>
        <el-form-item label="选择发票">
          <el-select v-model="rbForm.invoice_ids" multiple filterable placeholder="选择未报销发票" style="width: 100%">
            <el-option v-for="i in payableInvoices" :key="i.id" :label="(i.invoice_no || '无号') + ' ¥' + fmt(i.total_amount)" :value="i.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="rbForm.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rbVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreateRb">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listInvoices, createInvoice, updateInvoice, deleteInvoice, listReimbursements, createReimbursement, approveReimbursement, exportReimbursement } from '@/api/invoice'

const tab = ref('invoices')
const invoices = ref([])
const reimbursements = ref([])
const loading = ref(false)
const rbLoading = ref(false)
const submitting = ref(false)
const invTotal = ref(0)
const invPage = ref(1)
const invStatus = ref('')
const invVisible = ref(false)
const rbVisible = ref(false)
const invForm = reactive({ qr_text: '', invoice_no: '', invoice_code: '', invoice_type: '全电', seller_name: '', buyer_name: '', amount: null, tax_amount: null, total_amount: null, invoice_date: null, remark: '' })
const rbForm = reactive({ title: '', invoice_ids: [], remark: '' })
const fmt = (v) => (v == null ? '0.00' : Number(v).toFixed(2))
const invStatusType = (s) => ({ 待识别: 'info', 已识别: 'success', 待核对: 'warning', 已入账: 'primary', 已报销: 'success' }[s] || 'info')
const rbStatusType = (s) => ({ 草稿: 'info', 待审核: 'warning', 已通过: 'primary', 已报销: 'success' }[s] || 'info')

const payableInvoices = computed(() => invoices.value.filter((i) => i.status !== '已报销' && i.status !== '已入账'))

async function loadInvoices() {
  loading.value = true
  try {
    const params = { page: invPage.value, page_size: 20, status: invStatus.value || undefined }
    const res = await listInvoices(params)
    invoices.value = res.data.items || []
    invTotal.value = res.data.total || 0
  } finally {
    loading.value = false
  }
}

async function loadReimbursements() {
  rbLoading.value = true
  try {
    const res = await listReimbursements({ page: 1, page_size: 50 })
    reimbursements.value = res.data.items || []
  } finally {
    rbLoading.value = false
  }
}

function openInvoice() {
  Object.assign(invForm, { qr_text: '', invoice_no: '', invoice_code: '', invoice_type: '全电', seller_name: '', buyer_name: '', amount: null, tax_amount: null, total_amount: null, invoice_date: null, remark: '' })
  invVisible.value = true
}

async function handleCreateInvoice() {
  submitting.value = true
  try {
    await createInvoice({ ...invForm })
    ElMessage.success('登记成功')
    invVisible.value = false
    loadInvoices()
  } finally {
    submitting.value = false
  }
}

function openEditInvoice(row) {
  Object.assign(invForm, { qr_text: '', invoice_no: row.invoice_no, invoice_code: row.invoice_code, invoice_type: row.invoice_type, seller_name: row.seller_name, buyer_name: row.buyer_name, amount: row.amount, tax_amount: row.tax_amount, total_amount: row.total_amount, invoice_date: row.invoice_date ? String(row.invoice_date).slice(0, 10) : null, remark: row.remark })
  invForm._id = row.id
  invVisible.value = true
}

async function handleDeleteInvoice(row) {
  await ElMessageBox.confirm('确认删除该发票?', '提示', { type: 'warning' })
  await deleteInvoice(row.id)
  ElMessage.success('已删除')
  loadInvoices()
}

function openReimburse() {
  rbForm.title = ''
  rbForm.invoice_ids = []
  rbForm.remark = ''
  rbVisible.value = true
  loadInvoices()
}

async function handleCreateRb() {
  if (!rbForm.invoice_ids.length) {
    ElMessage.warning('请选择发票')
    return
  }
  submitting.value = true
  try {
    await createReimbursement({ title: rbForm.title, invoice_ids: rbForm.invoice_ids, remark: rbForm.remark })
    ElMessage.success('报销单已创建')
    rbVisible.value = false
    loadReimbursements()
    loadInvoices()
  } finally {
    submitting.value = false
  }
}

async function handleApprove(row) {
  await approveReimbursement(row.id, { status: '已通过' })
  ElMessage.success('已通过')
  loadReimbursements()
}

async function handleExport(row) {
  const res = await exportReimbursement(row.id)
  const blob = new Blob([res], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `reimbursement_${row.batch_no}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  loadInvoices()
  loadReimbursements()
})
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.mb-8 { margin-bottom: 8px; }
.mt-16 { margin-top: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
</style>
