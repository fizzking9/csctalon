<template>
  <div class="page-container">
    <div class="page-title">BMS 数据同步</div>

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
              @change="toggleSchedule"
              :loading="scheduleToggling"
              active-text="启用"
              inactive-text="停止"
            />
          </div>
        </div>
      </template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="定时计划">
          <el-tag v-for="h in scheduleStatus.schedule_hours" :key="h" size="small" class="mr-4">{{ h }}:00</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="下次执行">{{ formatTime(scheduleStatus.next_run) }}</el-descriptions-item>
        <el-descriptions-item label="BMS凭证">
          <el-tag v-if="scheduleStatus.has_credentials" type="success">已配置</el-tag>
          <el-tag v-else type="danger">未配置</el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="scheduleStatus.current_progress"
        type="info"
        :closable="false"
        show-icon
        class="mt-16"
      >
        正在同步: {{ scheduleStatus.current_progress.data_type }} - {{ scheduleStatus.current_progress.phase }}
      </el-alert>
      <el-alert
        v-if="!scheduleStatus.has_credentials && scheduleStatus.enabled"
        type="warning"
        :closable="false"
        show-icon
        class="mt-16"
      >
        定时同步已启动,但未配置 BMS 凭证。请在后端 .env 中设置 BMS_USERNAME 和 BMS_PASSWORD。
      </el-alert>
    </el-card>

    <!-- 手动同步 -->
    <el-card class="mb-16">
      <template #header>
        <span>手动同步</span>
      </template>
      <el-form :model="form" label-width="100px" style="max-width: 600px" :rules="rules" ref="formRef">
        <el-form-item label="BMS 账号" prop="username">
          <el-input v-model="form.username" placeholder="请输入 BMS 平台账号" />
        </el-form-item>
        <el-form-item label="BMS 密码" prop="password">
          <el-input v-model="form.password" type="password" placeholder="请输入 BMS 平台密码" show-password />
        </el-form-item>
        <el-form-item label="同步数据源" prop="dataTypes">
          <el-checkbox-group v-model="form.dataTypes">
            <el-checkbox v-for="ds in dataSources" :key="ds.data_type" :label="ds.data_type">
              {{ ds.name }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="时间范围" prop="timeRange">
          <el-radio-group v-model="form.timeRange" @change="onTimeRangeChange">
            <el-radio-button label="today">今天</el-radio-button>
            <el-radio-button label="yesterday">昨天</el-radio-button>
            <el-radio-button label="week">过去一周</el-radio-button>
            <el-radio-button label="custom">自定义</el-radio-button>
          </el-radio-group>
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
          />
        </el-form-item>
        <el-form-item>
          <el-alert type="info" :closable="false" show-icon>
            <p class="alert-text">
              选中的数据源将依次同步,每个间隔约 65 秒(BMS 1分钟限制)。
              默认同步当天 00:00 到当前时间的数据。
            </p>
          </el-alert>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="syncing" @click="handleSyncAll" :disabled="syncing || form.dataTypes.length === 0">
            <el-icon><Refresh /></el-icon>
            开始同步({{ form.dataTypes.length }}个数据源)
          </el-button>
          <el-button @click="handleTest" :loading="testing">测试浏览器</el-button>
        </el-form-item>
      </el-form>

      <!-- 同步进度 -->
      <div v-if="syncing || syncResults.length" class="sync-progress mt-16">
        <el-steps v-if="syncing" :active="currentStep" align-center>
          <el-step
            v-for="dt in form.dataTypes"
            :key="dt"
            :title="dataSources.find(d => d.data_type === dt)?.name || dt"
            :status="getStepStatus(dt)"
          />
        </el-steps>

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
        <el-table-column prop="data_type" label="代码" width="80" />
        <el-table-column label="来源" width="100">
          <template #default>
            <el-tag type="info" size="small">BMS 同步</el-tag>
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
        <el-table-column label="数据已同步至" width="160">
          <template #default="{ row }">
            {{ formatTime(row.last_query_end) || '-' }}
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
          </el-select>
        </div>
      </template>
      <el-table :data="syncLogs" border size="small" max-height="400">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="数据源" width="100">
          <template #default="{ row }">{{ getDsName(row.data_type) }}</template>
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
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  listSyncDataSources,
  syncAll,
  listSyncLogs,
  getScheduleStatus,
  startSchedule,
  stopSchedule,
  testBrowser,
} from '@/api/fetch'

const syncing = ref(false)
const testing = ref(false)
const scheduleToggling = ref(false)
const dataSources = ref([])
const syncLogs = ref([])
const syncResults = ref([])
const logFilter = ref('')
const formRef = ref(null)
const currentStep = ref(0)

