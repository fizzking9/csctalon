<template>
  <div class="audit-view" :class="{ 'av-compact': compact }">
    <!-- 顶部:第1行=标题+时间范围/条数/刷新; 第2行=主体搜索选择器 -->
    <div class="av-header">
      <div class="av-header-row av-header-row--top">
        <div class="page-title" style="margin: 0">
          {{ title || (subjectKind === 'person' ? '人员行为分析' : '设备行为分析') }}
        </div>
        <div class="av-toolbar">
          <el-select v-model="days" size="small" class="av-select-sm" @change="loadTimeline">
            <el-option label="当天" :value="1" />
            <el-option label="一周" :value="7" />
            <el-option label="一个月" :value="30" />
            <el-option label="三个月" :value="90" />
            <el-option label="半年" :value="180" />
            <el-option label="一年" :value="365" />
            <el-option label="所有" :value="0" />
          </el-select>
          <el-select v-model="limit" size="small" class="av-select-sm" @change="loadTimeline">
            <el-option :value="50" label="50 条" />
            <el-option :value="100" label="100 条" />
            <el-option :value="200" label="200 条" />
            <el-option :value="500" label="500 条" />
          </el-select>
          <el-button type="primary" :icon="Refresh" size="small" @click="loadTimeline" :disabled="!hasSubject">刷新</el-button>
        </div>
      </div>
      <div class="av-header-row av-header-row--search">
        <template v-if="subjectKind === 'person'">
          <el-select
            v-model="personId"
            filterable
            remote
            clearable
            placeholder="搜索人员姓名"
            :remote-method="searchPersons"
            :loading="searching"
            size="small"
            class="av-subject-select"
            @change="onSubjectChange"
          >
            <el-option
              v-for="p in personOptions"
              :key="p.id"
              :label="`${p.display_name}${p.department ? ' · ' + p.department : ''}`"
              :value="p.id"
            />
          </el-select>
        </template>
        <template v-else>
          <el-select
            v-model="sn"
            filterable
            remote
            clearable
            placeholder="输入设备 SN 搜索"
            :remote-method="searchDevices"
            :loading="searching"
            size="small"
            class="av-subject-select"
            @change="onSubjectChange"
          >
            <el-option
              v-for="d in deviceOptions"
              :key="d.sn"
              :label="`${d.sn}${d.device_type ? ' · ' + d.device_type : ''}`"
              :value="d.sn"
            />
          </el-select>
        </template>
      </div>
    </div>

    <!-- Subject 信息卡片(人事/设备档案 默认折叠, 避免占用主时序区域) -->
    <el-collapse v-if="subject" class="av-subject-collapse mb-16">
      <el-collapse-item :name="subjectKind">
        <template #title>
          <span class="av-collapse-title">
            <el-icon><UserFilled v-if="subjectKind === 'person'" /><Monitor v-else /></el-icon>
            {{ subjectKind === 'person' ? '人事档案' : '设备档案' }}
            <span class="av-collapse-sub">{{ subjectKind === 'person' ? subject.name : subject.sn }}</span>
          </span>
        </template>
        <div v-if="subjectKind === 'person'" v-loading="extraLoading">
          <el-descriptions :column="descColumn" border size="small" class="av-desc">
            <el-descriptions-item label="姓名">{{ subject.name }}</el-descriptions-item>
            <el-descriptions-item label="部门">{{ subject.department || '-' }}</el-descriptions-item>
            <el-descriptions-item label="分组">{{ personGroupName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="角色">{{ subject.role || '-' }}</el-descriptions-item>
            <el-descriptions-item label="在职状态">
              <el-tag size="small" :type="(personDetail?.employment_status || '').includes('离职') ? 'danger' : (personDetail?.is_active ? 'success' : 'info')" effect="plain">
                {{ personDetail?.employment_status || (subject.is_active ? '在职' : '-') }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="入职日期">{{ fmtDateOnly(personDetail?.hire_date) }}</el-descriptions-item>
            <el-descriptions-item label="试用期止">{{ fmtDateOnly(personDetail?.probation_end_date) }}</el-descriptions-item>
            <el-descriptions-item label="转正日期">{{ fmtDateOnly(personDetail?.regularization_date) }}</el-descriptions-item>
            <el-descriptions-item label="记录总数">{{ total }}</el-descriptions-item>
            <el-descriptions-item label="当前专项" :span="descColumn">
              <el-tag
                v-for="s in personSpecialties"
                :key="s.id"
                size="small"
                :type="s.is_primary ? 'primary' : 'info'"
                :effect="s.is_primary ? 'dark' : 'plain'"
                style="margin-right: 4px; margin-bottom: 4px"
              >
                {{ s.name }}{{ s.is_primary ? '(主)' : '' }}
              </el-tag>
              <span v-if="!personSpecialties.length">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="归并别名" :span="descColumn">
              <el-tag
                v-for="a in subject.aliases || []"
                :key="a"
                size="small"
                effect="plain"
                style="margin-right: 4px; margin-bottom: 4px"
              >
                {{ a }}
              </el-tag>
              <span v-if="!(subject.aliases || []).length">-</span>
            </el-descriptions-item>
          </el-descriptions>

          <!-- 近期排班 -->
          <div class="av-sub-section">
            <div class="av-sub-title">近期排班 <span class="av-sub-hint">(前14天~后14天)</span></div>
            <div v-if="schedules.length" class="av-sched-list">
              <div v-for="s in schedules" :key="s.id" class="av-sched-item">
                <span class="av-sched-date">{{ s.schedule_date }}</span>
                <el-tag size="small" :type="s.status === '调班' ? 'warning' : (s.status === '请假' ? 'danger' : 'success')" effect="plain">
                  {{ s.shift_name || s.status || '-' }}
                </el-tag>
                <span class="av-sched-time">{{ fmtTimeRange(s.start_time, s.end_time) }}</span>
                <span v-if="s.note" class="av-sched-note">{{ s.note }}</span>
              </div>
            </div>
            <div v-else class="av-sub-empty">无排班记录</div>
          </div>
        </div>
        <div v-else v-loading="extraLoading">
          <el-descriptions :column="descColumn" border size="small" class="av-desc">
            <el-descriptions-item label="设备 SN" class="av-desc-sn">{{ subject.sn }}</el-descriptions-item>
            <el-descriptions-item label="设备类型">{{ subject.device_type || '-' }}</el-descriptions-item>
            <el-descriptions-item label="类型备注">{{ subject.device_type_remark || '-' }}</el-descriptions-item>
            <el-descriptions-item label="商户">{{ subject.merchant_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ subject.status || '-' }}</el-descriptions-item>
            <el-descriptions-item label="激活时间">{{ fmtDate(subject.active_time) }}</el-descriptions-item>
            <el-descriptions-item label="标签" :span="descColumn - 1">
              <el-tag
                v-for="t in subject.tags || []"
                :key="t.id"
                size="small"
                :color="t.color"
                style="margin-right: 4px; color: #fff"
              >
                {{ t.name }}
              </el-tag>
              <span v-if="!(subject.tags || []).length">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="记录总数">{{ total }}</el-descriptions-item>
          </el-descriptions>

          <!-- 套餐&充值&流量汇总 -->
          <div class="av-sub-section">
            <div class="av-sub-title">套餐 / 充值 / 流量</div>
            <div v-if="comboSummary" class="av-combo-summary">
              <span class="av-cs-item"><b>{{ comboSummary.count }}</b> 笔充值</span>
              <span class="av-cs-item">总金额 ¥{{ Number(comboSummary.total_amount || 0).toFixed(2) }}</span>
              <span class="av-cs-item">剩余流量 <b>{{ comboSummary.remaining_traffic_human || '-' }}</b></span>
            </div>
            <div v-else class="av-sub-empty">无套餐/充值记录</div>

            <!-- 充值套餐明细(可折叠) -->
            <el-collapse v-if="comboItems.length" class="av-combo-collapse">
              <el-collapse-item :title="`充值套餐明细 (${comboItems.length})`">
                <el-table :data="comboItems" size="small" border stripe max-height="320" class="av-combo-table">
                  <el-table-column label="套餐" min-width="140" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.package_name || '-' }}<span v-if="row.package_type" class="av-combo-sub"> · {{ row.package_type }}</span></template>
                  </el-table-column>
                  <el-table-column label="金额" width="80" align="right">
                    <template #default="{ row }">{{ row.actual_amount != null ? '¥' + Number(row.actual_amount).toFixed(2) : '-' }}</template>
                  </el-table-column>
                  <el-table-column label="流量(可用/已用/剩余)" min-width="180" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="av-traffic-row">
                        <span>{{ row.available_traffic_human || '-' }}</span>
                        <span class="av-traffic-sep">/</span>
                        <span>{{ row.used_traffic_human || '-' }}</span>
                        <span class="av-traffic-sep">/</span>
                        <span class="av-traffic-remain">{{ row.remaining_traffic_human || '-' }}</span>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="有效期" width="180" show-overflow-tooltip>
                    <template #default="{ row }">{{ fmtDateOnly(row.start_date) }} ~ {{ fmtDateOnly(row.end_date) }}</template>
                  </el-table-column>
                  <el-table-column label="状态" width="90">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.status && row.status.includes('正常') ? 'success' : 'info'" effect="plain">{{ row.status || '-' }}</el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </el-collapse-item>
            </el-collapse>
          </div>

          <!-- 实时流量查询 -->
          <div class="av-sub-section av-traffic-section">
            <div class="av-sub-title">
              实时流量
              <el-button size="small" type="primary" plain :loading="trafficLoading" @click="queryTraffic" style="margin-left: 8px">查询实时流量</el-button>
            </div>
            <div v-if="trafficInfo" class="av-traffic-result">
              <template v-if="trafficInfo.device_sn">
                <span class="av-cs-item">可用 <b>{{ trafficInfo.available_human || trafficInfo.available_bytes || '-' }}</b></span>
                <span class="av-cs-item">已用 <b>{{ trafficInfo.used_human || trafficInfo.used_bytes || '-' }}</b></span>
                <span class="av-cs-item">剩余 <b>{{ trafficInfo.remaining_human || trafficInfo.remaining_bytes || '-' }}</b></span>
              </template>
              <el-alert v-else :title="trafficInfo._hint || '上游流量接口未接入'" type="info" :closable="false" show-icon style="margin-top: 4px" />
            </div>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>

    <!-- 空状态 -->
    <el-empty v-if="!hasSubject" :description="subjectKind === 'person' ? '请选择人员查看行为分析' : '请选择设备 SN 查看行为分析'" />

    <!-- 时序流 + 明细表双视图 -->
    <AuditTimeline
      v-else
      :items="items"
      :type-meta="typeMeta"
      :loading="loading"
      :compact="compact"
      @sn-click="onSnClick"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh, UserFilled, Monitor } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import AuditTimeline from '@/components/AuditTimeline.vue'
