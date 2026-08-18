<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">用户管理</div>
      <el-button v-if="tab === 'list'" type="primary" :icon="Plus" @click="openCreate">新建用户</el-button>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="用户列表" name="list">
        <el-card>
          <div class="mb-16">
            <el-input v-model="keyword" placeholder="搜索用户名/姓名" clearable style="width: 240px" @keyup.enter="loadData" @clear="loadData">
              <template #append>
                <el-button :icon="Search" @click="loadData" />
              </template>
            </el-input>
          </div>
          <el-table :data="list" v-loading="loading" border stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="username" label="账号" width="140" />
            <el-table-column prop="display_name" label="姓名" width="120" />
            <el-table-column label="角色" width="100">
              <template #default="{ row }">
                <el-tag :type="roleType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="group_name" label="所属组" width="120" />
            <el-table-column prop="qw_name" label="企微姓名" width="120" />
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button text type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                <el-button text type="warning" size="small" @click="toggleActive(row)">{{ row.is_active ? '停用' : '启用' }}</el-button>
                <el-button text type="danger" size="small" :disabled="row.username === 'admin'" @click="handleDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="mt-16 flex-between">
            <span>共 {{ total }} 条</span>
            <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="注册审批" name="approval">
        <el-card>
          <div class="mb-16 flex gap-8">
            <el-select v-model="regStatus" style="width: 160px" @change="loadRegistrations">
              <el-option label="待审批" value="pending" />
              <el-option label="全部" value="" />
              <el-option label="已通过" value="approved" />
              <el-option label="已驳回" value="rejected" />
            </el-select>
            <el-button :icon="Refresh" @click="loadRegistrations">刷新</el-button>
          </div>
          <el-table :data="regList" v-loading="regLoading" border stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="desired_username" label="期望账号" width="120" />
            <el-table-column prop="desired_display_name" label="期望姓名" width="110" />
            <el-table-column label="期望角色" width="100">
              <template #default="{ row }">
                <el-tag :type="roleType(row.desired_role)" size="small">{{ roleLabel(row.desired_role) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="desired_group" label="期望分组" width="110" />
            <el-table-column prop="qw_name" label="企微姓名" width="110" />
            <el-table-column label="来源" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ sourceLabel(row.source) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="提交时间" width="160">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <template v-if="row.status === 'pending'">
                  <el-button text type="primary" size="small" @click="openApprove(row)">通过</el-button>
                  <el-button text type="danger" size="small" @click="openReject(row)">驳回</el-button>
                </template>
                <el-tag v-else-if="row.status === 'approved'" type="success" size="small">已通过</el-tag>
                <el-tag v-else type="danger" size="small">已驳回</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用户' : '新建用户'" width="520px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="账号" required>
          <el-input v-model="form.username" :disabled="editing" />
        </el-form-item>
        <el-form-item label="姓名" required>
          <el-input v-model="form.display_name" />
        </el-form-item>
        <el-form-item label="密码" :required="!editing">
          <el-input v-model="form.password" type="password" show-password :placeholder="editing ? '留空则不修改' : ''" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option label="系统管理员" value="admin" />
            <el-option label="主管" value="supervisor" />
            <el-option label="专项组长" value="specialty_lead" />
            <el-option label="专员" value="specialist" />
            <el-option label="普通客服" value="agent" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属组">
          <el-input v-model="form.group_name" placeholder="主管用" />
        </el-form-item>
        <el-form-item label="企微姓名">
          <el-input v-model="form.qw_name" placeholder="用于匹配数据中的'企微-XXX'" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="approveVisible" :title="approveMode ? '通过注册申请' : '驳回注册申请'" width="520px">
      <el-form :model="approveForm" label-width="100px">
        <el-form-item label="期望账号"><el-input :model-value="currentReg?.desired_username" disabled /></el-form-item>
        <el-form-item label="期望姓名"><el-input :model-value="currentReg?.desired_display_name" disabled /></el-form-item>
        <el-form-item label="企微姓名"><el-input :model-value="currentReg?.qw_name" disabled /></el-form-item>
        <template v-if="approveMode">
          <el-form-item label="账号"><el-input v-model="approveForm.assign_username" placeholder="留空则用期望账号" /></el-form-item>
          <el-form-item label="角色">
            <el-select v-model="approveForm.assign_role" style="width: 100%">
              <el-option label="系统管理员" value="admin" />
              <el-option label="主管" value="supervisor" />
              <el-option label="专项组长" value="specialty_lead" />
              <el-option label="专员" value="specialist" />
              <el-option label="普通客服" value="agent" />
            </el-select>
          </el-form-item>
          <el-form-item label="所属组"><el-input v-model="approveForm.assign_group" placeholder="留空则用期望分组" /></el-form-item>
        </template>
        <el-form-item v-else label="驳回原因">
          <el-input v-model="approveForm.reject_reason" type="textarea" :rows="3" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveVisible = false">取消</el-button>
        <el-button :type="approveMode ? 'primary' : 'danger'" :loading="approveSubmitting" @click="handleApprove">{{ approveMode ? '通过' : '驳回' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Refresh } from '@element-plus/icons-vue'
import { listUsers, createUser, updateUser, deleteUser } from '@/api/user'
import { listRegistrations, approveRegistration } from '@/api/wechat'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const keyword = ref('')
const dialogVisible = ref(false)
const submitting = ref(false)
const editing = ref(null)
const form = reactive({ username: '', display_name: '', password: '', role: 'agent', group_name: '', qw_name: '' })

// 注册审批
const tab = ref('list')
const regStatus = ref('pending')
const regList = ref([])
const regLoading = ref(false)
const approveVisible = ref(false)
const approveSubmitting = ref(false)
const approveMode = ref(true)
const currentReg = ref(null)
const approveForm = reactive({ assign_username: '', assign_role: '', assign_group: '', reject_reason: '' })

const roleMap = { admin: '系统管理员', supervisor: '主管', specialty_lead: '专项组长', specialist: '专员', agent: '普通客服' }
const roleLabel = (r) => roleMap[r] || r
const roleType = (r) => ({ admin: 'danger', supervisor: 'warning', specialty_lead: 'primary', specialist: 'success', agent: '' }[r] || '')
const sourceMap = { miniapp: '小程序', oa: '公众号', qr: '扫码登录' }
const sourceLabel = (s) => sourceMap[s] || s || '-'
const fmtTime = (t) => (t ? String(t).slice(0, 16).replace('T', ' ') : '-')

async function loadData() {
  loading.value = true
  try {
    const res = await listUsers({ page: page.value, page_size: 20, keyword: keyword.value })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { username: '', display_name: '', password: '', role: 'agent', group_name: '', qw_name: '' })
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, { username: row.username, display_name: row.display_name, password: '', role: row.role, group_name: row.group_name, qw_name: row.qw_name })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!form.username.trim() || !form.display_name.trim()) {
    ElMessage.warning('请填写账号和姓名')
    return
  }
  if (!editing.value && !form.password) {
    ElMessage.warning('请设置密码')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      const payload = { display_name: form.display_name, role: form.role, group_name: form.group_name, qw_name: form.qw_name }
      if (form.password) payload.password = form.password
      await updateUser(editing.value.id, payload)
    } else {
      await createUser({ username: form.username, display_name: form.display_name, password: form.password, role: form.role, group_name: form.group_name, qw_name: form.qw_name })
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function toggleActive(row) {
  await updateUser(row.id, { is_active: !row.is_active })
  ElMessage.success(row.is_active ? '已停用' : '已启用')
  loadData()
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除用户 ${row.display_name}?`, '提示', { type: 'warning' })
  await deleteUser(row.id)
  ElMessage.success('已删除')
  loadData()
}

async function loadRegistrations() {
  regLoading.value = true
  try {
    const res = await listRegistrations(regStatus.value ? { status: regStatus.value } : {})
    regList.value = res || []
  } finally {
    regLoading.value = false
  }
}

function openApprove(row) {
  currentReg.value = row
  approveMode.value = true
  Object.assign(approveForm, { assign_username: row.desired_username || '', assign_role: row.desired_role || '', assign_group: row.desired_group || '', reject_reason: '' })
  approveVisible.value = true
}

function openReject(row) {
  currentReg.value = row
  approveMode.value = false
  Object.assign(approveForm, { assign_username: '', assign_role: '', assign_group: '', reject_reason: '' })
  approveVisible.value = true
}

async function handleApprove() {
  const reg = currentReg.value
  if (!reg) return
  approveSubmitting.value = true
  try {
    if (approveMode.value) {
      const res = await approveRegistration(reg.id, {
        approved: true,
        assign_role: approveForm.assign_role || undefined,
        assign_group: approveForm.assign_group || undefined,
        assign_username: approveForm.assign_username || undefined,
      })
      ElMessage.success(`已通过,初始密码:${res.initial_password || '(见返回)'}`)
    } else {
      await approveRegistration(reg.id, { approved: false, reject_reason: approveForm.reject_reason || undefined })
      ElMessage.success('已驳回')
    }
    approveVisible.value = false
    loadRegistrations()
  } finally {
    approveSubmitting.value = false
  }
}

onMounted(() => {
  loadData()
  loadRegistrations()
})
</script>
