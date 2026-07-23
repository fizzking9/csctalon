<template>
  <div class="page-container">
    <div class="page-title">工作台</div>

    <!-- 汇总卡片 -->
    <div class="card-row">
      <el-card class="stat-card" shadow="hover">
        <div class="stat">
          <div class="stat-icon" style="background: #ecf5ff">
            <el-icon :size="28" color="#409eff"><Money /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">退款总额(元)</div>
            <div class="stat-value">{{ formatMoney(summary.refund_total) }}</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat">
          <div class="stat-icon" style="background: #fdf6ec">
            <el-icon :size="28" color="#e6a23c"><Document /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">退费笔数</div>
            <div class="stat-value">{{ summary.refund_count }}</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat">
          <div class="stat-icon" style="background: #fef0f0">
            <el-icon :size="28" color="#f56c6c"><Wallet /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">真实现金流出(元)</div>
            <div class="stat-value">{{ formatMoney(summary.cash_out_total) }}</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat">
          <div class="stat-icon" style="background: #f0f9eb">
            <el-icon :size="28" color="#67c23a"><Bell /></el-icon>
          </div>
          <div class="stat-body">
            <div class="stat-label">待办事项</div>
            <div class="stat-value">{{ todoCount }}</div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 快捷入口 -->
    <el-card class="chart-card">
      <template #header>快捷入口</template>
      <div class="shortcuts">
        <el-button type="primary" :icon="Upload" @click="$router.push('/data/upload')">上传数据</el-button>
        <el-button :icon="Connection" @click="$router.push('/data/match')">关联匹配</el-button>
        <el-button :icon="Bell" @click="$router.push('/todos')">事项跟进</el-button>
        <el-button :icon="TrendCharts" @click="$router.push('/analytics/operation')">运营分析</el-button>
      </div>
    </el-card>

    <!-- 提示 -->
    <el-alert
      v-if="!summary.refund_count"
      class="mt-16"
      title="暂无业务数据"
      type="info"
      :closable="false"
      description="请前往 数据中心-数据上传 导入退费单、退款工单、钱包提现、商户号退款等数据后查看分析。"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Upload, Connection, Bell, TrendCharts } from '@element-plus/icons-vue'
import { getSummary } from '@/api/analytics'
import { listTodos } from '@/api/todo'

const summary = ref({ refund_total: 0, refund_count: 0, cash_out_total: 0, wallet_balance_total: 0 })
const todoCount = ref(0)

function formatMoney(v) {
  if (v == null) return '0.00'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

onMounted(async () => {
  try {
    const res = await getSummary()
    summary.value = res.data
  } catch (e) {
    /* 忽略,后端可能未启动 */
  }
  try {
    const res = await listTodos({ page: 1, page_size: 1 })
    todoCount.value = res.data.total
  } catch (e) {
    /* 忽略 */
  }
})
</script>

<style scoped>
.stat {
  display: flex;
  align-items: center;
  gap: 16px;
}
.stat-icon {
  width: 56px;
  height: 56px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-label {
  color: #909399;
  font-size: 13px;
}
.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin-top: 4px;
}
.shortcuts {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
</style>
