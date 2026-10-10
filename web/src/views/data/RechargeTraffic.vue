<template>
  <div class="page-container recharge-traffic">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">流量与充值查询</div>
        <div class="page-sub">
          本地口径基于本库 device_combos(来源:BMS/导出同步);上游实时口径占位(待你提供真实接口文档/抓包后填充)。
        </div>
      </div>
    </div>

    <el-tabs v-model="activeTab" type="card">
      <!-- 本地(默认) -->
      <el-tab-pane label="本地数据" name="local">
        <el-card class="mb-16" shadow="never">
          <div class="flex gap-8" style="flex-wrap: wrap; align-items: center">
            <el-input v-model="filter.sn" placeholder="设备SN" clearable style="width: 200px" @keyup.enter="onSearch" />
            <el-input v-model="filter.merchant" placeholder="商户(模糊)" clearable style="width: 220px" @keyup.enter="onSearch" />
            <el-select v-model="filter.package_type" placeholder="套餐类型" clearable style="width: 150px">
              <el-option label="续费套餐" value="续费套餐" />
              <el-option label="新购套餐" value="新购套餐" />
              <el-option label="充值券" value="充值券" />
            </el-select>
            <el-select v-model="filter.status" placeholder="状态" clearable style="width: 130px">
              <el-option label="流量正常" value="流量正常" />
              <el-option label="已过期" value="已过期" />
              <el-option label="已用完" value="已用完" />
            </el-select>
            <el-select v-model="filter.time_field" style="width: 180px">
              <el-option label="按充值时间" value="created_at_raw" />
              <el-option label="按开始时间" value="start_date" />
              <el-option label="按结束时间" value="end_date" />
            </el-select>
            <el-date-picker
              v-model="filter.range"
              type="daterange"
              range-separator="至"
              start-placeholder="起"
              end-placeholder="止"
              value-format="YYYY-MM-DD"
              style="width: 260px"
            />
            <el-button type="primary" @click="onSearch">查询</el-button>
            <el-button @click="resetFilter">重置</el-button>
          </div>
        </el-card>

        <!-- 汇总 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :span="6">
            <el-card v-loading="summaryLoading" shadow="hover">
              <div class="stat-label">充值笔数</div>
              <div class="stat-value">{{ summary.recharge_count || 0 }}</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card v-loading="summaryLoading" shadow="hover">
              <div class="stat-label">总金额(¥)</div>
              <div class="stat-value">{{ Number(summary.total_amount || 0).toFixed(2) }}</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card v-loading="summaryLoading" shadow="hover">
              <div class="stat-label">已用流量</div>
              <div class="stat-value">{{ summary.used_traffic_human || '-' }}</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card v-loading="summaryLoading" shadow="hover">
              <div class="stat-label">剩余流量</div>
              <div class="stat-value">{{ summary.remaining_traffic_human || '-' }}</div>
            </el-card>
          </el-col>
        </el-row>

        <!-- 列表 -->
        <el-card shadow="never">
          <el-table v-loading="loading" :data="list" border stripe max-height="560">
            <el-table-column prop="recharge_order_no" label="充值订单号" width="200" show-overflow-tooltip />
            <el-table-column prop="device_sn" label="设备SN" width="180" show-overflow-tooltip />
            <el-table-column prop="package_name" label="套餐" min-width="180" show-overflow-tooltip />
            <el-table-column prop="package_type" label="类型" width="110" />
            <el-table-column prop="traffic_type" label="流量类型" width="110" />
            <el-table-column label="实际金额(¥)" width="110" align="right">
              <template #default="{ row }">
                {{ row.actual_amount == null ? '-' : Number(row.actual_amount).toFixed(2) }}
              </template>
            </el-table-column>
            <el-table-column label="已用" width="120">
              <template #default="{ row }">{{ row.used_traffic_human || '-' }}</template>
            </el-table-column>
            <el-table-column label="剩余" width="120">
              <template #default="{ row }">{{ row.remaining_traffic_human || '-' }}</template>
            </el-table-column>
            <el-table-column label="有效期" width="320">
              <template #default="{ row }">
                {{ fmt(row.start_date) || '-' }} ~ {{ fmt(row.end_date) || '-' }}
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="110" />
          </el-table>
          <div class="mt-16 flex-between">
            <span>共 {{ total }} 条</span>
            <el-pagination
              layout="total, sizes, prev, pager, next"
              :total="total"
              :current-page="page"
              :page-size="pageSize"
              :page-sizes="[20, 50, 100, 200]"
              @size-change="(s) => { pageSize = s; page = 1; onSearch() }"
              @current-change="(p) => { page = p; onSearch() }"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 上游占位 -->
      <el-tab-pane label="上游实时" name="upstream">
        <el-card shadow="never">
          <el-alert type="info" :closable="false" show-icon class="mb-16">
            <template #title>实时流量已接入 BMS 真实上游(deviceFlow/sn/pageList)。</template>
            <div style="margin-top: 8px; line-height: 1.8">
              <div>实时流量 = 本地套餐快照(device_combos) + 月流量使用(device_flow_usage, 本地无该SN数据时自动拉一次上游)。</div>
              <div style="margin-top: 4px">#185 清理: 充值订单上游查询长期无真实接口文档支撑, 占位接口/按钮已删除。</div>
            </div>
          </el-alert>
          <div class="flex gap-8" style="flex-wrap: wrap">
            <el-input v-model="upstreamFilter.sn" placeholder="设备SN(单台)" clearable style="width: 220px" @keyup.enter="queryUpstreamTrafficClick" />
            <el-button type="success" :loading="trafficLoading" @click="queryUpstreamTrafficClick">查询流量(拉最新)</el-button>
          </div>
          <div class="mt-8" style="color: #909399; font-size: 12px; line-height: 1.7">
            本地已有该 SN 数据也会先展示(秒回), 本工具页「查询流量」会再触发一次 BMS 上游拉取覆盖入库;
            设备行为分析里的「流量」区块默认只读本地, 点「刷新最新数据」才拉上游。
          </div>

          <!-- 实时流量结果 -->
          <div v-if="trafficInfo" class="mt-16">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="设备SN">{{ trafficInfo.device_sn || '-' }}</el-descriptions-item>
              <el-descriptions-item v-if="trafficInfo.quota_latest" label="当前套餐">
                {{ trafficInfo.quota_latest.package_name || '-' }}
                <el-tag v-if="trafficInfo.quota_latest.status" size="small" effect="plain" style="margin-left: 6px">{{ trafficInfo.quota_latest.status }}</el-tag>
                <span v-if="trafficInfo.quota_latest.end_date" style="margin-left: 8px; color: #909399">至 {{ fmt(trafficInfo.quota_latest.end_date) }}</span>
              </el-descriptions-item>
              <el-descriptions-item v-if="trafficInfo.quota_latest" label="套餐流量">
                可用 {{ trafficInfo.quota_latest.available_human || '-' }} · 已用 {{ trafficInfo.quota_latest.used_human || '-' }} · 剩余 {{ trafficInfo.quota_latest.remaining_human || '-' }}
              </el-descriptions-item>
              <el-descriptions-item v-if="trafficInfo.quota_summary" label="累计充值">
                {{ trafficInfo.quota_summary.count || 0 }} 笔 · ¥{{ Number(trafficInfo.quota_summary.total_amount || 0).toFixed(2) }} · 剩余 {{ trafficInfo.quota_summary.remaining_human || '-' }}
              </el-descriptions-item>
            </el-descriptions>

            <div class="mt-16">
              <div style="margin-bottom: 6px; font-weight: 600">月流量使用({{ trafficInfo.monthly_usage?.length || 0 }} 个月, 共 {{ trafficInfo.total_flow_gb || 0 }} GB)</div>
              <el-table v-if="trafficInfo.monthly_usage && trafficInfo.monthly_usage.length" :data="trafficInfo.monthly_usage" size="small" border stripe max-height="320">
                <el-table-column prop="stat_month" label="月份" width="110" />
                <el-table-column label="流量(GB)" width="120" align="right">
                  <template #default="{ row }">{{ row.flow_gb != null ? Number(row.flow_gb) : '-' }}</template>
                </el-table-column>
                <el-table-column label="完整月" width="100" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.month_complete" type="success" size="small">完整</el-tag>
                    <el-tag v-else type="warning" size="small">进行中</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="device_combo_name" label="当前套餐" min-width="140" show-overflow-tooltip />
              </el-table>
              <el-empty v-else description="上游未返回月流量记录" :image-size="60" />
              <div v-if="trafficInfo.fetch_msg" class="mt-8" style="color: #909399; font-size: 12px">{{ trafficInfo.fetch_msg }}</div>
            </div>
          </div>
          <div v-else class="mt-16 todo-result">
            <el-empty description="输入设备 SN 后点击「查询流量」" />
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import {
  listLocalCombos,
  getLocalCombosSummary,
  queryUpstreamTraffic,
} from '@/api/recharge'

