<template>
  <div class="page-container device-flow-audit">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">设备流量审计</div>
        <div class="page-sub">
          输入设备 SN 按月拉取 BMS 上游月流量并入库(幂等); 不完整月可反复重取更新。
          数据存于本地 device_flow_usage, 用于设备审计(单 SN × 月流量汇聚)。
        </div>
      </div>
    </div>

    <!-- 拉取操作 -->
    <el-card class="mb-16" shadow="never">
      <div class="flex gap-8" style="flex-wrap: wrap; align-items: center">
        <el-input
          v-model="pull.sn"
          placeholder="设备SN(一次一个)"
          clearable
          style="width: 240px"
          @keyup.enter="onPull"
        />
        <el-date-picker
          v-model="pull.month"
          type="month"
          placeholder="月份(空=全量有流量月)"
          value-format="YYYY-MM"
          style="width: 200px"
        />
        <el-button type="primary" :loading="pulling" @click="onPull">拉取入库</el-button>
        <el-button @click="onViewSn">查看该SN历史</el-button>
      </div>
      <el-alert
        v-if="pullResult"
        :title="pullResultText"
        type="success"
        :closable="false"
        class="mt-8"
        show-icon
      />
    </el-card>

    <!-- 本地历史 -->
    <el-card shadow="never">
      <template #header>
        <div class="flex-between">
          <span>本地月流量历史</span>
          <div class="flex gap-8">
            <el-input v-model="filter.sn" placeholder="SN(模糊)" clearable style="width: 180px" @keyup.enter="onSearch" />
            <el-input v-model="filter.merchant" placeholder="商户(模糊)" clearable style="width: 200px" @keyup.enter="onSearch" />
            <el-input v-model="filter.month" placeholder="月份 YYYY-MM" clearable style="width: 140px" @keyup.enter="onSearch" />
            <el-button type="primary" @click="onSearch">查询</el-button>
          </div>
        </div>
      </template>
      <el-table v-loading="loading" :data="list" border stripe max-height="560">
        <el-table-column prop="device_sn" label="设备SN" width="180" show-overflow-tooltip />
        <el-table-column prop="merchant_name" label="商户名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="stat_month" label="月份" width="100" />
        <el-table-column prop="flow_gb" label="流量(GB)" width="110" align="right">
          <template #default="{ row }">
            {{ row.flow_gb != null ? Number(row.flow_gb) : '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="device_combo_name" label="当前套餐" min-width="150" show-overflow-tooltip />
        <el-table-column prop="device_type_name" label="设备类型" width="130" show-overflow-tooltip />
        <el-table-column label="完整月" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.month_complete" type="success" size="small">完整</el-tag>
            <el-tag v-else type="warning" size="small">进行中</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fetched_at" label="抓取时间" width="170">
          <template #default="{ row }">{{ fmt(row.fetched_at) }}</template>
        </el-table-column>
      </el-table>
      <el-pagination
        class="mt-16"
        background
        layout="total, prev, pager, next, sizes"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        @current-change="onPage"
        @size-change="onSize"
      />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import {
  fetchDeviceFlow,
  listDeviceFlowUsage,
  getDeviceFlowBySn,
} from '@/api/deviceFlow'

const pull = reactive({ sn: '', month: null })
const pulling = ref(false)
const pullResult = ref(null)

const filter = reactive({ sn: '', merchant: '', month: '' })
const page = ref(1)
const pageSize = ref(20)
const list = ref([])
const total = ref(0)
const loading = ref(false)

function fmt(t) { return t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-' }

const pullResultText = computed(() => {
  const r = pullResult.value
  if (!r) return ''
  const months = (r.months || []).join(', ') || '-'
  return `SN ${r.sn} ${r.month ? `(${r.month})` : ''}: 拉取 ${r.fetched_rows} 行 / 新增 ${r.inserted} / 更新 ${r.updated} / 总流量 ${r.total_flow_gb}GB / 月份: ${months} — ${r.message || ''}`
})

async function onPull() {
  const sn = (pull.sn || '').trim()
  if (!sn) { ElMessage.warning('请输入设备SN'); return }
  pulling.value = true
  pullResult.value = null
  try {
    const res = await fetchDeviceFlow(sn, pull.month)
    pullResult.value = res.data
    ElMessage.success(`拉取完成: 新增 ${res.data.inserted}, 更新 ${res.data.updated}`)
    // 拉取后自动刷新历史(若查询条件匹配该 SN)
    if (sn) { filter.sn = sn; await onSearch() }
  } catch (e) {
    ElMessage.error(`拉取失败: ${e.message}`)
  } finally {
    pulling.value = false
  }
}

async function onViewSn() {
  const sn = (pull.sn || '').trim()
  if (!sn) { ElMessage.warning('请输入设备SN'); return }
  filter.sn = sn
  page.value = 1
  await onSearch()
}

function toParams() {
  const p = { page: page.value, page_size: pageSize.value }
  if (filter.sn) p.sn = filter.sn
  if (filter.merchant) p.merchant = filter.merchant
  if (filter.month) p.month = filter.month
  return p
}

async function onSearch() {
  loading.value = true
  try {
    const res = await listDeviceFlowUsage(toParams())
    list.value = res.data.items || []
    total.value = res.data.total || 0
  } catch (e) {
    ElMessage.error(`查询失败: ${e.message}`)
  } finally {
    loading.value = false
  }
}

function onPage(p) { page.value = p; onSearch() }
function onSize(s) { pageSize.value = s; page.value = 1; onSearch() }

onMounted(onSearch)
</script>
