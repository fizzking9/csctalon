<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">排班管理</div>
        <div class="page-sub">企业微信排班对接同步 · 系统内顶班/请假微调 · 排班×接待×工单对照</div>
      </div>
      <div class="flex gap-8">
        <el-button type="primary" :icon="Plus" @click="openBulk">批量排班</el-button>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="排班列表" name="list">
        <el-card class="mb-16">
          <div class="flex gap-8">
            <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束" style="width: 260px" @change="loadSchedules" />
            <el-select v-model="personFilter" placeholder="按人员" clearable filterable style="width: 180px" @change="loadSchedules">
              <el-option v-for="p in persons" :key="p.id" :label="p.display_name" :value="p.id" />
            </el-select>
          </div>
        </el-card>
        <el-card>
          <el-table :data="schedules" v-loading="loading" border stripe>
            <el-table-column prop="schedule_date" label="日期" width="120" />
            <el-table-column prop="person_name" label="人员" width="120" />
            <el-table-column prop="shift_name" label="班次" width="100" />
            <el-table-column label="时间" width="140">
              <template #default="{ row }">{{ row.start_time || '-' }} ~ {{ row.end_time || '-' }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="note" label="备注" show-overflow-tooltip />
            <el-table-column label="操作" width="140" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openEdit(row)">微调</el-button>
                <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="排班对照" name="comparison">
        <el-card class="mb-16">
          <div class="flex gap-8">
            <el-date-picker v-model="cmpDate" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" @change="loadComparison" />
            <el-button :icon="Refresh" @click="loadComparison">查询</el-button>
          </div>
        </el-card>
        <el-card>
          <div class="flex gap-16 mb-16">
            <div class="cmp-chip"><span>排班人数</span><b>{{ cmp.scheduled_count ?? '-' }}</b></div>
            <div class="cmp-chip"><span>实际接待人数</span><b>{{ cmp.active_reception_count ?? '-' }}</b></div>
          </div>
          <el-table :data="cmp.detail || []" border stripe>
            <el-table-column prop="person_name" label="人员" width="120" />
            <el-table-column prop="shift_name" label="班次" width="100" />
            <el-table-column label="时间" width="140">
              <template #default="{ row }">{{ row.start_time || '-' }} ~ {{ row.end_time || '-' }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="90" />
            <el-table-column prop="reception_count" label="接待量" width="90" align="right" />
            <el-table-column prop="ticket_count" label="工单量" width="90" align="right" />
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 批量排班 -->
    <el-dialog v-model="bulkVisible" title="批量排班" width="560px">
      <el-form label-width="90px">
        <el-form-item label="人员">
          <el-select v-model="bulkForm.person_ids" multiple filterable placeholder="选择人员" style="width: 100%">
            <el-option v-for="p in persons" :key="p.id" :label="p.display_name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期范围">
          <el-date-picker v-model="bulkForm.range" type="daterange" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="班次">
          <el-select v-model="bulkForm.shift_template_id" placeholder="选择班次" style="width: 100%">
            <el-option v-for="s in shifts" :key="s.id" :label="s.name + ' (' + s.start_time + '-' + s.end_time + ')'" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="bulkForm.status" style="width: 100%">
            <el-option v-for="s in ['正常', '顶班', '请假', '调班']" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="bulkForm.note" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bulkVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleBulk">提交</el-button>
      </template>
    </el-dialog>

    <!-- 微调 -->
    <el-dialog v-model="editVisible" title="排班微调" width="440px">
      <el-form label-width="90px">
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option v-for="s in ['正常', '顶班', '请假', '调班']" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="editForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listShifts, listSchedules, bulkSchedule, updateSchedule, deleteSchedule, scheduleComparison } from '@/api/schedule'
import { listPersons } from '@/api/person'
import dayjs from 'dayjs'

const tab = ref('list')
const shifts = ref([])
const persons = ref([])
const schedules = ref([])
const loading = ref(false)
const submitting = ref(false)
const range = ref([dayjs().format('YYYY-MM-DD'), dayjs().format('YYYY-MM-DD')])
const personFilter = ref(null)
const bulkVisible = ref(false)
const editVisible = ref(false)
const cmpDate = ref(dayjs().format('YYYY-MM-DD'))
const cmp = ref({})
const bulkForm = reactive({ person_ids: [], range: [], shift_template_id: null, status: '正常', note: '' })
const editForm = reactive({ id: null, status: '正常', note: '' })

const statusType = (s) => ({ 正常: 'success', 顶班: 'warning', 请假: 'info', 调班: 'primary' }[s] || 'info')

async function loadShifts() {
  const res = await listShifts()
  shifts.value = res.data || []
}

async function loadPersons() {
  const res = await listPersons({ page: 1, page_size: 200 })
  persons.value = (res.data && res.data.items) || []
}

async function loadSchedules() {
  if (!range.value || range.value.length !== 2) return
  loading.value = true
  try {
    const params = { start: range.value[0], end: range.value[1] }
    if (personFilter.value) params.person_id = personFilter.value
    const res = await listSchedules(params)
    schedules.value = res.data || []
  } finally {
    loading.value = false
  }
}

async function loadComparison() {
  const res = await scheduleComparison({ date: cmpDate.value })
  cmp.value = res.data || {}
}

function openBulk() {
  bulkForm.person_ids = []
  bulkForm.range = [dayjs().format('YYYY-MM-DD'), dayjs().format('YYYY-MM-DD')]
  bulkForm.shift_template_id = shifts.value[0]?.id || null
  bulkForm.status = '正常'
  bulkForm.note = ''
  bulkVisible.value = true
}

async function handleBulk() {
  if (!bulkForm.person_ids.length || !bulkForm.range || bulkForm.range.length !== 2) {
    ElMessage.warning('请选择人员和日期范围')
    return
  }
  submitting.value = true
  try {
    await bulkSchedule({
      person_ids: bulkForm.person_ids,
      start: bulkForm.range[0],
      end: bulkForm.range[1],
      shift_template_id: bulkForm.shift_template_id,
      status: bulkForm.status,
      note: bulkForm.note || null,
    })
    ElMessage.success('排班完成')
    bulkVisible.value = false
    loadSchedules()
  } finally {
    submitting.value = false
  }
}

function openEdit(row) {
  editForm.id = row.id
  editForm.status = row.status
  editForm.note = row.note || ''
  editVisible.value = true
}

async function handleEdit() {
  submitting.value = true
  try {
    await updateSchedule(editForm.id, { status: editForm.status, note: editForm.note })
    ElMessage.success('已更新')
    editVisible.value = false
    loadSchedules()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm('确认删除该排班?', '提示', { type: 'warning' })
  await deleteSchedule(row.id)
  ElMessage.success('已删除')
  loadSchedules()
}

function loadAll() {
  loadShifts()
  loadPersons()
  loadSchedules()
}

onMounted(loadAll)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.gap-16 { gap: 16px; }
.cmp-chip { border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 16px; display: flex; flex-direction: column; gap: 4px; min-width: 120px; }
.cmp-chip span { font-size: 12px; color: #909399; }
.cmp-chip b { font-size: 24px; }
</style>
