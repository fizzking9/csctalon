<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">排班管理</div>
        <div class="page-sub">企业微信排班对接同步 · 系统内顶班/请假微调 · 排班×接待×工单对照 · 周/月日历视图</div>
      </div>
      <div class="flex gap-8">
        <el-button type="primary" :icon="Plus" @click="openBulk">批量排班</el-button>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <!-- ============ 排班列表 ============ -->
      <el-tab-pane label="排班列表" name="list">
        <el-card class="mb-16">
          <div class="flex gap-8" style="flex-wrap: wrap">
            <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束" style="width: 260px" @change="loadSchedules" />
            <el-select v-model="groupFilter" placeholder="按分组" clearable style="width: 160px" @change="onGroupFilterChange">
              <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
            </el-select>
            <el-select v-model="personFilter" placeholder="按人员" clearable filterable style="width: 180px" @change="loadSchedules">
              <el-option v-for="p in filteredPersons" :key="p.id" :label="p.display_name" :value="p.id" />
            </el-select>
          </div>
        </el-card>
        <el-card>
          <el-table :data="schedules" v-loading="loading" border stripe>
            <el-table-column prop="schedule_date" label="日期" width="120" />
            <el-table-column label="人员" width="140">
              <template #default="{ row }">
                {{ row.person_name }}
                <el-tag v-if="row.group_name" size="small" effect="plain" style="margin-left: 4px">{{ row.group_name }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="shift_name" label="班次" width="100" />
            <el-table-column label="时间" width="140">
              <template #default="{ row }">{{ row.start_time || '-' }} ~ {{ row.end_time || '-' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
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

      <!-- ============ 日历视图(周/月) ============ -->
      <el-tab-pane label="日历视图" name="calendar">
        <el-card class="mb-16">
          <div class="flex gap-8" style="flex-wrap: wrap; align-items: center">
            <el-radio-group v-model="calMode" @change="renderCalendar">
              <el-radio-button value="week">周视图</el-radio-button>
              <el-radio-button value="month">月视图</el-radio-button>
            </el-radio-group>
            <el-date-picker
              v-model="calCursor"
              :type="calMode === 'week' ? 'week' : 'month'"
              :value-format="calMode === 'week' ? 'YYYY-MM-DD' : 'YYYY-MM-DD'"
              placeholder="选择周/月"
              style="width: 200px"
              @change="renderCalendar"
            />
            <el-button :icon="ArrowLeft" @click="calPrev" />
            <el-button @click="calToday">今天</el-button>
            <el-button :icon="ArrowRight" @click="calNext" />
            <span class="cal-range">{{ calRangeLabel }}</span>
            <el-divider direction="vertical" />
            <el-select v-model="groupFilter" placeholder="按分组" clearable style="width: 160px" @change="renderCalendar">
              <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
            </el-select>
            <el-checkbox v-model="showEmpty">显示无排班人员</el-checkbox>
          </div>
        </el-card>

        <!-- 周视图: 人员 × 7 天 矩阵 -->
        <el-card v-if="calMode === 'week'" v-loading="calLoading">
          <div style="overflow-x: auto">
            <table class="cal-grid week-grid">
              <thead>
                <tr>
                  <th class="cal-corner">人员</th>
                  <th v-for="d in calDates" :key="d.iso" :class="['cal-h', { 'is-today': d.isToday }]">
                    <div class="cal-h-date">{{ d.date }}</div>
                    <div class="cal-h-weekday">{{ d.weekday }}</div>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in calPersons" :key="p.id">
                  <td class="cal-pname">
                    <span>{{ p.display_name }}</span>
                    <el-tag v-if="p.group_name" size="small" effect="plain">{{ p.group_name }}</el-tag>
                  </td>
                  <td v-for="d in calDates" :key="d.iso" :class="['cal-cell', { 'is-today': d.isToday }]" @click="openCellEdit(p, d)">
                    <template v-if="cellOf(p.id, d.iso)">
                      <el-tag :type="statusType(cellOf(p.id, d.iso).status)" size="small">{{ cellOf(p.id, d.iso).shift_name || '-' }}</el-tag>
                      <div class="cal-cell-time">{{ cellOf(p.id, d.iso).start_time || '' }}</div>
                      <div v-if="cellOf(p.id, d.iso).note" class="cal-cell-note">{{ cellOf(p.id, d.iso).note }}</div>
                    </template>
                    <span v-else class="cal-cell-empty">+</span>
                  </td>
                </tr>
                <tr v-if="!calPersons.length">
                  <td :colspan="calDates.length + 1" class="cal-empty-row">暂无人员</td>
                </tr>
              </tbody>
            </table>
          </div>
        </el-card>

        <!-- 月视图: 日期网格 × 多人排班 -->
        <el-card v-else v-loading="calLoading">
          <div class="month-grid">
            <div v-for="w in [0, 1, 2, 3, 4, 5]" :key="w" class="month-week">
              <div v-for="(d, i) in calDates.slice(w * 7, w * 7 + 7)" :key="d.iso" :class="['month-cell', { 'is-today': d.isToday, 'is-other': d.otherMonth }]">
                <div class="month-cell-date">
                  <span>{{ d.date }}</span>
                  <el-tag v-if="d.schedules && d.schedules.length" size="small" effect="plain" round>{{ d.schedules.length }}</el-tag>
                </div>
                <div class="month-cell-body">
                  <div v-for="s in (d.schedules || []).slice(0, 4)" :key="s.id" class="month-chip" @click="openRowEdit(s)">
                    <el-tag :type="statusType(s.status)" size="small">{{ s.shift_name || '-' }}</el-tag>
                    <span class="month-chip-name">{{ s.person_name }}</span>
                    <span v-if="s.status !== '正常'" class="month-chip-status">{{ s.status }}</span>
                  </div>
                  <div v-if="d.schedules && d.schedules.length > 4" class="month-chip-more">
                    +{{ d.schedules.length - 4 }} 更多
                  </div>
                </div>
              </div>
            </div>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ============ 排班对照 ============ -->
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
            <div class="cmp-chip"><span>考勤人数</span><b>{{ cmp.attendance_count ?? '-' }}</b></div>
          </div>
          <el-table :data="cmp.detail || []" border stripe>
            <el-table-column prop="person_name" label="人员" width="120" />
            <el-table-column prop="shift_name" label="班次" width="100" />
            <el-table-column label="时间" width="140">
              <template #default="{ row }">{{ row.start_time || '-' }} ~ {{ row.end_time || '-' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reception_count" label="接待量" width="90" align="right" />
            <el-table-column prop="ticket_count" label="工单量" width="90" align="right" />
            <el-table-column label="考勤" width="140">
              <template #default="{ row }">
                <span v-if="row.check_in_at || row.check_out_at">
                  {{ row.check_in_at || '-' }} ~ {{ row.check_out_at || '-' }}
                </span>
                <span v-else class="text-muted">-</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 批量排班 -->
    <el-dialog v-model="bulkVisible" title="批量排班" width="560px">
      <el-form label-width="90px">
        <el-form-item label="分组">
          <el-select v-model="bulkForm.group_id" placeholder="按分组筛选(可选)" clearable style="width: 100%" @change="onBulkGroupChange">
            <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="人员">
          <el-select v-model="bulkForm.person_ids" multiple filterable placeholder="选择人员" style="width: 100%">
            <el-option v-for="p in filteredPersons" :key="p.id" :label="p.display_name" :value="p.id" />
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
    <el-dialog v-model="editVisible" :title="editForm.schedule_date ? '排班微调' : '新增排班'" width="480px">
      <el-form label-width="90px">
        <el-form-item v-if="editForm.person_name" label="人员">
          <span style="font-weight: 600">{{ editForm.person_name }}</span>
        </el-form-item>
        <el-form-item label="日期">
          <el-input v-if="!editForm.id" v-model="editForm.schedule_date" placeholder="YYYY-MM-DD" />
          <span v-else>{{ editForm.schedule_date }}</span>
        </el-form-item>
        <el-form-item label="班次">
          <el-select v-model="editForm.shift_template_id" placeholder="选择班次" style="width: 100%">
            <el-option v-for="s in shifts" :key="s.id" :label="s.name + ' (' + s.start_time + '-' + s.end_time + ')'" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option v-for="s in ['正常', '顶班', '请假', '调班']" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="editForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button v-if="editForm.id" link type="danger" @click="handleDelete(editForm)">删除</el-button>
        <el-button type="primary" :loading="submitting" @click="handleEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import { listShifts, listSchedules, bulkSchedule, updateSchedule, deleteSchedule, scheduleComparison } from '@/api/schedule'
import { listPersons } from '@/api/person'
import { listGroups } from '@/api/group'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'

dayjs.extend(isoWeek)

const tab = ref('list')
const shifts = ref([])
const persons = ref([])
const groups = ref([])
const schedules = ref([])
const loading = ref(false)
const submitting = ref(false)
const range = ref([dayjs().format('YYYY-MM-DD'), dayjs().format('YYYY-MM-DD')])
const groupFilter = ref(null)
const personFilter = ref(null)
const bulkVisible = ref(false)
const editVisible = ref(false)
const cmpDate = ref(dayjs().format('YYYY-MM-DD'))
const cmp = ref({})
const bulkForm = reactive({ group_id: null, person_ids: [], range: [], shift_template_id: null, status: '正常', note: '' })
const editForm = reactive({ id: null, person_id: null, person_name: '', schedule_date: '', shift_template_id: null, status: '正常', note: '' })

// 日历视图
const calMode = ref('week')
const calCursor = ref(dayjs().format('YYYY-MM-DD'))
const calLoading = ref(false)
const calDates = ref([])
const calPersons = ref([])
const calSchedules = ref([])
const showEmpty = ref(true)

const statusType = (s) => ({ 正常: 'success', 顶班: 'warning', 请假: 'info', 调班: 'primary' }[s] || 'info')

// 按分组筛选后的可见人员
const filteredPersons = computed(() => {
  if (!groupFilter.value) return persons.value
  return persons.value.filter((p) => p.group_id === groupFilter.value || p.current_group?.id === groupFilter.value)
})

const calRangeLabel = computed(() => {
  if (!calDates.value.length) return ''
  const first = calDates.value[0]
  const last = calDates.value[calDates.value.length - 1]
  // 月视图过滤掉 otherMonth 的显示
  if (calMode.value === 'month') {
    return dayjs(calCursor.value).format('YYYY年M月')
  }
  return `${first.fullDate} ~ ${last.fullDate}`
})

async function loadShifts() {
  const res = await listShifts()
  shifts.value = res.data || []
}

async function loadPersons() {
  const res = await listPersons({ page: 1, page_size: 500 })
  persons.value = (res.data && res.data.items) || []
}

async function loadGroups() {
  try {
    const res = await listGroups()
    groups.value = res.data?.items || res.data || []
  } catch {
    groups.value = []
  }
}

async function loadSchedules() {
  if (!range.value || range.value.length !== 2) return
  loading.value = true
  try {
    const params = { start: range.value[0], end: range.value[1] }
    if (personFilter.value) params.person_id = personFilter.value
    const res = await listSchedules(params)
    schedules.value = (res.data || []).map((s) => {
      // 关联人员组名(后端已返回 group_name 字段,兜底从前端 persons 补)
      if (!s.group_name) {
        const p = persons.value.find((x) => x.id === s.person_id)
        s.group_name = p?.group_name || p?.current_group?.name || ''
      }
      return s
    })
  } finally {
    loading.value = false
  }
}

async function loadComparison() {
  const res = await scheduleComparison({ date: cmpDate.value })
  cmp.value = res.data || {}
}

// 分组筛选变更: 清空人员筛选,重新加载列表
function onGroupFilterChange() {
  personFilter.value = null
  if (tab.value === 'list') loadSchedules()
  else renderCalendar()
}

function onBulkGroupChange() {
  // 切换分组时清空已选人员(避免跨组残留)
  bulkForm.person_ids = []
}

// ============ 日历视图 ============
async function renderCalendar() {
  calLoading.value = true
  try {
    const dates = buildCalDates()
    calDates.value = dates
    const start = dates[0].iso
    const end = dates[dates.length - 1].iso
    // 取排班
    const params = { start, end }
    if (groupFilter.value) params.group_id = groupFilter.value
    const res = await listSchedules(params)
    const all = res.data || []
    // 关联人员组名
    for (const s of all) {
      if (!s.group_name) {
        const p = persons.value.find((x) => x.id === s.person_id)
        s.group_name = p?.group_name || p?.current_group?.name || ''
      }
    }
    calSchedules.value = all

    // 周视图: 行=人员, 列=日期
    if (calMode.value === 'week') {
      const personIds = new Set(all.map((s) => s.person_id))
      const list = groupFilter.value
        ? persons.value.filter((p) => p.group_id === groupFilter.value || p.current_group?.id === groupFilter.value)
        : persons.value
      calPersons.value = list.filter((p) => showEmpty.value || personIds.has(p.id))
      // 排序: 有排班优先 → 组名 → 姓名
      calPersons.value.sort((a, b) => {
        const aHas = personIds.has(a.id) ? 0 : 1
        const bHas = personIds.has(b.id) ? 0 : 1
        if (aHas !== bHas) return aHas - bHas
        const ga = a.group_name || a.current_group?.name || ''
        const gb = b.group_name || b.current_group?.name || ''
        if (ga !== gb) return ga.localeCompare(gb)
        return (a.display_name || '').localeCompare(b.display_name || '')
      })
    } else {
      // 月视图: 把排班按日期塞进 calDates 的 schedules 字段
      const byDate = {}
      for (const s of all) {
        const d = s.schedule_date
        if (!byDate[d]) byDate[d] = []
        byDate[d].push(s)
      }
      for (const d of calDates.value) {
        d.schedules = byDate[d.iso] || []
      }
    }
  } finally {
    calLoading.value = false
  }
}

function buildCalDates() {
  const today = dayjs().format('YYYY-MM-DD')
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  const cursor = dayjs(calCursor.value || today)
  const dates = []
  if (calMode.value === 'week') {
    // 周: 周一 ~ 周日
    const start = cursor.startOf('week') // 周日为开始(默认)
    // 调整为周一为开始
    const dow = cursor.day()
    const mondayStart = cursor.subtract(dow === 0 ? 6 : dow - 1, 'day').startOf('day')
    for (let i = 0; i < 7; i++) {
      const d = mondayStart.add(i, 'day')
      dates.push({
        iso: d.format('YYYY-MM-DD'),
        date: d.format('M/D'),
        fullDate: d.format('YYYY-MM-DD'),
        weekday: weekdays[d.day()],
        isToday: d.format('YYYY-MM-DD') === today,
      })
    }
  } else {
    // 月: 6 周 × 7 天,从当月1号所在周的周一开始
    const firstOfMonth = cursor.startOf('month')
    const dow = firstOfMonth.day()
    const startDay = firstOfMonth.subtract(dow === 0 ? 6 : dow - 1, 'day')
    for (let i = 0; i < 42; i++) {
      const d = startDay.add(i, 'day')
      const inMonth = d.month() === cursor.month()
      dates.push({
        iso: d.format('YYYY-MM-DD'),
        date: d.format('D'),
        fullDate: d.format('YYYY-MM-DD'),
        weekday: weekdays[d.day()],
        isToday: d.format('YYYY-MM-DD') === today,
        otherMonth: !inMonth,
        schedules: [],
      })
    }
  }
  return dates
}

function cellOf(personId, dateIso) {
  return calSchedules.value.find((s) => s.person_id === personId && s.schedule_date === dateIso)
}

function calPrev() {
  const unit = calMode.value === 'week' ? 'week' : 'month'
  calCursor.value = dayjs(calCursor.value).subtract(1, unit).format('YYYY-MM-DD')
  renderCalendar()
}

function calNext() {
  const unit = calMode.value === 'week' ? 'week' : 'month'
  calCursor.value = dayjs(calCursor.value).add(1, unit).format('YYYY-MM-DD')
  renderCalendar()
}

function calToday() {
  calCursor.value = dayjs().format('YYYY-MM-DD')
  renderCalendar()
}

function openCellEdit(person, date) {
  const existing = cellOf(person.id, date.iso)
  if (existing) {
    openRowEdit(existing)
  } else {
    // 新增
    Object.assign(editForm, {
      id: null, person_id: person.id, person_name: person.display_name,
      schedule_date: date.iso, shift_template_id: shifts.value[0]?.id || null,
      status: '正常', note: '',
    })
    editVisible.value = true
  }
}

function openRowEdit(row) {
  Object.assign(editForm, {
    id: row.id, person_id: row.person_id, person_name: row.person_name,
    schedule_date: row.schedule_date, shift_template_id: row.shift_template_id || null,
    status: row.status || '正常', note: row.note || '',
  })
  editVisible.value = true
}

function openBulk() {
  bulkForm.group_id = groupFilter.value
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
    refreshActive()
  } finally {
    submitting.value = false
  }
}

