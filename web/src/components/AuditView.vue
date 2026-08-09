<template>
  <div class="audit-view">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">
        {{ title || (subjectKind === 'person' ? '人员审计时序' : '设备审计时序') }}
      </div>
      <div class="flex" style="gap: 8px; align-items: center">
        <!-- 搜索选择器 -->
        <template v-if="subjectKind === 'person'">
          <el-select
            v-model="personId"
            filterable
            remote
            clearable
            placeholder="搜索人员姓名"
            :remote-method="searchPersons"
            :loading="searching"
            :style="{ width: searchWidth }"
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
            :style="{ width: searchWidth }"
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

        <el-select v-model="days" style="width: 110px" @change="loadTimeline">
          <el-option label="近 30 天" :value="30" />
          <el-option label="近 90 天" :value="90" />
          <el-option label="近 180 天" :value="180" />
          <el-option label="近 365 天" :value="365" />
          <el-option label="不限" :value="0" />
        </el-select>
        <el-select v-model="limit" style="width: 100px" @change="loadTimeline">
          <el-option :value="50" label="50 条" />
          <el-option :value="100" label="100 条" />
          <el-option :value="200" label="200 条" />
          <el-option :value="500" label="500 条" />
        </el-select>
        <el-button type="primary" :icon="Refresh" @click="loadTimeline" :disabled="!hasSubject">刷新</el-button>
      </div>
    </div>

    <!-- Subject 信息卡片 -->
    <el-card v-if="subject && subjectKind === 'person'" class="mb-16" shadow="never">
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="姓名">{{ subject.name }}</el-descriptions-item>
        <el-descriptions-item label="部门">{{ subject.department || '-' }}</el-descriptions-item>
        <el-descriptions-item label="角色">{{ subject.role || '-' }}</el-descriptions-item>
        <el-descriptions-item label="记录总数">{{ total }}</el-descriptions-item>
        <el-descriptions-item label="归并别名" :span="4">
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
    </el-card>

    <el-card v-else-if="subject && subjectKind === 'device'" class="mb-16" shadow="never">
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="设备 SN">{{ subject.sn }}</el-descriptions-item>
        <el-descriptions-item label="设备类型">{{ subject.device_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="类型备注">{{ subject.device_type_remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="商户">{{ subject.merchant_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ subject.status || '-' }}</el-descriptions-item>
        <el-descriptions-item label="激活时间">{{ fmtDate(subject.active_time) }}</el-descriptions-item>
        <el-descriptions-item label="标签">
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
    </el-card>

    <!-- 空状态 -->
    <el-empty v-if="!hasSubject" :description="subjectKind === 'person' ? '请选择人员查看审计时序' : '请选择设备 SN 查看审计时序'" />

    <!-- 时序流 + 明细表双视图 -->
    <AuditTimeline
      v-else
      :items="items"
      :type-meta="typeMeta"
      :loading="loading"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import AuditTimeline from '@/components/AuditTimeline.vue'
import { getDeviceTimeline, getPersonTimeline, searchDevices as searchDevicesApi } from '@/api/audit'
import { listPersons } from '@/api/person'

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
  } finally {
    loading.value = false
  }
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

onMounted(() => {
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

defineExpose({ loadTimeline, subject, items, total })
</script>

<style scoped>
.flex { display: flex; }
.flex-between { display: flex; align-items: center; justify-content: space-between; }
.mb-16 { margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 600; color: #303133; }
</style>