const activeTab = ref('local')

// ---------- 本地查询 ----------
const filter = reactive({
  sn: '', merchant: '', package_type: '', status: '', time_field: 'created_at_raw', range: [],
})
const page = ref(1)
const pageSize = ref(20)
const list = ref([])
const total = ref(0)
const loading = ref(false)
const summary = reactive({})
const summaryLoading = ref(false)

function fmt(t) { return t ? dayjs(t).format('YYYY-MM-DD') : '' }

function toParams() {
  const p = { page: page.value, page_size: pageSize.value, sn: filter.sn || undefined, merchant: filter.merchant || undefined, package_type: filter.package_type || undefined, status: filter.status || undefined, time_field: filter.time_field }
  if ((filter.range || []).length === 2) {
    p.start = filter.range[0]
    p.end = filter.range[1]
  }
  return p
}

async function onSearch() {
  loading.value = true
  try {
    const params = toParams()
    const [res1, res2] = await Promise.all([listLocalCombos(params), getLocalCombosSummary(params)])
    const d = res1.data || {}
    list.value = d.items || []
    total.value = d.total || 0
    Object.assign(summary, res2.data || {})
  } finally {
    loading.value = false
    summaryLoading.value = false
  }
}

function resetFilter() {
  Object.assign(filter, { sn: '', merchant: '', package_type: '', status: '', time_field: 'created_at_raw', range: [] })
  page.value = 1
  onSearch()
}

