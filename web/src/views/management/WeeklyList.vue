<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">周报看板</div>
        <div class="page-sub">可编辑看板框架 · 视图+摘要组合 · 确认后固化,可持续注释/下钻审计</div>
      </div>
      <div class="flex gap-8">
        <el-button :icon="Files" @click="$router.push('/management/templates')">看板模板</el-button>
        <el-button type="success" :icon="MagicStick" :loading="generating" @click="handleGenerate">一键生成本周</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">手动新建</el-button>
      </div>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="resetAndLoad">
          <el-option v-for="(label, k) in statusMap" :key="k" :label="label" :value="k" />
        </el-select>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table :data="list" v-loading="loading" border stripe @row-click="goDetail" style="cursor: pointer">
        <el-table-column prop="name" label="周报看板名" show-overflow-tooltip min-width="220" />
        <el-table-column label="周次" width="220">
          <template #default="{ row }">{{ formatDate(row.week_start) }} ~ {{ formatDate(row.week_end) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusMap[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="summary" label="总评" show-overflow-tooltip min-width="160" />
        <el-table-column prop="confirmed_by" label="确认人" width="100" />
        <el-table-column label="确认时间" width="160">
          <template #default="{ row }">{{ formatTime(row.confirmed_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="goDetail(row)">查看</el-button>
            <el-button v-if="row.status === 'draft'" link type="danger" @click.stop="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 手动新建对话框 -->
    <el-dialog v-model="dialogVisible" title="新建周报看板(草稿)" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="看板名" required>
          <el-input v-model="form.name" placeholder="如 2026-W30 客服中心周报" />
        </el-form-item>
        <el-form-item label="周次范围">
          <el-date-picker
            v-model="form.range"
            type="daterange"
            range-separator="至"
            start-placeholder="周一"
            end-placeholder="周日"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="总评">
          <el-input v-model="form.summary" type="textarea" :rows="2" placeholder="本周总体情况概述(可选)" />
        </el-form-item>
      </el-form>
      <div class="page-sub mb-16">创建后从默认周报模板实例化空白框架,可在详情页逐项填写/增删分项。推荐使用"一键生成本周"自动从默认模板生成。</div>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, MagicStick, Files } from '@element-plus/icons-vue'
import {
  listWeeklyReports,
  createWeeklyReport,
  deleteWeeklyReport,
  generateWeeklyReport,
} from '@/api/weekly'
import dayjs from 'dayjs'

const router = useRouter()
const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ status: '' })
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ name: '', range: null, summary: '' })
const generating = ref(false)

const statusMap = { draft: '草稿', confirmed: '已确认', archived: '已归档' }
const statusType = (s) => ({ draft: 'info', confirmed: 'success', archived: 'warning' }[s] || 'info')
const formatDate = (t) => (t ? dayjs(t).format('YY/MM/DD') : '-')
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

async function loadData() {
  loading.value = true
  try {
    const res = await listWeeklyReports({ page: page.value, page_size: 20, status: filter.status || undefined })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function resetAndLoad() {
  page.value = 1
  loadData()
}

function goDetail(row) {
  router.push(`/management/weekly/${row.id}`)
}

function openCreate() {
  Object.assign(form, { name: '', range: null, summary: '' })
  dialogVisible.value = true
}

async function handleCreate() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入看板名')
    return
  }
  submitting.value = true
  try {
    await createWeeklyReport({
      name: form.name,
      week_start: form.range && form.range[0] ? form.range[0] + 'T00:00:00' : null,
      week_end: form.range && form.range[1] ? form.range[1] + 'T23:59:59' : null,
      summary: form.summary || null,
    })
    ElMessage.success('创建成功')
    dialogVisible.value = false
    loadData()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确认删除该周报?', '提示', { type: 'warning' })
  await deleteWeeklyReport(row.id)
  ElMessage.success('已删除')
  loadData()
}

async function handleGenerate() {
  generating.value = true
  try {
    const res = await generateWeeklyReport()
    ElMessage.success(res.message || '已生成本周周报看板')
    loadData()
    if (res.data?.id) router.push(`/management/weekly/${res.data.id}`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '生成失败')
  } finally {
    generating.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.mt-16 {
  margin-top: 16px;
}
.mb-16 {
  margin-bottom: 16px;
}
</style>
