<template>
  <div class="page-container">
    <div class="page-title">关联匹配</div>

    <!-- 汇总卡片 -->
    <el-row :gutter="16" class="mb-16">
      <el-col :span="8">
        <el-card shadow="hover">
          <div class="match-stat">
            <div class="match-label">退费单总数</div>
            <div class="match-value">{{ summary.refund_total || 0 }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover">
          <div class="match-stat">
            <div class="match-label">未关联商户退款</div>
            <div class="match-value" :class="{ 'text-danger': summary.refund_without_merchant_refund > 0 }">
              {{ summary.refund_without_merchant_refund || 0 }}
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover">
          <div class="match-stat">
            <div class="match-label">未关联工单</div>
            <div class="match-value" :class="{ 'text-warning': summary.refund_without_ticket > 0 }">
              {{ summary.refund_without_ticket || 0 }}
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="mb-16">
      <div class="flex-between">
        <div>
          <el-text>每次导入新数据后系统会自动执行增量匹配。也可手动触发全量重跑。</el-text>
        </div>
        <div class="flex gap-8">
          <el-button :icon="Refresh" :loading="matching" @click="handleMatch(true)">增量匹配</el-button>
          <el-button type="primary" :icon="Refresh" :loading="matching" @click="handleMatch(false)">全量重跑</el-button>
        </div>
      </div>
      <el-alert v-if="lastMatchResult" class="mt-16" type="success" :closable="false" :title="matchResultText" />
    </el-card>

    <el-card>
      <template #header>
        <div class="flex-between">
          <span>未关联商户退款的退费单(待人工补关联)</span>
          <el-button :icon="Refresh" text @click="loadUnmatched">刷新</el-button>
        </div>
      </template>
      <el-table :data="unmatchedList" v-loading="loading" border stripe size="small">
        <el-table-column prop="refund_no" label="退费单号" width="220" />
        <el-table-column prop="platform_order_no" label="平台订单号" width="200" show-overflow-tooltip />
        <el-table-column prop="device_sn" label="设备SN" width="160" />
        <el-table-column prop="merchant_name" label="商户名称" show-overflow-tooltip />
        <el-table-column label="退费金额" width="100">
          <template #default="{ row }">{{ row.refund_amount ? Number(row.refund_amount).toFixed(2) : '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at_raw) }}</template>
        </el-table-column>
      </el-table>
      <div class="mt-16 flex-between">
        <span>共 {{ unmatchedTotal }} 条</span>
        <el-pagination
          v-model:current-page="page"
          :total="unmatchedTotal"
          :page-size="20"
          layout="prev, pager, next"
          @current-change="loadUnmatched"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { matchSummary, runMatch, listUnmatchedRefunds } from '@/api/upload'
import dayjs from 'dayjs'

const summary = ref({})
const matching = ref(false)
const loading = ref(false)
const lastMatchResult = ref(null)
const unmatchedList = ref([])
const unmatchedTotal = ref(0)
const page = ref(1)

const matchResultText = computed(() => {
  if (!lastMatchResult.value) return ''
  const r = lastMatchResult.value
  const total = Object.values(r).reduce((a, b) => a + b, 0)
  const parts = Object.entries(r).filter(([, v]) => v > 0).map(([k, v]) => `${k}:${v}`)
  return `匹配完成,新增 ${total} 条关联${parts.length ? ' (' + parts.join(', ') + ')' : ''}`
})

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '')

async function loadSummary() {
  try {
    const res = await matchSummary()
    summary.value = res.data
  } catch (e) {}
}

async function handleMatch(onlyUnmatched) {
  matching.value = true
  try {
    const res = await runMatch(onlyUnmatched)
    lastMatchResult.value = res.data
    ElMessage.success(`匹配完成,新增 ${Object.values(res.data).reduce((a, b) => a + b, 0)} 条关联`)
    await loadSummary()
    await loadUnmatched()
  } finally {
    matching.value = false
  }
}

async function loadUnmatched() {
  loading.value = true
  try {
    const res = await listUnmatchedRefunds({ page: page.value, page_size: 20 })
    unmatchedList.value = res.data.items
    unmatchedTotal.value = res.data.total
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadSummary()
  await loadUnmatched()
})
</script>

<style scoped>
.match-stat {
  text-align: center;
}
.match-label {
  color: #909399;
  font-size: 13px;
}
.match-value {
  font-size: 28px;
  font-weight: 600;
  margin-top: 4px;
}
</style>