// initial load
onSearch()

// ---------- 上游(流量已接入真实 BMS; #185 清理: 删除未接入的充值订单占位按钮/函数) ----------
const upstreamFilter = reactive({ sn: '', merchant: '', range: [] })
const trafficInfo = ref(null)
const trafficLoading = ref(false)

async function queryUpstreamTrafficClick() {
  const sn = (upstreamFilter.sn || '').trim()
  if (!sn) { ElMessage.warning('请输入设备SN'); return }
  trafficLoading.value = true
  trafficInfo.value = null
  try {
    // 工具页=主动拉最新(refresh=true 触发 BMS 上游覆盖入库后重读)
    const res = await queryUpstreamTraffic({ sn, refresh: true })
    trafficInfo.value = res.data?.result || null
  } catch (e) {
    ElMessage.error(`查询失败: ${e.message}`)
  } finally {
    trafficLoading.value = false
  }
}
</script>

<style scoped>
.flex { display: flex; }
.flex-between { display: flex; align-items: center; justify-content: space-between; }
.gap-8 { gap: 8px; }
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.page-title { font-size: 18px; font-weight: 600; color: #303133; }
.page-sub { margin-top: 4px; font-size: 12px; color: #909399; }

.stat-label { font-size: 12px; color: #909399; }
.stat-value { margin-top: 6px; font-size: 22px; font-weight: 600; color: #303133; }
.todo-result { min-height: 220px; display: flex; align-items: center; justify-content: center; }
</style>