async function handleEdit() {
  submitting.value = true
  try {
    if (editForm.id) {
      await updateSchedule(editForm.id, {
        status: editForm.status,
        note: editForm.note,
        shift_template_id: editForm.shift_template_id,
      })
      ElMessage.success('已更新')
    } else {
      // 新增: 单人单日
      await bulkSchedule({
        person_ids: [editForm.person_id],
        start: editForm.schedule_date,
        end: editForm.schedule_date,
        shift_template_id: editForm.shift_template_id,
        status: editForm.status,
        note: editForm.note || null,
      })
      ElMessage.success('已新增')
    }
    editVisible.value = false
    refreshActive()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  if (!row?.id) return
  await ElMessageBox.confirm('确认删除该排班?', '提示', { type: 'warning' })
  await deleteSchedule(row.id)
  ElMessage.success('已删除')
  editVisible.value = false
  refreshActive()
}

// 刷新当前激活的 tab 数据
function refreshActive() {
  if (tab.value === 'list') loadSchedules()
  else if (tab.value === 'calendar') renderCalendar()
  else if (tab.value === 'comparison') loadComparison()
}

function loadAll() {
  loadShifts()
  loadPersons()
  loadGroups()
  loadSchedules()
  if (tab.value === 'calendar') renderCalendar()
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
.text-muted { color: #909399; font-size: 12px; }
.cmp-chip { border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 16px; display: flex; flex-direction: column; gap: 4px; min-width: 120px; }
.cmp-chip span { font-size: 12px; color: #909399; }
.cmp-chip b { font-size: 24px; }

/* 日历视图 */
.cal-range { color: #303133; font-size: 14px; font-weight: 600; }

.cal-grid { width: 100%; border-collapse: collapse; table-layout: fixed; }
.week-grid { min-width: 900px; }
.cal-grid th, .cal-grid td { border: 1px solid #ebeef5; padding: 8px; vertical-align: top; }
.cal-corner { width: 160px; background: #f5f7fa; text-align: left; }
.cal-h { background: #f5f7fa; text-align: center; }
.cal-h.is-today { background: #ecf5ff; }
.cal-h-date { font-size: 14px; font-weight: 600; color: #303133; }
.cal-h-weekday { font-size: 12px; color: #909399; }
.cal-pname {
  background: #fafafa; width: 160px;
}
.cal-pname span { display: block; font-weight: 600; color: #303133; font-size: 13px; margin-bottom: 2px; }
.cal-pname .el-tag { font-size: 11px; }
.cal-cell {
  cursor: pointer; transition: background 0.15s; min-height: 48px;
}
.cal-cell:hover { background: #f0f7ff; }
.cal-cell.is-today { background: #f0f9eb; }
.cal-cell-empty {
  color: #c0c4cc; display: flex; justify-content: center; align-items: center;
  font-size: 18px; height: 32px;
}
.cal-cell-time { font-size: 11px; color: #909399; margin-top: 2px; }
.cal-cell-note { font-size: 11px; color: #d35400; margin-top: 2px; }
.cal-empty-row { text-align: center; color: #909399; padding: 32px 0; }

/* 月视图 */
.month-grid { display: flex; flex-direction: column; gap: 4px; }
.month-week { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
.month-cell {
  border: 1px solid #ebeef5; border-radius: 4px; min-height: 100px; padding: 6px;
  background: #fff; display: flex; flex-direction: column;
}
.month-cell.is-today { border-color: #67c23a; background: #f0f9eb; }
.month-cell.is-other { background: #fafafa; opacity: 0.6; }
.month-cell-date {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 4px; font-size: 13px; color: #303133; font-weight: 600;
}
.month-cell-body { display: flex; flex-direction: column; gap: 4px; flex: 1; overflow: hidden; }
.month-chip {
  display: flex; align-items: center; gap: 4px; padding: 2px 4px;
  border-radius: 3px; background: #f5f7fa; cursor: pointer; font-size: 11px;
}
.month-chip:hover { background: #ecf5ff; }
.month-chip-name { flex: 1; color: #303133; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.month-chip-status { color: #d35400; }
.month-chip-more { font-size: 11px; color: #909399; text-align: center; padding: 2px; }
</style>
