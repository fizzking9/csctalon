<template>
  <div class="collection-logs" :class="{ 'logs-embedded': embedded }">
    <!-- 筛选栏 -->
    <div class="flex-between mb-12">
      <div class="flex gap-8 flex-wrap">
        <el-select v-model="filter.source" placeholder="来源" clearable style="width: 130px" @change="loadData">
          <el-option v-for="s in sourceOptions" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
        <el-select v-model="filter.data_type" placeholder="数据类型" clearable style="width: 160px" filterable @change="loadData">
          <el-option v-for="t in typeOptions" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 120px" @change="loadData">
          <el-option label="成功" value="success" />
          <el-option label="失败" value="failed" />
          <el-option label="进行中" value="running" />
          <el-option label="待处理" value="pending" />
        </el-select>
      </div>
      <el-button :icon="Refresh" @click="loadData">刷新</el-button>
    </div>

    <!-- 日志表格 -->
    <el-card :shadow="embedded ? 'never' : 'always'">
      <el-table v-loading="loading" :data="list" border stripe size="small" :max-height="tableMaxHeight">
        <el-table-column label="来源" width="90" fixed>
          <template #default="{ row }">
            <el-tag :type="sourceTagType(row.source)" size="small">{{ row.source_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160" sortable>
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="数据类型" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.data_type_label || row.data_type || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag
              :type="row.status === 'success' || row.status === 'imported' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'"
              size="small"
            >{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="触发" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.trigger_label" size="small" effect="plain">{{ row.trigger_label }}</el-tag>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="triggered_by" label="操作人/来源" width="100" show-overflow-tooltip />
        <el-table-column label="统计" width="200">
          <template #default="{ row }">
            <span class="stats-text">
              <template v-if="row.total_rows != null">总<b>{{ row.total_rows }}</b></template>
              <template v-if="row.inserted_rows != null"> / 新<b>{{ row.inserted_rows }}</b></template>
              <template v-if="row.updated_rows != null"> / 更<b>{{ row.updated_rows }}</b></template>
              <template v-if="row.failed_rows != null && row.failed_rows > 0"> / <span class="text-danger">失<b>{{ row.failed_rows }}</b></span></template>
              <span v-if="row.total_rows == null && row.inserted_rows == null" class="dim">—</span>
            </span>
          </template>
        </el-table-column>
        <!-- #42 abc-c: 拒收行 / 入库前校验(校验告警≠失败, 单独列以示区分) -->
        <el-table-column label="拒收/校验" width="120">
          <template #default="{ row }">
            <div class="reject-cell">
              <el-tooltip
                v-if="rejectedTag(row.rejected_rows).show"
                :content="rejectedTag(row.rejected_rows).tip"
                placement="top"
              >
                <el-tag type="danger" size="small" effect="plain">
                  {{ rejectedTag(row.rejected_rows).text }}
                </el-tag>
              </el-tooltip>
              <el-tooltip
                v-if="validationTag(row.validation_count, row.validation_block, row.validation_warn).show"
                :content="validationTag(row.validation_count, row.validation_block, row.validation_warn).tip"
                placement="top"
              >
                <el-tag type="warning" size="small" effect="plain">
                  {{ validationTag(row.validation_count, row.validation_block, row.validation_warn).text }}
                </el-tag>
              </el-tooltip>
              <span v-if="showRejectPlaceholder(row.rejected_rows, row.validation_count)" class="dim">—</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="filename" label="文件/来源" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.filename" class="filename-text">{{ row.filename }}</span>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <el-text v-if="row.error" type="danger" size="small">{{ row.error }}</el-text>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="完成时间" width="160">
          <template #default="{ row }">{{ row.finished_at ? formatTime(row.finished_at) : '—' }}</template>
        </el-table-column>
      </el-table>
      <div class="mt-12 flex-between">
        <span class="dim">共 {{ total }} 条</span>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          background
          size="small"
          @current-change="loadData"
          @size-change="page = 1; loadData()"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { listCollectionLogs, listLogSources, listLogDataTypes } from '@/api/collectionLogs'
import { rejectedTag, validationTag, showRejectPlaceholder } from '@/utils/collectionStats'
import dayjs from 'dayjs'

const props = defineProps({
  embedded: { type: Boolean, default: false },
})

const list = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(50)
const filter = reactive({ source: '', data_type: '', status: '' })
const sourceOptions = ref([])
const typeOptions = ref([])
// 采集进行中(表格存在 running 记录) → 加快轮询, 让用户实时看到进度
const hasRunning = ref(false)
// 轮询间隔: 有 running 5s; 空闲 30s
let pollTimer = null
let _polling = false

const tableMaxHeight = computed(() => props.embedded ? 560 : 640)

const sourceTagType = (s) => ({
  upload: '',
  bms: 'success',
  scrm: 'warning',
  push: 'info',
  shared_doc: 'primary',
}[s] || 'info')

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '')

async function loadData() {
  if (_polling) return // 防重入(慢响应时避免叠请求)
  _polling = true
  loading.value = true
  try {
    const params = {
      page: page.value,
      page_size: pageSize.value,
      limit: 500,
      // #36: 采集日志需实时; 云端 OpenResty 对 GET 有 proxy_cache(x-cache: HIT),
      // 不带随机参数会命中旧缓存(同步成功但日志看不到最新)。加时间戳绕开缓存键。
      _t: Date.now(),
    }
    if (filter.source) params.source = filter.source
    if (filter.data_type) params.data_type = filter.data_type
    if (filter.status) params.status = filter.status
    const res = await listCollectionLogs(params)
    const rows = res.data || []
    // 判断是否有进行中任务 → 动态调整刷新节奏
    hasRunning.value = rows.some((r) => r.status === 'running' || r.status === 'pending' || r.status === 'parsing')
    list.value = rows
    total.value = rows.length
  } finally {
    loading.value = false
    _polling = false
  }
}

async function loadOptions() {
  try {
    const [srcRes, typeRes] = await Promise.all([
      listLogSources(),
      listLogDataTypes(),
    ])
    sourceOptions.value = srcRes.data || []
    typeOptions.value = typeRes.data || []
  } catch {
    // 静默失败
  }
}

// 轮询: 采集进行中 5s(实时看进度), 空闲 30s(省资源)。递归 setTimeout, 避免 interval 叠加。
function _schedulePoll() {
  const intervalMs = hasRunning.value ? 5000 : 30000
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = setTimeout(async () => {
    await loadData()
    _schedulePoll() // 依据最新 hasRunning 决定下一轮间隔
  }, intervalMs)
}

onMounted(() => {
  loadData()
  loadOptions()
  _schedulePoll()
})

onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<style scoped>
.collection-logs { font-size: 13px; }
.collection-logs.logs-embedded { padding: 0; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.flex { display: flex; }
.gap-8 { gap: 8px; }
.flex-wrap { flex-wrap: wrap; }
.mb-12 { margin-bottom: 12px; }
.mt-12 { margin-top: 12px; }
.dim { color: #909399; }
.text-danger { color: #f56c6c; }
.stats-text { font-size: 12px; }
.stats-text b { font-weight: 600; }
.filename-text { font-size: 12px; color: #606266; }
/* #42 abc-c: 拒收/校验列 */
.reject-cell { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.reject-cell .dim { color: #909399; }
</style>