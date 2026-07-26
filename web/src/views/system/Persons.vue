<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">人员统一管理</div>
      <div class="flex gap-8">
        <el-button :icon="Refresh" :loading="refreshing" @click="handleRefresh">提取别名</el-button>
        <el-button :icon="Connection" @click="loadSuggestions">归并建议</el-button>
      </div>
    </div>

    <!-- 待归并别名面板 -->
    <el-card class="mb-16" v-if="unmergedAliases.length">
      <template #header>
        <div class="flex-between">
          <span>待归并别名 ({{ unmergedAliases.length }})</span>
          <div class="flex gap-8">
            <el-button size="small" type="primary" :disabled="!selectedAliasIds.length" @click="openMergeToNew">
              归并到新建人员 ({{ selectedAliasIds.length }})
            </el-button>
            <el-button size="small" :disabled="!selectedAliasIds.length" @click="openMergeToExisting">
              归并到已有人员
            </el-button>
          </div>
        </div>
      </template>
      <el-table :data="unmergedAliases" size="small" max-height="300" border @selection-change="onAliasSelect">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="alias" label="别名原值" />
        <el-table-column prop="alias_normalized" label="归一化" width="160" />
        <el-table-column prop="source_table" label="来源表" width="140" />
        <el-table-column prop="source_field" label="来源字段" width="100" />
        <el-table-column label="系统" width="60">
          <template #default="{ row }">
            <el-tag v-if="row.is_system" type="info" size="small">sys</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 人员列表 -->
    <el-card>
      <div class="mb-16 flex-between">
        <el-radio-group v-model="filter.status" @change="loadData">
          <el-radio-button value="unconfirmed">待确认</el-radio-button>
          <el-radio-button value="confirmed">已确认</el-radio-button>
          <el-radio-button value="inactive">已停用</el-radio-button>
        </el-radio-group>
        <span>共 {{ total }} 条</span>
      </div>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="display_name" label="显示名" width="140" />
        <el-table-column prop="alias_count" label="别名数" width="80" />
        <el-table-column label="角色" width="80">
          <template #default="{ row }">{{ roleLabels[row.role] || row.role }}</template>
        </el-table-column>
        <el-table-column prop="group_name" label="组" width="100" />
        <el-table-column label="确认" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_confirmed ? 'success' : 'warning'" size="small">
              {{ row.is_confirmed ? '已确认' : '待确认' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="60">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button text type="success" size="small" @click="confirmPerson(row)">确认归并</el-button>
            <el-button text type="warning" size="small" @click="toggleActive(row)">{{ row.is_active ? '停用' : '启用' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span></span>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 人员详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="`${currentPerson?.display_name} - 详情`" size="60%">
      <div v-if="currentDetail">
        <el-descriptions :column="2" border class="mb-16">
          <el-descriptions-item label="显示名">{{ currentDetail.display_name }}</el-descriptions-item>
          <el-descriptions-item label="别名数">{{ currentDetail.alias_count }}</el-descriptions-item>
          <el-descriptions-item label="角色">{{ roleLabels[currentDetail.role] || currentDetail.role }}</el-descriptions-item>
          <el-descriptions-item label="组">{{ currentDetail.group_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="确认状态">
            <el-tag :type="currentDetail.is_confirmed ? 'success' : 'warning'" size="small">
              {{ currentDetail.is_confirmed ? '已确认' : '待确认' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="备注">{{ currentDetail.remark || '-' }}</el-descriptions-item>
        </el-descriptions>

        <div class="flex-between mb-8">
          <span class="page-title" style="margin: 0; font-size: 16px">别名列表</span>
          <el-button size="small" type="warning" :disabled="!selectedDetailAliasIds.length" @click="handleSplit">
            拆出选中 ({{ selectedDetailAliasIds.length }})
          </el-button>
        </div>
        <el-table :data="currentDetail.aliases" size="small" border @selection-change="onDetailAliasSelect">
          <el-table-column type="selection" width="40" :selectable="(row) => !row.is_system" />
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="alias" label="别名原值" />
          <el-table-column prop="source_table" label="来源表" width="140" />
          <el-table-column prop="source_field" label="来源字段" width="100" />
          <el-table-column label="系统" width="60">
            <template #default="{ row }">
              <el-tag v-if="row.is_system" type="info" size="small">sys</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>

    <!-- 归并到新建人员 对话框 -->
    <el-dialog v-model="mergeNewVisible" title="归并到新建人员" width="480px">
      <el-form label-width="100px">
        <el-form-item label="已选别名">
          <span>{{ selectedAliasIds.length }} 个</span>
        </el-form-item>
        <el-form-item label="显示名" required>
          <el-input v-model="mergeNewForm.display_name" placeholder="归并后的统一显示名" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="mergeNewForm.role" style="width: 100%">
            <el-option label="客服" value="agent" />
            <el-option label="主管" value="supervisor" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="组">
          <el-input v-model="mergeNewForm.group_name" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeNewVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleMergeToNew">确认归并</el-button>
      </template>
    </el-dialog>

    <!-- 归并到已有人员 对话框 -->
    <el-dialog v-model="mergeExistVisible" title="归并到已有人员" width="480px">
      <el-form label-width="100px">
        <el-form-item label="已选别名">
          <span>{{ selectedAliasIds.length }} 个</span>
        </el-form-item>
        <el-form-item label="目标人员" required>
          <el-select v-model="mergeExistTargetId" filterable placeholder="选择目标人员" style="width: 100%">
            <el-option v-for="p in confirmedPersons" :key="p.id" :label="`${p.display_name} (${p.alias_count}别名)`" :value="p.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeExistVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleMergeToExisting">确认归并</el-button>
      </template>
    </el-dialog>

    <!-- 归并建议 对话框 -->
    <el-dialog v-model="suggestionsVisible" title="归并建议(归一化重复的别名组)" width="700px">
      <div v-if="suggestions.length">
        <el-card v-for="(group, gi) in suggestions" :key="gi" class="mb-12" shadow="never">
          <template #header>
            <div class="flex-between">
              <span>{{ group.normalized }} ({{ group.aliases.length }} 个别名)</span>
              <el-button size="small" type="primary" @click="quickMerge(group)">一键归并</el-button>
            </div>
          </template>
          <el-table :data="group.aliases" size="small" border>
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="alias" label="别名原值" />
            <el-table-column prop="source_table" label="来源表" width="140" />
            <el-table-column prop="person_id" label="当前人员" width="100" />
          </el-table>
        </el-card>
      </div>
      <el-empty v-else description="暂无归并建议(所有别名归一化值唯一)" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Connection } from '@element-plus/icons-vue'
import {
  listPersons,
  getPerson,
  updatePerson,
  mergeAliases,
  mergeToNew,
  splitAliases,
  listUnmergedAliases,
  listMergeSuggestions,
  refreshAliases,
} from '@/api/person'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const filter = reactive({ status: 'unconfirmed' })
const refreshing = ref(false)
const submitting = ref(false)

// 待归并别名
const unmergedAliases = ref([])
const selectedAliasIds = ref([])

// 详情
const detailVisible = ref(false)
const currentPerson = ref(null)
const currentDetail = ref(null)
const selectedDetailAliasIds = ref([])

// 归并对话框
const mergeNewVisible = ref(false)
const mergeExistVisible = ref(false)
const mergeExistTargetId = ref(null)
const confirmedPersons = ref([])
const mergeNewForm = reactive({ display_name: '', role: 'agent', group_name: '' })

// 归并建议
const suggestionsVisible = ref(false)
const suggestions = ref([])

const roleLabels = { admin: '管理员', supervisor: '主管', agent: '客服' }

async function loadData() {
  loading.value = true
  try {
    const res = await listPersons({ page: page.value, page_size: 20, status: filter.status })
    list.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function loadUnmerged() {
  const res = await listUnmergedAliases()
  unmergedAliases.value = res.data
}

function onAliasSelect(rows) {
  selectedAliasIds.value = rows.map((r) => r.id)
}

function onDetailAliasSelect(rows) {
  selectedDetailAliasIds.value = rows.map((r) => r.id)
}

async function openDetail(row) {
  currentPerson.value = row
  detailVisible.value = true
  currentDetail.value = null
  const res = await getPerson(row.id)
  currentDetail.value = res.data
}

async function confirmPerson(row) {
  await updatePerson(row.id, { is_confirmed: true })
  ElMessage.success('已确认归并')
  loadData()
}

async function toggleActive(row) {
  await updatePerson(row.id, { is_active: !row.is_active })
  ElMessage.success(row.is_active ? '已停用' : '已启用')
  loadData()
}

// 归并到新建
function openMergeToNew() {
  mergeNewForm.display_name = ''
  mergeNewForm.role = 'agent'
  mergeNewForm.group_name = ''
  mergeNewVisible.value = true
}

async function handleMergeToNew() {
  if (!mergeNewForm.display_name.trim()) {
    ElMessage.warning('请填写显示名')
    return
  }
  submitting.value = true
  try {
    await mergeToNew(mergeNewForm.display_name.trim(), selectedAliasIds.value)
    ElMessage.success('归并成功')
    mergeNewVisible.value = false
    selectedAliasIds.value = []
    loadData()
    loadUnmerged()
  } finally {
    submitting.value = false
  }
}

// 归并到已有
async function openMergeToExisting() {
  // 加载已确认人员列表作为目标
  const res = await listPersons({ page: 1, page_size: 100, status: 'confirmed' })
  confirmedPersons.value = res.data.items
  mergeExistTargetId.value = null
  mergeExistVisible.value = true
}

async function handleMergeToExisting() {
  if (!mergeExistTargetId.value) {
    ElMessage.warning('请选择目标人员')
    return
  }
  submitting.value = true
  try {
    await mergeAliases(mergeExistTargetId.value, selectedAliasIds.value)
    ElMessage.success('归并成功')
    mergeExistVisible.value = false
    selectedAliasIds.value = []
    loadData()
    loadUnmerged()
  } finally {
    submitting.value = false
  }
}

// 拆分
async function handleSplit() {
  if (!currentPerson.value) return
  await ElMessageBox.confirm(`确认从 ${currentPerson.value.display_name} 拆出 ${selectedDetailAliasIds.value.length} 个别名?`, '提示', { type: 'warning' })
  submitting.value = true
  try {
    await splitAliases(currentPerson.value.id, selectedDetailAliasIds.value)
    ElMessage.success('拆分成功')
    selectedDetailAliasIds.value = []
    // 刷新详情
    const res = await getPerson(currentPerson.value.id)
    currentDetail.value = res.data
    loadData()
  } finally {
    submitting.value = false
  }
}

// 归并建议
async function loadSuggestions() {
  const res = await listMergeSuggestions()
  suggestions.value = res.data
  suggestionsVisible.value = true
}

async function quickMerge(group) {
  const aliasIds = group.aliases.map((a) => a.id)
  const displayName = group.aliases[0].alias
  await ElMessageBox.confirm(`将 ${aliasIds.length} 个别名归并为 "${displayName}"?`, '一键归并', { type: 'warning' })
  submitting.value = true
  try {
    await mergeToNew(displayName, aliasIds)
    ElMessage.success('归并成功')
    // 刷新建议
    const res = await listMergeSuggestions()
    suggestions.value = res.data
    loadData()
    loadUnmerged()
  } finally {
    submitting.value = false
  }
}

async function handleRefresh() {
  refreshing.value = true
  try {
    const res = await refreshAliases()
    ElMessage.success(`提取完成: ${JSON.stringify(res.data)}`)
    loadData()
    loadUnmerged()
  } finally {
    refreshing.value = false
  }
}

onMounted(() => {
  loadData()
  loadUnmerged()
})
</script>
