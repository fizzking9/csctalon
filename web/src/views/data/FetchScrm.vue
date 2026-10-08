<template>
  <div>
    <!-- 定时同步控制（与 BMS 共用同一调度） -->
    <el-card class="mb-16">
      <template #header>
        <div class="flex justify-between items-center">
          <span>SCRM 定时同步</span>
          <div class="flex gap-8 items-center">
            <el-tag v-if="scheduleStatus.enabled" type="success">运行中</el-tag>
            <el-tag v-else type="info">已停止</el-tag>
            <el-switch
              v-model="scheduleEnabled"
              :loading="scheduleToggling"
              active-text="启用"
              inactive-text="停止"
              @change="toggleSchedule"
            />
          </div>
        </div>
      </template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="定时计划">
          <el-tag v-for="h in scheduleStatus.schedule_hours" :key="h" size="small" class="mr-4">{{ h }}:00</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="下次执行">{{ formatTime(scheduleStatus.next_run) }}</el-descriptions-item>
        <el-descriptions-item label="SCRM 凭证">
          <el-tag v-if="scrmCredentialsOk" type="success">已配置</el-tag>
          <el-tag v-else type="danger">未配置</el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="runningTask"
        type="info"
        :closable="false"
        show-icon
        class="mt-16"
      >
        <template #title>
          <div class="flex justify-between items-center w-full">
            <div>
              <b>{{ triggerLabel(runningTask.trigger_type, runningTask.triggered_by) }}</b>
              正在同步 · 整体进度
              <el-progress
                :percentage="runningTask.percent || 0"
                :stroke-width="8"
                style="width: 260px; display: inline-block; margin-left: 8px; vertical-align: middle"
              />
              <span style="margin-left: 8px">
                {{ runningTask.done_sources }}/{{ runningTask.total_sources }} 个源
              </span>
              <span v-if="runningTask.current_phase" class="phase-chip">
                {{ dsName(runningTask.current_source) }} · {{ runningTask.current_phase }}
              </span>
            </div>
          </div>
        </template>
      </el-alert>
      <el-alert
        v-if="!scrmCredentialsOk"
        type="warning"
        :closable="false"
        show-icon
        class="mt-16"
      >
        SCRM 微信聚合平台凭证未配置,定时同步无法启动。
        请前往 <router-link to="/data/ingest?tab=sync-settings" class="link">数据采集 → 同步设置</router-link>
        中找到 scrm_operator 等源填写 base_url/clientId/clientSecret。
      </el-alert>
    </el-card>

    <!-- 手动同步 -->
    <el-card class="mb-16">
      <template #header>
        <div class="flex justify-between items-center">
          <span>手动同步</span>
          <el-button text type="primary" @click="$router.push('/data/ingest?tab=sync-settings')">
            <el-icon><Setting /></el-icon>
            同步设置
          </el-button>
        </div>
      </template>
      <el-form ref="formRef" :model="form" label-width="110px" style="max-width: 820px" :rules="rules">
        <el-form-item label="同步数据源" prop="syncTypes">
          <el-checkbox-group v-model="form.syncTypes">
            <el-checkbox
              v-for="ds in SCRM_DATA_TYPES"
              :key="ds.data_type"
              :label="ds.data_type"
              :disabled="busyRunning"
            >
              {{ ds.name }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="时间范围" prop="timeRange">
          <el-radio-group v-model="form.timeRange" :disabled="busyRunning" @change="onTimeRangeChange">
            <el-radio-button value="incremental">增量接续(推荐)</el-radio-button>
            <el-radio-button value="today">今天</el-radio-button>
            <el-radio-button value="yesterday">昨天</el-radio-button>
            <el-radio-button value="week">过去一周</el-radio-button>
            <el-radio-button value="custom">自定义</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.timeRange === 'incremental'" label="即将抓取范围">
          <div class="range-list">
            <div
              v-for="r in scrmStatusRows.filter(r => form.syncTypes.includes(r.data_type))"
              :key="r.data_type"
              class="range-item"
            >
              <span class="range-ds">{{ dsName(r.data_type) }}</span>
              <span v-if="r.status === 'never'" class="range-desc">（首次）兜底 30 天前 → 现在</span>
              <span v-else class="range-desc">上次 {{ formatTime(r.last_start) }} 后 {{ OVERLAP_LABEL }} → 现在</span>
            </div>
          </div>
          <div v-if="cooldownRemaining > 0" class="cooldown-tip mt-8">
            <el-icon><Clock /></el-icon>
            距离上次成功完成还在冷却窗口内,还剩
            <b>{{ Math.ceil(cooldownRemaining / 60) }}分{{ cooldownRemaining % 60 }}秒</b>;
            若改选自定义/昨天/一周则不受冷却限制。
          </div>
        </el-form-item>
        <el-form-item v-if="form.timeRange === 'custom'" label="自定义时间">
          <el-date-picker
            v-model="form.customRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 100%"
            :disabled="busyRunning"
          />
        </el-form-item>
        <el-form-item>
          <el-alert type="info" :closable="false" show-icon>
            <p class="alert-text">
              SCRM 基于 HTTPS OpenAPI 直接对接(非 Playwright),无需验证码登录。
              「增量接续」会按各源上次成功的截止时间回退 2 小时补边,首次则自动回溯 30 天;
              任务仍与 BMS 共用后台互斥锁 + 冷却,可关闭页面。
            </p>
          </el-alert>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="submitLoading"
            :disabled="busyRunning || form.syncTypes.length === 0 || submitDisabledByCool"
            @click="handleSyncAll"
          >
            <el-icon><Refresh /></el-icon>
            <span v-if="busyRunning">任务执行中,请等待…</span>
            <span v-else-if="submitDisabledByCool">冷却中,还剩{{ Math.ceil(cooldownRemaining/60) }}分</span>
            <span v-else>开始同步({{ form.syncTypes.length }}个源,{{ timeRangeLabel }})</span>
          </el-button>
          <el-button
            v-if="form.timeRange === 'incremental' && cooldownRemaining > 0 && !busyRunning"
            type="warning"
            plain
            :loading="submitLoading"
            @click="handleSyncAll(true)"
          >
            强制同步(跳过冷却)
          </el-button>
        </el-form-item>
      </el-form>

      <!-- 异步任务进度 + 结果 -->
      <div v-if="runningTask || syncResults.length" class="sync-progress mt-16">
        <div class="progress-header mb-8">
          <span class="progress-title">
            <b>任务实时进度</b>
            <span v-if="runningTask && runningTask.task_id" class="task-id"> · {{ runningTask.task_id }}</span>
          </span>
          <span v-if="runningTask" class="progress-meta">
            {{ runningTask.done_sources }}/{{ runningTask.total_sources }} · {{ runningTask.percent }}%
          </span>
        </div>
        <el-steps v-if="form.syncTypes.length > 0" :active="activeStepIndex" align-center finish-status="success">
          <el-step
            v-for="dt in form.syncTypes"
            :key="dt"
            :title="dsName(dt)"
            :status="getStepStatus(dt)"
          />
        </el-steps>
        <div v-if="runningTask && runningTask.current_phase" class="current-phase mt-8 mb-8">
          当前阶段：<b>{{ dsName(runningTask.current_source) }}</b> · {{ runningTask.current_phase }}
        </div>
        <el-collapse v-model="logPanelOpen" class="mt-16 log-collapse">
          <el-collapse-item title="详细日志流" name="logs">
            <div ref="logStreamRef" class="log-stream">
              <div
                v-for="(log, i) in (runningTask && runningTask.recent_logs || [])"
                :key="i"
                :class="['log-line', 'log-' + (log.phase || 'info')]"
              >
                <span class="log-t">[{{ log.t }}]</span>
                <span class="log-dt">{{ dsName(log.dt) }}</span>
                <span class="log-msg">{{ log.msg }}</span>
              </div>
              <div v-if="!runningTask || !runningTask.recent_logs || runningTask.recent_logs.length === 0" class="log-empty">
                等待日志…
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>

        <el-table v-if="syncResults.length" :data="syncResults" border size="small" class="mt-16">
          <el-table-column prop="name" label="数据源" width="150" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.success ? 'success' : 'danger'" size="small">
                {{ row.success ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="API调用" width="90">
            <template #default="{ row }">{{ row.api_call_count ?? '-' }}</template>
          </el-table-column>
          <el-table-column label="接口总数" width="90">
            <template #default="{ row }">{{ row.api_total_reported ?? '-' }}</template>
          </el-table-column>
          <el-table-column label="对账偏差" width="90">
            <template #default="{ row }">
              <el-text :type="(row.reconcile_delta == null || row.reconcile_delta === 0) ? 'info' : 'warning'" size="small">
                {{ row.reconcile_delta == null ? '-' : row.reconcile_delta }}
              </el-text>
            </template>
          </el-table-column>
          <el-table-column prop="fetched_rows" label="拉取" width="70" />
          <el-table-column prop="inserted" label="新增" width="70" />
          <el-table-column prop="updated" label="更新" width="70" />
          <el-table-column prop="failed" label="失败" width="70" />
          <el-table-column prop="start_time" label="时间范围" show-overflow-tooltip>
            <template #default="{ row }">
              {{ formatTime(row.start_time) }} ~ {{ formatTime(row.end_time) }}
            </template>
          </el-table-column>
          <el-table-column prop="error" label="错误" show-overflow-tooltip>
            <template #default="{ row }">
              <el-text v-if="row.error" type="danger" size="small">{{ row.error }}</el-text>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <!-- SCRM 数据源最近同步状态 -->
    <el-card class="mb-16">
      <template #header>
        <span>SCRM 数据源同步状态</span>
      </template>
      <el-table :data="scrmStatusRows" border size="small">
        <el-table-column prop="name" label="数据源" width="180" />
        <el-table-column prop="data_type" label="代码" width="180" />
        <el-table-column label="最近同步" width="180">
          <template #default="{ row }">
            {{ formatTime(row.last_end) || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'success'" type="success" size="small">成功</el-tag>
            <el-tag v-else-if="row.status === 'failed'" type="danger" size="small">失败</el-tag>
            <el-tag v-else-if="row.status === 'running'" type="warning" size="small">进行中</el-tag>
            <el-text v-else type="info" size="small">未同步</el-text>
          </template>
        </el-table-column>
        <el-table-column label="触发类型" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.trigger_type === 'scheduled'" size="small" type="warning">定时</el-tag>
            <el-tag v-else-if="row.trigger_type === 'manual'" size="small" type="info">手动</el-tag>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="拉取行数" width="90">
          <template #default="{ row }">{{ row.stats?.fetched_rows ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="新增/更新/失败" width="170">
          <template #default="{ row }">
            <span v-if="row.stats">
              <span class="ok">{{ row.stats.inserted ?? 0 }}</span> /
              <span class="warn">{{ row.stats.updated ?? 0 }}</span> /
              <span class="err">{{ row.stats.failed ?? 0 }}</span>
            </span>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="对账" width="90">
          <template #default="{ row }">
            <el-text v-if="row.stats?.reconcile_delta != null"
                     :type="row.stats.reconcile_delta === 0 ? 'success' : 'warning'" size="small">
              Δ={{ row.stats.reconcile_delta }}
            </el-text>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- SCRM 同步日志 -->
    <el-card>
      <template #header>
        <div class="flex justify-between items-center">
          <span>SCRM 同步日志</span>
          <el-select v-model="logFilter" placeholder="全部" size="small" style="width: 200px" @change="loadScrmLogs">
            <el-option label="全部" value="" />
            <el-option v-for="ds in SCRM_DATA_TYPES" :key="ds.data_type" :label="ds.name" :value="ds.data_type" />
          </el-select>
        </div>
      </template>
      <el-table :data="scrmSyncLogs" border size="small" max-height="420">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="数据源" width="160">
          <template #default="{ row }">{{ dsName(row.data_type) }}</template>
        </el-table-column>
        <el-table-column label="同步范围" show-overflow-tooltip>
          <template #default="{ row }">{{ row.sync_scope || '-' }}</template>
        </el-table-column>
        <el-table-column label="触发" width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.trigger_type === 'scheduled' ? 'warning' : 'info'">
              {{ row.trigger_type === 'scheduled' ? '定时' : '手动' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="triggered_by" label="触发者" width="80" />
        <el-table-column label="状态" width="70">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : (row.status === 'failed' ? 'danger' : 'warning')" size="small">
              {{ row.status === 'success' ? '成功' : (row.status === 'failed' ? '失败' : '进行中') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="API" width="60">
          <template #default="{ row }">{{ row.api_call_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="拉取" width="60">
          <template #default="{ row }">{{ row.fetched_rows || 0 }}</template>
        </el-table-column>
        <el-table-column label="新增" width="60">
          <template #default="{ row }">{{ row.inserted_rows || 0 }}</template>
        </el-table-column>
        <el-table-column label="更新" width="60">
          <template #default="{ row }">{{ row.updated_rows || 0 }}</template>
        </el-table-column>
        <el-table-column label="失败" width="60">
          <template #default="{ row }">{{ row.failed_rows || 0 }}</template>
        </el-table-column>
        <el-table-column label="对账Δ" width="80">
          <template #default="{ row }">
            <el-text v-if="row.reconcile_delta != null"
                     :type="row.reconcile_delta === 0 ? 'success' : 'warning'" size="small">
              {{ row.reconcile_delta }}
            </el-text>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误" show-overflow-tooltip>
          <template #default="{ row }">
            <el-text v-if="row.error" type="danger" size="small">{{ row.error }}</el-text>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Setting, Clock } from '@element-plus/icons-vue'
import {
  getScheduleStatus,
  startSchedule,
  stopSchedule,
  taskPreflight,
  taskStatus,
  getScrmSyncStatus,
  submitScrmSync,
  listScrmSyncLogs,
} from '@/api/fetch'
import { usePolling, POLL_INTERVALS } from '@/composables/usePolling'

const OVERLAP_LABEL = '(重叠2小时)'
const SCRM_DATA_TYPES = [
  { data_type: 'scrm_operator', name: '客服操作员(全量快照)', default_sync: true },
  { data_type: 'scrm_session_private', name: '私聊会话(增量)', default_sync: true },
  { data_type: 'scrm_session_group', name: '群聊会话(增量)', default_sync: false },
  { data_type: 'scrm_session_virtual', name: '虚拟会话(增量)', default_sync: false },
]

const scheduleToggling = ref(false)
const scrmStatusRows = ref([])
const scrmSyncLogs = ref([])
const syncResults = ref([])
const logFilter = ref('')
const formRef = ref(null)
const syncTypesInited = ref(false)
const submitLoading = ref(false)
const logPanelOpen = ref(['logs'])
const logStreamRef = ref(null)

const form = reactive({
  syncTypes: [],
  timeRange: 'incremental',
  customRange: null,
})

const rules = {
  syncTypes: [{ required: true, type: 'array', min: 1, message: '请至少选择一个数据源', trigger: 'change' }],
}

const cooldownRemaining = ref(0)
const busyRunning = ref(false)
const currentTaskId = ref(null)
const runningTask = ref(null)

const scheduleStatus = ref({
  enabled: false,
  schedule_hours: [0, 8, 12, 16, 20],
  next_run: null,
  current_progress: null,
  has_credentials: { bms: false, scrm: false },
})

const scrmCredentialsOk = computed(() => {
  const hc = scheduleStatus.value.has_credentials
  if (typeof hc === 'boolean') return hc
  return !!(hc && hc.scrm)
})

const scheduleEnabled = computed({
  get: () => !!scheduleStatus.value.enabled,
  set: (v) => { scheduleStatus.value.enabled = !!v },
})

// 统一轮询层(S-P1-2): key 全局单例 + 页面隐藏自动暂停
const PROGRESS_POLL_KEY = 'fetch-scrm:progress'
const PREFLIGHT_POLL_KEY = 'fetch-scrm:preflight'
const { startPolling, stopPolling } = usePolling()
// 轮询错误计数:连续 N 次失败 → 熔断(停止轮询并提示一次,避免不断报错)
let _pollErrorStreak = 0
const POLL_ERROR_MAX = 5
let _preflightErrorStreak = 0
const PREFLIGHT_ERROR_MAX = 3

onMounted(async () => {
  await Promise.all([loadScrmStatus(), loadScrmLogs(), loadScheduleStatus(), runPreflight()])
  logPanelOpen.value = ['logs']
  startPolling(PROGRESS_POLL_KEY, () => pollProgress(), POLL_INTERVALS.PROGRESS)
  // 定时预检(后台静默刷新,失败不弹 toast)
  startPolling(PREFLIGHT_POLL_KEY, () => runPreflight({ silentError: true }), POLL_INTERVALS.PREFLIGHT)
})

// 轮询任务由 usePolling 卸载时自动停止

watch(() => form.syncTypes.slice().sort().join(','), () => runPreflight())
watch(() => form.timeRange, () => runPreflight())

const timeRangeLabel = computed(() => ({
  incremental: '增量接续',
  today: '今天',
  yesterday: '昨天',
  week: '过去一周',
  custom: '自定义时间',
}[form.timeRange] || form.timeRange))

const submitDisabledByCool = computed(() => {
  if (!['incremental', 'today'].includes(form.timeRange)) return false
  return cooldownRemaining.value > 0
})

const activeStepIndex = computed(() => {
  if (!runningTask.value) {
    const done = syncResults.value.filter(r => form.syncTypes.includes(r.data_type)).length
    return done
  }
  const done = runningTask.value.done_sources || 0
  if (runningTask.value.current_source) {
    const idx = form.syncTypes.indexOf(runningTask.value.current_source)
    return Math.max(0, idx)
  }
  return done
})

function triggerLabel(tt, who) {
  if (tt === 'scheduled') return '定时任务'
  return `手动任务（${who || '未知'}）`
}

function dsName(dt) {
  if (!dt) return '-'
  const d = SCRM_DATA_TYPES.find(s => s.data_type === dt)
  if (d) return d.name
  const r = scrmStatusRows.value.find(s => s.data_type === dt)
  return r ? r.name : dt
}

async function loadScrmStatus() {
  try {
    const res = await getScrmSyncStatus()
    scrmStatusRows.value = (res.data || []).map(r => ({ ...r, name: r.name || dsName(r.data_type) }))
    if (!syncTypesInited.value) {
      // 使用后端返回的 default_sync 配置（优先），fallback 到前端常量
      form.syncTypes = scrmStatusRows.value
        .filter(r => r.default_sync !== undefined ? r.default_sync : SCRM_DATA_TYPES.find(d => d.data_type === r.data_type)?.default_sync)
        .map(r => r.data_type)
      syncTypesInited.value = true
    }
  } catch (e) {
    console.warn('[scrm] status load failed', e)
  }
}

async function loadScrmLogs() {
  try {
    const params = {}
    if (logFilter.value) params.data_type = logFilter.value
    const res = await listScrmSyncLogs(params)
    scrmSyncLogs.value = res.data || []
  } catch (e) {
    // 忽略
  }
}

async function loadScheduleStatus() {
  try {
    const res = await getScheduleStatus()
    scheduleStatus.value = res.data
  } catch (e) {
    // 忽略
  }
}

async function runPreflight(opts = {}) {
  try {
    const res = await taskPreflight(form.syncTypes, opts)
    const d = res.data
    busyRunning.value = !!d.busy
    cooldownRemaining.value = d.cooldown_remaining || 0
    if (d.busy && d.current_task_id) {
      currentTaskId.value = d.current_task_id
    }
    if (!opts.silentError) _preflightErrorStreak = 0
    else _preflightErrorStreak = 0
  } catch (e) {
    if (opts.silentError) {
      _preflightErrorStreak += 1
      if (_preflightErrorStreak >= PREFLIGHT_ERROR_MAX) {
        // 预检熔断: 停止后台预检轮询(停止后不再产生 catch, 提示不重复)
        stopPolling(PREFLIGHT_POLL_KEY)
        ElMessage.warning(`SCRM 预检连续 ${PREFLIGHT_ERROR_MAX} 次失败,已停止后台刷新,请检查后端后刷新页面恢复`)
      }
    }
  }
}

async function pollProgress() {
  try {
    const res = await taskStatus(currentTaskId.value)
    const d = res.data
    _pollErrorStreak = 0
    busyRunning.value = !!d.busy
    cooldownRemaining.value = d.cooldown_remaining || 0
    if (d.task) {
      const isTerminal = ['success', 'failed', 'partial'].includes(d.task.status)
      const trackingSpecific = !!currentTaskId.value
      // 失败/部分失败的已终态任务 → 立即停止追踪(状态不会再变,避免继续轮询)
      if (isTerminal && trackingSpecific && d.task.status !== 'success') {
        currentTaskId.value = null
      }
      let keepAsRunning = true
      if (isTerminal && !trackingSpecific && d.task.finished_at) {
        const finishTs = new Date(d.task.finished_at).getTime()
        if (Date.now() - finishTs > 2 * 60 * 1000) keepAsRunning = false
      }
      if (!isTerminal || trackingSpecific || keepAsRunning) {
        runningTask.value = d.task
      } else {
        runningTask.value = null
      }
      if (!currentTaskId.value && d.task.task_id && !isTerminal) {
        currentTaskId.value = d.task.task_id
      }
      // 任务已完成,填充结果,清 task running
      if (isTerminal && d.task.results) {
        const statusChanged =
          !syncResults.value.length ||
          (d.task.finished_at && (!runningTask.value || runningTask.value.status !== d.task.status))
        if (statusChanged) {
          syncResults.value = d.task.results
          // 成功的追踪任务,展示 1~2 个周期后再清,避免闪走;失败已在上面清过
          if (trackingSpecific && d.task.status === 'success') {
            // 延迟 1 个周期再清(下一次 poll 会因 isTerminal+trackingSpecific=false 而自然收尾)
            setTimeout(() => { currentTaskId.value = null }, 2000)
          }
          await Promise.all([loadScrmStatus(), loadScrmLogs()])
        } else {
          syncResults.value = d.task.results
          // 终态且不是本次追踪 → 清 runningTask 与 taskId
          if (!trackingSpecific) currentTaskId.value = null
        }
      }
      await nextTick()
      if (logStreamRef.value) {
        logStreamRef.value.scrollTop = logStreamRef.value.scrollHeight
      }
    } else {
      runningTask.value = null
    }
    if (!pollProgress._scheduleTick) pollProgress._scheduleTick = 0
    pollProgress._scheduleTick += 1
    if (pollProgress._scheduleTick % 5 === 0) {
      loadScheduleStatus()
      if (pollProgress._scheduleTick % 15 === 0) loadScrmStatus()
    }
  } catch (e) {
    _pollErrorStreak += 1
    if (_pollErrorStreak >= POLL_ERROR_MAX) {
      // 状态轮询熔断: 停止进度轮询(停止后不再产生 catch, 提示不重复)
      stopPolling(PROGRESS_POLL_KEY)
      ElMessage.warning(`SCRM 状态查询连续 ${POLL_ERROR_MAX} 次失败,已停止自动刷新,请检查后端后刷新页面或重新提交`)
    }
  }
}

function onTimeRangeChange() {
  if (form.timeRange !== 'custom') {
    form.customRange = null
  }
  runPreflight()
}

async function toggleSchedule(val) {
  scheduleToggling.value = true
  try {
    if (val) await startSchedule()
    else await stopSchedule()
    ElMessage.success(val ? '定时同步已启动(包含 BMS + SCRM)' : '定时同步已停止')
    await loadScheduleStatus()
  } catch (e) {
    ElMessage.error(e.message || '操作失败')
    scheduleStatus.value.enabled = !val
  } finally {
    scheduleToggling.value = false
  }
}

async function handleSyncAll(force = false) {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    if (form.timeRange === 'custom' && (!form.customRange || form.customRange.length !== 2)) {
      ElMessage.warning('请选择自定义时间范围')
      return
    }
    const dsNames = form.syncTypes.map(dsName).join('、')
    let rangeText = timeRangeLabel.value
    if (form.timeRange === 'incremental') {
      rangeText = '各源从上次截止时间 + 重叠2小时增量接续'
    }
    try {
      await ElMessageBox.confirm(
        `将同步 SCRM 数据源: ${dsNames}\n时间范围: ${rangeText}\n与 BMS 共用后台互斥锁,可关闭页面。是否继续?`,
        '确认同步',
        { confirmButtonText: '开始同步', cancelButtonText: '取消', type: 'info' },
      )
    } catch {
      return
    }
    submitLoading.value = true
    syncResults.value = []
    try {
      const payload = {
        sync_types: form.syncTypes,
        time_range: form.timeRange,
        force: !!force,
        ignore_cooldown: !!force,
      }
      if (form.timeRange === 'custom' && form.customRange) {
        payload.custom_start = form.customRange[0]
        payload.custom_end = form.customRange[1]
      }
      const res = await submitScrmSync(payload)
      currentTaskId.value = res.data.task_id
      ElMessage.success(res.data.message || `任务已提交(${res.data.task_id}),后台执行中…`)
      await pollProgress()
    } catch (e) {
      ElMessage.error(`提交失败: ${e.message}`)
    } finally {
      submitLoading.value = false
    }
  })
}

function getStepStatus(dataType) {
  const idx = form.syncTypes.indexOf(dataType)
  if (idx === -1) return 'wait'
  if (!runningTask.value) {
    const r = syncResults.value.find(r => r.data_type === dataType)
    if (!r) return 'wait'
    return r.success ? 'success' : 'error'
  }
  const p = runningTask.value.per_source && runningTask.value.per_source[dataType]
  if (!p) return 'wait'
  if (runningTask.value.current_source === dataType && ['fetching', 'waiting', 'importing', 'fetched', 'syncing'].includes(p.phase)) {
    return 'process'
  }
  if (p.phase === 'imported') return 'success'
  if (p.phase === 'failed') return 'error'
  if (p.phase === 'skipped') return 'finish'
  if (p.phase === 'pending') return 'wait'
  return 'wait'
}

function formatTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}
</script>

<style scoped>
.alert-text { margin: 0; font-size: 13px; line-height: 1.5; }
.sync-progress { padding: 16px; background: #fafafa; border-radius: 4px; }
.mr-4 { margin-right: 4px; }
.ml-4 { margin-left: 4px; }
.mt-8 { margin-top: 8px; }
.mt-16 { margin-top: 16px; }
.mb-8 { margin-bottom: 8px; }
.mb-16 { margin-bottom: 16px; }
.gap-8 { gap: 8px; }
.link { color: var(--el-color-primary); text-decoration: underline; }
.phase-chip {
  margin-left: 12px; padding: 2px 8px;
  background: #ecf5ff; color: #409eff;
  border-radius: 4px; font-size: 12px;
}
.range-list { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.range-item { display: flex; gap: 12px; align-items: baseline; }
.range-ds { min-width: 180px; color: #303133; font-weight: 500; }
.range-desc { color: #606266; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.cooldown-tip { color: #e6a23c; font-size: 13px; display: flex; align-items: center; gap: 6px; }
.progress-header { display: flex; justify-content: space-between; align-items: baseline; font-size: 13px; }
.progress-title { font-size: 14px; }
.task-id { color: #909399; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.current-phase { font-size: 13px; color: #409eff; }
.log-collapse { border: 1px solid #ebeef5; border-radius: 4px; }
.log-stream {
  max-height: 280px; overflow-y: auto;
  background: #1e1e1e; color: #e0e0e0;
  padding: 10px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px; line-height: 1.6;
  border-radius: 4px;
}
.log-line { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.log-t { color: #9cdcfe; margin-right: 6px; }
.log-dt { color: #4ec9b0; margin-right: 8px; }
.log-line.log-failed .log-msg { color: #f48771; }
.log-line.log-imported .log-msg { color: #ce9178; }
.log-empty { color: #858585; font-style: italic; }
.dim { color: #909399; }
.ok { color: #67c23a; } .warn { color: #e6a23c; } .err { color: #f56c6c; }
</style>
