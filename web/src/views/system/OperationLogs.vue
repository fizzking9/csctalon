<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">操作日志</div>
      <el-button :icon="Refresh" @click="loadData">刷新</el-button>
    </div>

    <el-card class="mb-16">
      <div class="flex gap-8" style="flex-wrap: wrap; align-items: center">
        <el-input v-model="filter.username" placeholder="操作人账号" clearable style="width: 160px" @keyup.enter="loadData" @clear="loadData" />
        <el-select v-model="filter.action" placeholder="操作类型" clearable filterable style="width: 180px" @change="loadData">
          <el-option v-for="a in actions" :key="a" :label="a" :value="a" />
        </el-select>
        <el-select v-model="filter.target_type" placeholder="对象类型" clearable style="width: 160px" @change="loadData">
          <el-option v-for="t in targetTypes" :key="t" :label="targetLabel(t)" :value="t" />
        </el-select>
        <el-select v-model="filter.status_code" placeholder="状态码" clearable style="width: 120px" @change="loadData">
          <el-option label="2xx 成功" :value="200" />
          <el-option label="4xx 客户端错误" :value="400" />
          <el-option label="401 未认证" :value="401" />
          <el-option label="403 无权限" :value="403" />
          <el-option label="404 不存在" :value="404" />
        </el-select>
        <el-date-picker
          v-model="filter.range"
          type="datetimerange"
          range-separator="-"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="width: 360px"
          @change="loadData"
        />
        <el-button type="primary" :icon="Search" @click="loadData">查询</el-button>
        <el-button @click="resetFilter">重置</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="list" border stripe>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作人" width="120">
          <template #default="{ row }">
            <span v-if="row.username">{{ row.username }}</span>
            <span v-else class="text-muted">未登录</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-tag size="small" :type="actionTagType(row.action)">{{ row.action }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="方法" width="70">
          <template #default="{ row }">
            <span :class="methodClass(row.method)">{{ row.method }}</span>
          </template>
        </el-table-column>
        <el-table-column label="路径" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono">{{ row.path }}</span>
          </template>
        </el-table-column>
        <el-table-column label="对象" width="120">
          <template #default="{ row }">
            <span v-if="row.target_type">{{ targetLabel(row.target_type) }}<span v-if="row.target_id" class="text-muted">#{{ row.target_id }}</span></span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.status_code" size="small" :type="statusType(row.status_code)" effect="plain">{{ row.status_code }}</el-tag>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="IP" width="120">
          <template #default="{ row }">
            <span class="mono">{{ row.ip || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="详情" min-width="160">
          <template #default="{ row }">
            <el-popover v-if="row.detail" trigger="hover" width="400" placement="left">
              <template #reference>
                <span class="detail-text">{{ row.detail }}</span>
              </template>
              <pre class="detail-pre">{{ formatDetail(row.detail) }}</pre>
            </el-popover>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ total }} 条</span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="pageSize" :page-sizes="[20, 50, 100]" layout="prev, pager, next, sizes, total" @current-change="loadData" @size-change="onSizeChange" />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { listOperationLogs, listLogActions, listLogTargetTypes } from '@/api/operationLog'
import dayjs from 'dayjs'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const actions = ref([])
const targetTypes = ref([])
const filter = reactive({ username: '', action: '', target_type: '', status_code: null, range: null })

const targetLabelMap = {
  auth: '认证', strategy_doc: '策略文档', task: '专项任务', todo: '待办', user: '用户',
  upload: '上传', data_source: '数据源', person: '人员', device: '设备',
  analysis_view: '分析视图', classification_rule: '分类规则', match: '关联',
}
const targetLabel = (t) => targetLabelMap[t] || t || '-'

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-')

const statusType = (c) => {
  if (c >= 200 && c < 300) return 'success'
  if (c === 401) return 'info'
  if (c === 403) return 'warning'
  if (c >= 400) return 'danger'
  return 'info'
}
const methodClass = (m) => ({
  GET: 'm-get', POST: 'm-post', PUT: 'm-put', DELETE: 'm-delete', PATCH: 'm-patch',
}[m] || '')
const actionTagType = (a) => {
  if (a.includes('登录失败') || a.includes('登录拒绝')) return 'danger'
  if (a.includes('删除')) return 'danger'
  if (a.includes('发布') || a.includes('创建')) return 'success'
  if (a.includes('更新') || a.includes('跟进')) return 'warning'
  return 'info'
}
const formatDetail = (d) => {
  try {
    return JSON.stringify(JSON.parse(d), null, 2)
  } catch {
    return d
  }
}

async function loadOptions() {
  try {
    const [a, t] = await Promise.all([listLogActions(), listLogTargetTypes()])
    actions.value = a.data || []
    targetTypes.value = t.data || []
  } catch {
    // 忽略
  }
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filter.username) params.username = filter.username
    if (filter.action) params.action = filter.action
    if (filter.target_type) params.target_type = filter.target_type
    if (filter.status_code !== null && filter.status_code !== '') params.status_code = filter.status_code
    if (filter.range && filter.range.length === 2) {
      params.start = filter.range[0]
      params.end = filter.range[1]
    }
    const res = await listOperationLogs(params)
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function onSizeChange(size) {
  pageSize.value = size
  page.value = 1
  loadData()
}

function resetFilter() {
  Object.assign(filter, { username: '', action: '', target_type: '', status_code: null, range: null })
  page.value = 1
  loadData()
}

onMounted(() => {
  loadOptions()
  loadData()
})
</script>

<style scoped>
.text-muted {
  color: #c0c4cc;
}
.mono {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
}
.detail-text {
  display: inline-block;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  cursor: pointer;
  font-size: 12px;
  color: #606266;
}
.detail-pre {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 280px;
  overflow-y: auto;
}
.m-get { color: #409eff; font-weight: 600; }
.m-post { color: #67c23a; font-weight: 600; }
.m-put { color: #e6a23c; font-weight: 600; }
.m-delete { color: #f56c6c; font-weight: 600; }
.m-patch { color: #e6a23c; font-weight: 600; }
</style>