import { getDeviceTimeline, getPersonTimeline, searchDevices as searchDevicesApi } from '@/api/audit'
import { listPersons, getPerson } from '@/api/person'
import { listSchedules } from '@/api/schedule'
import { getLocalComboBySn, queryUpstreamTraffic } from '@/api/recharge'
import { useAudit } from '@/composables/useAudit'

const { openDeviceAudit } = useAudit()

const props = defineProps({
  /** 审计主体:person 或 device */
  subjectKind: {
    type: String,
    default: 'person',
    validator: (v) => ['person', 'device'].includes(v),
  },
  /** 初始主体 ID(person_id 或 sn),便于从其他视图直接挂载调用 */
  initialId: {
    type: [String, Number],
    default: null,
  },
  /** 初始人员姓名关键字(无 person_id 时, 按姓名搜索并自动选中首条) */
  initialKeyword: { type: String, default: '' },
  /** 标题(为空时根据 subjectKind 显示默认) */
  title: { type: String, default: '' },
  /** 搜索框宽度 */
  searchWidth: { type: String, default: '260px' },
  /** 是否从 URL query 中读取 person_id / sn 预填 */
  useRouteQuery: { type: Boolean, default: true },
  /** 初始时间窗口天数 */
  defaultDays: { type: Number, default: 90 },
  /** 初始返回条数 */
  defaultLimit: { type: Number, default: 100 },
  /** 紧凑模式(弹窗内使用):顶部压缩为2行,减小间距 */
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['loaded', 'subject-changed'])

const route = props.useRouteQuery ? useRoute() : null

const personId = ref(null)
const sn = ref('')
const days = ref(props.defaultDays)
const limit = ref(props.defaultLimit)
const items = ref([])
const typeMeta = ref({})
const subject = ref(null)
const total = ref(0)
const loading = ref(false)

const personOptions = ref([])
const deviceOptions = ref([])
const searching = ref(false)
const isMobile = ref(false)
let _mq = null

// 扩展信息(人员:人事/专项/排班; 设备:套餐/充值/流量)
const personDetail = ref(null)   // getPerson 返回的完整详情
const schedules = ref([])        // 近期排班
const comboData = ref(null)      // 设备套餐&流量
const trafficInfo = ref(null)    // 上游实时流量(占位, 待接入)
const trafficLoading = ref(false)
const extraLoading = ref(false)

// 手机端 desc 展示列数从 4 降到 2, 避免被挤成竖排文字
const descColumn = computed(() => (isMobile.value ? 2 : 4))

const hasSubject = computed(() =>
  props.subjectKind === 'person' ? !!personId.value : !!sn.value,
)

async function searchPersons(query) {
  if (!query) {
    personOptions.value = []
    return
  }
  searching.value = true
  try {
    const res = await listPersons({ keyword: query, page: 1, page_size: 30 })
    personOptions.value = res.data?.items || []
  } finally {
    searching.value = false
  }
}

async function searchDevices(query) {
  if (!query) {
    deviceOptions.value = []
    return
  }
  searching.value = true
  try {
    const res = await searchDevicesApi({ keyword: query, page: 1, page_size: 30 })
    deviceOptions.value = res.data?.items || []
  } finally {
    searching.value = false
  }
}

function onSubjectChange(val) {
  emit('subject-changed', val)
  if (val) loadTimeline()
}

async function loadTimeline() {
  if (!hasSubject.value) return
  loading.value = true
  try {
    let res
    if (props.subjectKind === 'person') {
      res = await getPersonTimeline(personId.value, {
        days: days.value,
        limit: limit.value,
        include_device_sn: 1,
        include_person_name: 0,
      })
    } else {
      res = await getDeviceTimeline(sn.value, {
        days: days.value,
        limit: limit.value,
        include_person_name: 1,
        include_device_sn: 0,
      })
    }
    const data = res.data || {}
    items.value = data.items || []
    typeMeta.value = data.type_meta || {}
    subject.value = data.subject || null
    total.value = data.total || 0
    emit('loaded', data)
    // 加载扩展信息(人事/专项/排班 或 套餐/流量), 失败不阻塞主时序
    loadExtraInfo()
  } finally {
    loading.value = false
  }
}

// 加载扩展信息:人员→人事/专项/排班; 设备→套餐/充值/流量
async function loadExtraInfo() {
  personDetail.value = null
  schedules.value = []
  comboData.value = null
  trafficInfo.value = null
  if (!hasSubject.value) return
  extraLoading.value = true
  try {
    if (props.subjectKind === 'person') {
      // 排班范围: 前14天~后14天, 覆盖近期与未来排班
      const start = dayjs().subtract(14, 'day').format('YYYY-MM-DD')
      const end = dayjs().add(14, 'day').format('YYYY-MM-DD')
      const [detailRes, schedRes] = await Promise.all([
        getPerson(personId.value),
        listSchedules({ person_id: personId.value, start, end }).catch(() => ({ data: [] })),
      ])
      personDetail.value = detailRes.data || null
      schedules.value = schedRes.data || []
    } else {
      const res = await getLocalComboBySn(sn.value, { limit: 50 })
      comboData.value = res.data || null
    }
  } catch (e) {
    // 静默失败, 不影响审计主流程
    console.warn('[audit] loadExtraInfo failed', e)
  } finally {
    extraLoading.value = false
  }
}

// 查询上游实时流量(占位接口, 后端未接入真实数据源时返回 _hint)
async function queryTraffic() {
  if (!sn.value) return
  trafficLoading.value = true
  try {
    const res = await queryUpstreamTraffic({ sn: sn.value })
    trafficInfo.value = res.data || null
  } catch (e) {
    trafficInfo.value = { _hint: '查询失败:无权限或上游接口未接入' }
  } finally {
    trafficLoading.value = false
  }
}

// 时序流中点击设备SN → 切换到设备审计(弹窗内切换 kind, 全局单例)
function onSnClick(snVal) {
  if (!snVal) return
  openDeviceAudit(snVal)
}

// 人员当前专项(主专项在前)
const personSpecialties = computed(() => personDetail.value?.current_specialties || [])
// 人员当前分组
const personGroupName = computed(() => personDetail.value?.current_group?.name || subject.value?.group_name || '')
// 设备套餐汇总
const comboSummary = computed(() => comboData.value?.summary || null)
const comboItems = computed(() => comboData.value?.items || [])

function fmtDateOnly(t) {
  if (!t) return '-'
  return dayjs(t).format('YYYY-MM-DD')
}

function fmtTimeRange(start, end) {
  const s = start ? start.slice(0, 5) : ''
  const e = end ? end.slice(0, 5) : ''
  if (!s && !e) return '-'
  return `${s}~${e}`
}

function fmtDate(t) {
  if (!t) return '-'
  return dayjs(t).format('YYYY-MM-DD HH:mm')
}

function applyInitialId() {
  if (props.initialId == null || props.initialId === '') return
  if (props.subjectKind === 'person') {
    const id = Number(props.initialId)
    if (!Number.isNaN(id)) {
      personId.value = id
      personOptions.value = [{ id, display_name: `人员 #${id}` }]
      loadTimeline()
    }
  } else {
    const v = String(props.initialId)
    sn.value = v
    deviceOptions.value = [{ sn: v }]
    loadTimeline()
  }
}

async function applyInitialKeyword() {
  if (props.subjectKind !== 'person' || !props.initialKeyword) return
  await searchPersons(props.initialKeyword)
  if (personOptions.value.length) {
    personId.value = personOptions.value[0].id
    loadTimeline()
  }
}

onMounted(() => {
  // 响应式断点
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', (e) => { isMobile.value = e.matches })

  // 1) route query 优先级高(独立页面时使用)
  if (route) {
    if (props.subjectKind === 'person' && route.query.person_id) {
      personId.value = Number(route.query.person_id)
      personOptions.value = [{
        id: personId.value,
        display_name: route.query.name || `人员 #${personId.value}`,
      }]
      loadTimeline()
      return
    }
    if (props.subjectKind === 'device' && route.query.sn) {
      sn.value = String(route.query.sn)
      deviceOptions.value = [{ sn: sn.value }]
      loadTimeline()
      return
    }
  }
  // 2) props.initialId(嵌入式调用时使用)
  applyInitialId()
  // 3) props.initialKeyword(按姓名搜索并选中首条, 供点击人员唤起)
  applyInitialKeyword()
})

// initialId 动态变化时刷新(便于从其他面板直接点击跳转联动)
watch(
  () => props.initialId,
  (val) => {
    if (val == null || val === '') return
    if (props.subjectKind === 'person') {
      const id = Number(val)
      if (!Number.isNaN(id) && id !== personId.value) {
        personId.value = id
        personOptions.value = [{ id, display_name: `人员 #${id}` }]
        loadTimeline()
      }
    } else {
      const v = String(val)
      if (v !== sn.value) {
        sn.value = v
        deviceOptions.value = [{ sn: v }]
        loadTimeline()
      }
    }
  },
)

onBeforeUnmount(() => {
  if (_mq) {
    _mq.removeEventListener('change', (e) => { isMobile.value = e.matches })
    _mq = null
  }
})

defineExpose({ loadTimeline, subject, items, total })
</script>

<style scoped>
.flex { display: flex; }
.flex-between { display: flex; align-items: center; justify-content: space-between; }
.mb-16 { margin-bottom: 12px; }
.page-title { font-size: 15px; font-weight: 600; color: #303133; line-height: 1.3; }

/* 顶部2行布局:第1行=标题+工具控件;第2行=主体搜索 */
.av-header {
  position: relative;
  z-index: 2;
  margin-bottom: 8px;
}
.av-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.av-header-row--top {
  justify-content: space-between;
  margin-bottom: 6px;
}
.av-header-row--search {
  width: 100%;
}
.av-subject-select {
  flex: 1;
  min-width: 0;
}
.av-toolbar {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-shrink: 0;
}
.av-select-sm {
  width: 90px;
}

/* 紧凑模式(弹窗内) */
.av-compact .page-title { font-size: 14px; }
.av-compact .mb-16 { margin-bottom: 8px; }
.av-compact .av-header { margin-bottom: 6px; }
.av-compact .av-header-row--top { margin-bottom: 4px; }

/* 主体档案折叠(默认收起,不占主时序空间) */
.av-subject-collapse {
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
.av-subject-collapse :deep(.el-collapse-item__header) {
  height: 30px;
  line-height: 30px;
  padding: 0 10px;
  background: #fafafa;
  font-weight: 500;
  font-size: 12px;
}
.av-subject-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}
.av-subject-collapse :deep(.el-collapse-item__content) {
  padding: 8px 10px;
}
.av-collapse-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #606266;
}
.av-collapse-title .el-icon { font-size: 13px; }
.av-collapse-sub {
  font-weight: 600;
  color: #303133;
  margin-left: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.av-compact .av-subject-collapse :deep(.el-collapse-item__header) {
  height: 28px;
  line-height: 28px;
  font-size: 12px;
  padding: 0 8px;
}
.av-compact .av-subject-collapse :deep(.el-collapse-item__content) {
  padding: 6px 8px;
}
.av-compact .av-desc :deep(.el-descriptions__body) {
  font-size: 12px;
}
.av-compact .av-desc :deep(.el-descriptions__label),
.av-compact .av-desc :deep(.el-descriptions__content) {
  padding: 5px 8px;
}

/* 审计信息卡:SN 超长用等宽字体省略号显示,避免撑破表格 */
.av-desc-sn :deep(.el-descriptions__content) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all;
}

/* 信息卡内子区块(排班/套餐/流量) */
.av-sub-section {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #ebeef5;
}
.av-sub-section:first-of-type {
  border-top: none;
  margin-top: 2px;
  padding-top: 0;
}
.av-sub-title {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 5px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.av-sub-hint {
  font-size: 11px;
  color: #c0c4cc;
  font-weight: 400;
  margin-left: 4px;
}
.av-sub-empty {
  font-size: 11px;
  color: #c0c4cc;
  padding: 2px 0;
}

/* 排班列表 */
.av-sched-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
}
.av-sched-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #606266;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
}
.av-sched-date {
  font-weight: 600;
  color: #303133;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.av-sched-time { color: #909399; font-size: 11px; }
.av-sched-note { color: #c0c4cc; font-size: 10px; }

/* 套餐汇总 */
.av-combo-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 12px;
  color: #606266;
}
.av-cs-item b { color: #303133; }
.av-combo-sub { color: #909399; font-size: 11px; }
.av-combo-collapse { margin-top: 6px; }
.av-combo-table :deep(.el-table) { font-size: 11px; }
.av-combo-table :deep(.el-table th.el-table__cell),
.av-combo-table :deep(.el-table td.el-table__cell) {
  padding: 3px 0;
}

/* 流量三列 */
.av-traffic-row { display: flex; align-items: center; gap: 3px; flex-wrap: wrap; font-size: 11px; }
.av-traffic-sep { color: #c0c4cc; }
.av-traffic-remain { color: #67c23a; font-weight: 600; }
.av-traffic-result { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: #606266; margin-top: 4px; }
.av-traffic-section :deep(.el-button) {
  padding: 4px 8px;
  font-size: 11px;
}

@media (max-width: 768px) {
  .av-header-row--top {
    flex-wrap: wrap;
    gap: 4px;
  }
  .av-toolbar {
    width: 100%;
    justify-content: flex-start;
    gap: 4px;
  }
  .av-select-sm {
    width: calc(50% - 2px);
    min-width: 80px;
  }
  .page-title {
    font-size: 14px;
  }
  .av-sub-title { font-size: 11px; }
  .av-sched-item { padding: 2px 5px; font-size: 10px; }
  .av-combo-summary { gap: 4px 10px; font-size: 11px; }
  .av-combo-table { width: 100%; overflow-x: auto; }
  .av-combo-table :deep(.el-table) { width: 520px; }
}
</style>
