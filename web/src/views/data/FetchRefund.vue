<template>
  <div>
    <!-- 定时同步控制 -->
    <el-card class="mb-16">
      <template #header>
        <div class="flex justify-between items-center">
          <span>定时同步</span>
          <div class="flex gap-8 items-center">
            <el-tag v-if="scheduleStatus.enabled" type="success">运行中</el-tag>
            <el-tag v-else type="info">已停止</el-tag>
            <el-switch
              v-model="scheduleStatus.enabled"
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
        <el-descriptions-item label="默认源凭证">
          <el-tag v-if="scheduleStatus.has_credentials" type="success">已配置</el-tag>
          <el-tag v-else type="danger">未配置</el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <!-- 同步任务进行中提示(取进度查询接口,实时) -->
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
        v-if="!scheduleStatus.has_credentials"
        type="warning"
        :closable="false"
        show-icon
        class="mt-16"
      >
        默认同步源(退费单/服务请求/提现操作)未全部配置账号密码,定时同步无法启动。
        请前往 <router-link to="/system/sync-settings" class="link">系统管理 - 同步设置</router-link> 配置各源凭证。
      </el-alert>
    </el-card>

    <!-- 手动同步 -->
    <el-card class="mb-16">
      <template #header>
        <div class="flex justify-between items-center">
          <span>手动同步</span>
          <el-button text type="primary" @click="$router.push('/system/sync-settings')">
            <el-icon><Setting /></el-icon>
            同步设置
          </el-button>
        </div>
      </template>
      <el-form ref="formRef" :model="form" label-width="110px" style="max-width: 760px" :rules="rules">
        <el-form-item label="同步数据源" prop="dataTypes">
          <el-checkbox-group v-model="form.dataTypes">
            <el-checkbox
              v-for="ds in dataSources"
              :key="ds.data_type"
              :label="ds.data_type"
              :disabled="busyRunning"
            >
              {{ ds.name }}
              <el-tag v-if="!ds.has_credentials" type="danger" size="small" class="ml-4">未配凭证</el-tag>
              <el-tag v-else-if="ds.requires_captcha && !captchaStates[ds.data_type]?.has_storage_state"
                      type="warning" size="small" class="ml-4">需验证码登录</el-tag>
              <el-tag v-else-if="ds.requires_captcha && captchaStates[ds.data_type]?.has_storage_state"
                      type="success" size="small" class="ml-4">登录态已保存</el-tag>
              <el-tag v-else type="success" size="small" class="ml-4">已配凭证</el-tag>
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
        <!-- 增量模式:显示每个选中源即将抓取的时间范围 -->
        <el-form-item v-if="form.timeRange === 'incremental'" label="即将抓取范围">
          <div class="range-list">
            <div
              v-for="r in preflightRanges.filter(r => form.dataTypes.includes(r.data_type))"
              :key="r.data_type"
              class="range-item"
            >
              <span class="range-ds">{{ r.name }}</span>
              <span class="range-desc">{{ r.start_time }} → {{ r.end_time }}</span>
            </div>
          </div>
          <div v-if="cooldownRemaining > 0" class="cooldown-tip mt-8">
            <el-icon><Clock /></el-icon>
            距离上次成功完成还在 {{ cooldownMinutes }} 分钟冷却窗口内,还剩
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
              选中的数据源将依次同步,每个抓取之间约间隔 65 秒(BMS 1 分钟限制)。
              「增量接续」会从各源上次成功的截止时间开始(首次则从今日 00:00 开始),
              采集动作在后台执行,可以关闭页面,再次进入仍能看到进度。
            </p>
          </el-alert>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="submitLoading"
            :disabled="busyRunning || form.dataTypes.length === 0 || submitDisabledByCool"
            @click="handleSyncAll"
          >
            <el-icon><Refresh /></el-icon>
            <span v-if="busyRunning">任务执行中,请等待…</span>
            <span v-else-if="submitDisabledByCool">冷却中,还剩{{ Math.ceil(cooldownRemaining/60) }}分</span>
            <span v-else>开始同步({{ form.dataTypes.length }}个数据源,{{ timeRangeLabel }})</span>
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
          <el-button :loading="testing" @click="handleTest">测试浏览器</el-button>
        </el-form-item>
      </el-form>

      <!-- 异步任务进度条 + 日志 -->
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
        <el-steps v-if="form.dataTypes.length > 0" :active="activeStepIndex" align-center finish-status="success">
          <el-step
            v-for="dt in form.dataTypes"
            :key="dt"
            :title="dsName(dt)"
            :status="getStepStatus(dt)"
          />
        </el-steps>
        <div v-if="runningTask && runningTask.current_phase" class="current-phase mt-8 mb-8">
          当前阶段：<b>{{ dsName(runningTask.current_source) }}</b> · {{ runningTask.current_phase }}
        </div>
        <!-- 日志流 -->
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

        <!-- 同步结果 -->
        <el-table v-if="syncResults.length" :data="syncResults" border size="small" class="mt-16">
          <el-table-column prop="name" label="数据源" width="140" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.success ? 'success' : 'danger'" size="small">
                {{ row.success ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="total_rows" label="总行数" width="80" />
          <el-table-column prop="inserted_rows" label="新增" width="80" />
          <el-table-column prop="updated_rows" label="更新" width="80" />
          <el-table-column prop="failed_rows" label="失败" width="80" />
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

    <!-- 数据源状态 -->
    <el-card class="mb-16">
      <template #header>
        <span>数据源同步状态</span>
      </template>
      <el-table :data="dataSources" border size="small">
        <el-table-column prop="name" label="数据源" width="140" />
        <el-table-column prop="data_type" label="代码" width="100" />
        <el-table-column label="凭证" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.has_credentials" type="success" size="small">已配置</el-tag>
            <el-tag v-else type="danger" size="small">未配置</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="Object.keys(captchaStates).length" label="验证码登录" width="130">
          <template #default="{ row }">
            <template v-if="row.requires_captcha">
              <el-tag
                v-if="captchaStates[row.data_type]?.has_storage_state"
                type="success"
                size="small"
                effect="light"
              >已登录</el-tag>
              <el-tag v-else type="warning" size="small" effect="light">未登录</el-tag>
            </template>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="定时同步" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.default_sync" type="primary" size="small">纳入</el-tag>
            <el-tag v-else type="info" size="small">不纳入</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近同步" width="160">
          <template #default="{ row }">
            {{ formatTime(row.last_sync) || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.last_status === 'success'" type="success" size="small">成功</el-tag>
            <el-tag v-else-if="row.last_status === 'failed'" type="danger" size="small">失败</el-tag>
            <el-tag v-else-if="row.last_status === 'running'" type="warning" size="small">进行中</el-tag>
            <el-text v-else type="info" size="small">未同步</el-text>
          </template>
        </el-table-column>
        <el-table-column prop="last_rows" label="行数" width="80" />
        <el-table-column label="数据已同步至" width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.last_query_end">{{ formatTime(row.last_query_end) }}</span>
            <span v-else class="dim">今日 00:00 起</span>
          </template>
        </el-table-column>
        <el-table-column prop="url" label="BMS 页面" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- 同步日志 -->
    <el-card>
      <template #header>
        <div class="flex justify-between items-center">
          <span>同步日志</span>
          <el-select v-model="logFilter" placeholder="全部" size="small" style="width: 140px" @change="loadLogs">
            <el-option label="全部" value="" />
            <el-option label="退费单" value="refund" />
            <el-option label="服务请求" value="ticket" />
            <el-option label="提现操作" value="wallet" />
            <el-option label="设备套餐" value="device_combo" />
          </el-select>
        </div>
      </template>
      <el-table :data="syncLogs" border size="small" max-height="400">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="数据源" width="100">
          <template #default="{ row }">{{ dsName(row.data_type) }}</template>
        </el-table-column>
        <el-table-column label="触发" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.trigger_type === 'scheduled' ? 'warning' : 'info'">
              {{ row.trigger_type === 'scheduled' ? '定时' : '手动' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="triggered_by" label="触发者" width="80" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'" size="small">
              {{ row.status === 'success' ? '成功' : row.status === 'failed' ? '失败' : '进行中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间范围" show-overflow-tooltip>
          <template #default="{ row }">
            {{ formatTime(row.start_time) }} ~ {{ formatTime(row.end_time) }}
          </template>
        </el-table-column>
        <el-table-column prop="total_rows" label="行数" width="60" />
        <el-table-column prop="inserted_rows" label="新增" width="60" />
        <el-table-column prop="updated_rows" label="更新" width="60" />
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
  listSyncDataSources,
  syncAll,
  listSyncLogs,
  getScheduleStatus,
  startSchedule,
  stopSchedule,
  testBrowser,
  taskPreflight,
  taskSubmit,
  taskStatus,
  captchaLoginCheck,
} from '@/api/fetch'
import { usePolling, POLL_INTERVALS } from '@/composables/usePolling'

const testing = ref(false)
const scheduleToggling = ref(false)
const dataSources = ref([])
const syncLogs = ref([])
const syncResults = ref([])
const logFilter = ref('')
const formRef = ref(null)
const dataTypesInited = ref(false)
const submitLoading = ref(false)
const logPanelOpen = ref([])  // collapse 默认展开
const logStreamRef = ref(null)

// 验证码登录态(需验证码的数据源: shanmao_refund 等)
// { data_type: { has_storage_state, state_mtime } }
const captchaStates = reactive({})

const form = reactive({
  dataTypes: [],
  timeRange: 'incremental',  // 默认"增量接续"
  customRange: null,
})

const rules = {
  dataTypes: [{ required: true, type: 'array', min: 1, message: '请至少选择一个数据源', trigger: 'change' }],
}

const cooldownRemaining = ref(0)
const cooldownMinutes = ref(5)
const preflightRanges = ref([])
const busyRunning = ref(false)
const currentTaskId = ref(null)
const runningTask = ref(null)  // 当前/最近任务详细信息 dict

const scheduleStatus = ref({
  enabled: false,
  schedule_hours: [0, 8, 12, 16, 20],
  next_run: null,
  current_progress: null,
  has_credentials: false,
})

// 统一轮询层(S-P1-2): key 全局单例 + 页面隐藏自动暂停 + 卸载自动停止
const PROGRESS_POLL_KEY = 'fetch-refund:progress'
const PREFLIGHT_POLL_KEY = 'fetch-refund:preflight'
const { startPolling } = usePolling()

onMounted(async () => {
  await Promise.all([loadDataSources(), loadLogs(), loadScheduleStatus(), runPreflight()])
  logPanelOpen.value = ['logs']
  // 进度轮询: 2 秒一次
  startPolling(PROGRESS_POLL_KEY, pollProgress, POLL_INTERVALS.PROGRESS)
  // 预检轮询(冷却/增量时间): 30 秒一次(主要是每次进入页面和勾选变化时触发即可)
  startPolling(PREFLIGHT_POLL_KEY, runPreflight, POLL_INTERVALS.PREFLIGHT)
})

watch(
  () => form.dataTypes.slice().sort().join(','),
  () => runPreflight(),
)
watch(
  () => form.timeRange,
  () => runPreflight(),
)

// ---- 计算属性 ----
const timeRangeLabel = computed(() => {
  const t = form.timeRange
  return {
    incremental: '增量接续',
    today: '今天',
    yesterday: '昨天',
    week: '过去一周',
    custom: '自定义时间',
  }[t] || t
})

const submitDisabledByCool = computed(() => {
  // 仅增量/today 模式受冷却限制,custom/yesterday/week 不受
  if (!['incremental', 'today'].includes(form.timeRange)) return false
  return cooldownRemaining.value > 0
})

// 正在进行中的 dataTypes 顺序(和 Steps 对齐)
const activeStepIndex = computed(() => {
  if (!runningTask.value) {
    // 没有在跑的任务,根据 syncResults 已完成数量展示
    const successCnt = syncResults.value.filter(r => form.dataTypes.includes(r.data_type)).length
    return successCnt
  }
  const done = runningTask.value.done_sources || 0
  // done=0 且有 current_source -> 返回 current 索引
  if (runningTask.value.current_source) {
    const idx = form.dataTypes.indexOf(runningTask.value.current_source)
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
  const d = dataSources.value.find(s => s.data_type === dt)
  return d ? d.name : dt
}

// ---- Loader ----
async function loadDataSources() {
  try {
    const res = await listSyncDataSources()
    dataSources.value = res.data
    if (!dataTypesInited.value) {
      form.dataTypes = res.data.filter(d => d.default_sync).map(d => d.data_type)
      dataTypesInited.value = true
    }
    // 为需要验证码的源并行加载登录态
    await loadCaptchaStates()
  } catch (e) {
    ElMessage.error(`加载数据源失败: ${e.message}`)
  }
}

async function loadCaptchaStates() {
  const targets = (dataSources.value || []).filter(d => d.requires_captcha)
  if (!targets.length) return
  await Promise.all(
    targets.map(async d => {
      try {
        const r = await captchaLoginCheck(d.data_type)
        const x = r.data || {}
        captchaStates[d.data_type] = {
          has_storage_state: !!x.has_storage_state,
          state_mtime: x.state_mtime || null,
        }
      } catch (e) {
        // 接口不存在(后端未部署)时不给用户报错,只默认空
        captchaStates[d.data_type] = { has_storage_state: false, state_mtime: null }
      }
    }),
  )
}

async function loadLogs() {
  try {
    const params = {}
    if (logFilter.value) params.data_type = logFilter.value
    const res = await listSyncLogs(params)
    syncLogs.value = res.data
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

async function runPreflight() {
  try {
    const res = await taskPreflight(form.dataTypes)
    const d = res.data
    busyRunning.value = !!d.busy
    cooldownRemaining.value = d.cooldown_remaining || 0
    cooldownMinutes.value = d.cooldown_minutes || 5
    preflightRanges.value = d.time_ranges || []
    // 如果有正在进行的任务,顺便记住 task_id(万一用户进来时已经在跑)
    if (d.busy && d.current_task_id) {
      currentTaskId.value = d.current_task_id
    }
  } catch (e) {
    // 忽略预检错误
  }
}

async function pollProgress() {
  try {
    const res = await taskStatus(currentTaskId.value)
    const d = res.data
    busyRunning.value = !!d.busy
    cooldownRemaining.value = d.cooldown_remaining || 0
    if (d.task) {
      const isTerminal = ['success', 'failed', 'partial'].includes(d.task.status)
      const trackingSpecific = !!currentTaskId.value
      // runningTask 仅用于"进行中"警示条 + 实时日志流。
      // 终态任务如果不是本次主动追踪的特定 task_id，不要占着 runningTask（用 syncResults 展示结果即可），
      // 否则每次打开页面都会显示上次的历史日志，给人"总在跑智能助手导入"的错觉。
      // 例外: 刚完成 2 分钟内的终态任务, 允许短暂残留显示(方便看到收尾进度)
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
        if (!syncResults.value.length || (d.task.finished_at && (!runningTask.value || runningTask.value.status !== d.task.status))) {
          syncResults.value = d.task.results
          currentTaskId.value = null
          // 刷新数据源/日志
          await Promise.all([loadDataSources(), loadLogs()])
        }
        syncResults.value = d.task.results
        // 已完成的终态任务(且不是本次追踪的),清掉 runningTask 与 taskId
        if (!trackingSpecific) currentTaskId.value = null
      }
      // 日志自动滚到底
      await nextTick()
      if (logStreamRef.value) {
        logStreamRef.value.scrollTop = logStreamRef.value.scrollHeight
      }
    } else {
      runningTask.value = null
    }
    // 定时状态每 10 秒更新(减少请求)
    if (!pollProgress._scheduleTick) pollProgress._scheduleTick = 0
    pollProgress._scheduleTick += 1
    if (pollProgress._scheduleTick % 5 === 0) loadScheduleStatus()
  } catch (e) {
    // 忽略轮询错误
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
    ElMessage.success(val ? '定时同步已启动' : '定时同步已停止')
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
    const dsNames = form.dataTypes.map(dsName).join('、')
    let rangeText = timeRangeLabel.value
    if (form.timeRange === 'incremental') {
      rangeText = '各源从上次截止时间接续增量'
    }
    try {
      await ElMessageBox.confirm(
        `将同步 ${dsNames}\n时间范围: ${rangeText}\n每个数据源抓取之间间隔约 65 秒,任务在后台执行,可关闭页面。是否继续?`,
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
        data_types: form.dataTypes,
        time_range: form.timeRange,
        force: !!force,
        ignore_cooldown: !!force,
      }
      if (form.timeRange === 'custom' && form.customRange) {
        payload.custom_start = form.customRange[0]
        payload.custom_end = form.customRange[1]
      }
      const res = await taskSubmit(payload)
      currentTaskId.value = res.data.task_id
      ElMessage.success(res.data.message || `任务已提交(${res.data.task_id}),后台执行中…`)
      // 立刻拉一次进度
      await pollProgress()
    } catch (e) {
      ElMessage.error(`提交失败: ${e.message}`)
    } finally {
      submitLoading.value = false
    }
  })
}

function getStepStatus(dataType) {
  const idx = form.dataTypes.indexOf(dataType)
  if (idx === -1) return 'wait'
  if (!runningTask.value) {
    // 没在跑,看是否有 syncResult
    const r = syncResults.value.find(r => r.data_type === dataType)
    if (!r) return 'wait'
    return r.success ? 'success' : 'error'
  }
  const p = runningTask.value.per_source && runningTask.value.per_source[dataType]
  if (!p) return 'wait'
  if (runningTask.value.current_source === dataType && ['fetching', 'waiting', 'importing', 'fetched'].includes(p.phase)) {
    return 'process'
  }
  if (p.phase === 'imported') return 'success'
  if (p.phase === 'failed') return 'error'
  if (p.phase === 'skipped') return 'finish'
  if (p.phase === 'pending') return 'wait'
  return 'wait'
}

async function handleTest() {
  testing.value = true
  try {
    const res = await testBrowser()
    if (res.data) ElMessage.success('浏览器测试成功')
    else ElMessage.error('浏览器测试失败')
  } catch (e) {
    ElMessage.error(`测试失败: ${e.message}`)
  } finally {
    testing.value = false
  }
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
.alert-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}
.sync-progress {
  padding: 16px;
  background: #fafafa;
  border-radius: 4px;
}
.mr-4 { margin-right: 4px; }
.ml-4 { margin-left: 4px; }
.mt-8 { margin-top: 8px; }
.mt-16 { margin-top: 16px; }
.mb-8 { margin-bottom: 8px; }
.mb-16 { margin-bottom: 16px; }
.gap-8 { gap: 8px; }
.link {
  color: var(--el-color-primary);
  text-decoration: underline;
}
.phase-chip {
  margin-left: 12px;
  padding: 2px 8px;
  background: #ecf5ff;
  color: #409eff;
  border-radius: 4px;
  font-size: 12px;
}
.range-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.range-item {
  display: flex;
  gap: 12px;
  align-items: baseline;
}
.range-ds {
  min-width: 120px;
  color: #303133;
  font-weight: 500;
}
.range-desc {
  color: #606266;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.cooldown-tip {
  color: #e6a23c;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 13px;
}
.progress-title { font-size: 14px; }
.task-id {
  color: #909399;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
.current-phase {
  font-size: 13px;
  color: #409eff;
}
.log-collapse {
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
.log-stream {
  max-height: 280px;
  overflow-y: auto;
  background: #1e1e1e;
  color: #e0e0e0;
  padding: 10px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  border-radius: 4px;
}
.log-line {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.log-t { color: #9cdcfe; margin-right: 6px; }
.log-dt { color: #4ec9b0; margin-right: 8px; }
.log-line.log-failed .log-msg { color: #f48771; }
.log-line.log-imported .log-msg { color: #ce9178; }
.log-empty { color: #858585; font-style: italic; }
.dim { color: #909399; }
</style>