const form = reactive({
  username: '',
  password: '',
  dataTypes: ['refund', 'ticket', 'wallet'],  // 默认全选
  timeRange: 'today',  // 默认今天
  customRange: null,  // 自定义时间范围
})

const rules = {
  username: [{ required: true, message: '请输入 BMS 账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入 BMS 密码', trigger: 'blur' }],
  dataTypes: [{ required: true, type: 'array', min: 1, message: '请至少选择一个数据源', trigger: 'change' }],
}

function onTimeRangeChange() {
  // 切换预设时清空自定义范围
  if (form.timeRange !== 'custom') {
    form.customRange = null
  }
}

const scheduleStatus = ref({
  enabled: false,
  schedule_hours: [0, 8, 12, 16, 20],
  next_run: null,
  current_progress: null,
  has_credentials: false,
})

let pollTimer = null

onMounted(async () => {
  await loadDataSources()
  await loadLogs()
  await loadScheduleStatus()
  // 轮询调度器状态(如果正在运行)
  pollTimer = setInterval(loadScheduleStatus, 10000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function loadDataSources() {
  try {
    const res = await listSyncDataSources()
    dataSources.value = res.data
  } catch (e) {
    ElMessage.error(`加载数据源失败: ${e.message}`)
  }
}

async function loadLogs() {
  try {
    const params = {}
    if (logFilter.value) params.data_type = logFilter.value
    const res = await listSyncLogs(params)
    syncLogs.value = res.data
  } catch (e) {
    ElMessage.error(`加载日志失败: ${e.message}`)
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

async function toggleSchedule(val) {
  scheduleToggling.value = true
  try {
    if (val) {
      await startSchedule()
      ElMessage.success('定时同步已启动')
    } else {
      await stopSchedule()
      ElMessage.success('定时同步已停止')
    }
    await loadScheduleStatus()
  } catch (e) {
    ElMessage.error(e.message || '操作失败')
    // 恢复原状态
    scheduleStatus.value.enabled = !val
  } finally {
    scheduleToggling.value = false
  }
}

async function handleSyncAll() {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return

    // 校验自定义时间范围
    if (form.timeRange === 'custom' && (!form.customRange || form.customRange.length !== 2)) {
      ElMessage.warning('请选择自定义时间范围')
      return
    }

    const dsNames = form.dataTypes.map(dt => dataSources.value.find(d => d.data_type === dt)?.name || dt).join('、')
    const rangeText = { today: '今天0点至现在', yesterday: '昨天全天', week: '过去一周', custom: '自定义时间' }[form.timeRange]

    try {
      await ElMessageBox.confirm(
        `将同步 ${dsNames},时间范围: ${rangeText},每个数据源间隔约65秒。是否继续?`,
        '确认同步',
        { confirmButtonText: '开始同步', cancelButtonText: '取消', type: 'info' }
      )
    } catch {
      return
    }

    syncing.value = true
    syncResults.value = []
    currentStep.value = 0

    try {
      const payload = {
        username: form.username,
        password: form.password,
        data_types: form.dataTypes,
        time_range: form.timeRange,
      }
      if (form.timeRange === 'custom' && form.customRange) {
        payload.custom_start = form.customRange[0]
        payload.custom_end = form.customRange[1]
      }

      const res = await syncAll(payload)

      syncResults.value = res.data.results
      currentStep.value = form.dataTypes.length

      const success = res.data.total_success
      const total = res.data.total_count
      if (success === total) {
        ElMessage.success(`同步完成: ${success}/${total} 个数据源成功`)
      } else {
        ElMessage.warning(`同步完成: ${success}/${total} 个数据源成功,请查看失败详情`)
      }

      // 刷新状态
      await loadDataSources()
      await loadLogs()
    } catch (e) {
      ElMessage.error(`同步失败: ${e.message}`)
    } finally {
      form.password = ''
      syncing.value = false
    }
  })
}

async function handleTest() {
  testing.value = true
  try {
    const res = await testBrowser()
    if (res.data) {
      ElMessage.success('浏览器测试成功')
    } else {
      ElMessage.error('浏览器测试失败')
    }
  } catch (e) {
    ElMessage.error(`测试失败: ${e.message}`)
  } finally {
    testing.value = false
  }
}

function getStepStatus(dataType) {
  const idx = form.dataTypes.indexOf(dataType)
  if (idx === -1) return 'wait'
  if (currentStep.value > idx) return 'success'
  if (currentStep.value === idx) return 'process'
  return 'wait'
}

function getDsName(dataType) {
  const ds = dataSources.value.find((d) => d.data_type === dataType)
  return ds ? ds.name : dataType
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
.mr-4 {
  margin-right: 4px;
}
</style>
