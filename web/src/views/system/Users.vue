<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">用户管理</div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建用户</el-button>
    </div>

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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import { listUsers, createUser, updateUser, deleteUser } from '@/api/user'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const keyword = ref('')
const dialogVisible = ref(false)
const submitting = ref(false)
const editing = ref(null)
const form = reactive({ username: '', display_name: '', password: '', role: 'agent', group_name: '', qw_name: '' })

const roleMap = { admin: '系统管理员', supervisor: '主管', specialty_lead: '专项组长', specialist: '专员', agent: '普通客服' }
const roleLabel = (r) => roleMap[r] || r
const roleType = (r) => ({ admin: 'danger', supervisor: 'warning', specialty_lead: 'primary', specialist: 'success', agent: '' }[r] || '')

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

onMounted(loadData)
</script>
