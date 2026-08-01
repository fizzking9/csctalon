<template>
  <div class="page-container">
    <div class="page-title">分类分析（工单类别 + 退款维度）</div>

    <!-- 行1: 工单类别分布 (新增) + 投诉子类分布 (新增) -->
    <el-row :gutter="16" class="mb-16">
      <el-col :xs="24" :md="12">
        <el-card header="工单类别分布 (含投诉兜底:空类别→投诉)">
          <BaseChart :option="ticketCategoryOption" height="320px" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card header="投诉子类分布（商品/服务/微信/12315/工信部）">
          <BaseChart :option="complaintSubOption" height="320px" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 行2: 服务项退款原因 (按类别分组,原饼图改堆叠柱) + 商户分类 -->
    <el-row :gutter="16" class="mb-16">
      <el-col :xs="24" :md="12">
        <el-card header="按服务项分类(退款原因·按类别分组堆叠)">
          <BaseChart :option="serviceOption" height="340px" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card header="按商户分类">
          <BaseChart :option="merchantOption" height="340px" />
        </el-card>
      </el-col>
    </el-row>

    <el-card header="按套餐分类">
      <BaseChart :option="packageOption" height="320px" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import BaseChart from '@/components/BaseChart.vue'
import { category } from '@/api/analytics'

const service = ref([])
const merchant = ref([])
const pkg = ref([])

// --- 新增: 工单类别分布 饼图 (从 service 数据的 category 字段聚合) ---
const ticketCategoryOption = computed(() => {
  // 按外层 category 聚合 count 求和
  const agg = {}
  for (const s of service.value || []) {
    const cat = s.category || '(未知)'
    agg[cat] = (agg[cat] || 0) + (s.count || 0)
  }
  const data = Object.entries(agg).map(([name, value]) => ({ name, value }))
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c}单 ({d}%)' },
    legend: { type: 'scroll', bottom: 0 },
    color: ['#f56c6c', '#409eff', '#67c23a', '#e6a23c', '#909399', '#9b59b6', '#16a085', '#8e44ad'],
    series: [{
      name: '工单类别', type: 'pie', radius: ['38%', '68%'], center: ['50%', '45%'],
      label: { formatter: '{b}\n{c}单 {d}%', fontSize: 11 },
      data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#f0f0f0' } }],
    }],
  }
})

// --- 新增: 投诉子类分布 饼图 (从 category='投诉' 的 service_item 聚合) ---
const complaintSubOption = computed(() => {
  const agg = {}
  for (const s of service.value || []) {
    if (s.category !== '投诉') continue
    agg[s.name] = (agg[s.name] || 0) + (s.count || 0)
  }
  const data = Object.entries(agg).map(([name, value]) => ({ name, value }))
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c}单 ({d}%)' },
    legend: { type: 'scroll', bottom: 0 },
    color: ['#f56c6c', '#e6a23c', '#909399', '#9b59b6', '#409eff', '#67c23a'],
    series: [{
      name: '投诉子类', type: 'pie', radius: ['38%', '68%'], center: ['50%', '45%'],
      label: { formatter: '{b}\n{c}单 {d}%', fontSize: 11 },
      data: data.length ? data : [{ name: '暂无投诉数据', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '暂无投诉 ✅' } }],
    }],
  }
})

// --- 原有: 服务项退款原因 改为按 category 分组的堆叠柱图 (多系列,每个类别一条) ---
const serviceOption = computed(() => {
  const rows = service.value || []
  if (!rows.length) return { tooltip: {}, series: [] }
  // 获取所有外层 category 作为 series
  const categoriesSet = new Set()
  const itemMap = {}  // serviceItem -> {category: amount}
  for (const r of rows) {
    categoriesSet.add(r.category || '(未知)')
    if (!itemMap[r.name]) itemMap[r.name] = {}
    itemMap[r.name][r.category || '(未知)'] = r.value || 0
  }
  const cats = [...categoriesSet]
  const catColor = { '投诉': '#f56c6c', '退款': '#409eff', '套餐疑问': '#67c23a', '其他': '#e6a23c', '换货': '#909399', '设备故障': '#9b59b6', '网络故障': '#16a085' }
  // 取每个服务项跨所有类别金额之和 Top15
  const topItems = Object.entries(itemMap)
    .map(([name, byCat]) => [name, Object.values(byCat).reduce((a, b) => a + b, 0)])
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([n]) => n)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: cats, type: 'scroll', bottom: 0 },
    grid: { left: 160, right: 20, bottom: 48, top: 10 },
    xAxis: { type: 'value', axisLabel: { formatter: (v) => v >= 10000 ? (v/10000).toFixed(0)+'万' : v } },
    yAxis: {
      type: 'category',
      data: topItems.slice().reverse(),
      axisLabel: { width: 150, overflow: 'truncate', fontSize: 10 },
    },
    series: cats.map((cat) => ({
      name: cat, type: 'bar', stack: 'total',
      itemStyle: { color: catColor[cat] || '#409eff' },
      data: topItems.map((nm) => itemMap[nm]?.[cat] || 0).reverse(),
    })),
  }
})

const merchantOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 140, right: 30, bottom: 30, top: 20 },
  xAxis: { type: 'value' },
  yAxis: { type: 'category', data: merchant.value.map((m) => m.name).reverse() },
  series: [{ type: 'bar', data: merchant.value.map((m) => m.value).reverse(), itemStyle: { color: '#409eff' } }],
}))

const packageOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 200, right: 30, bottom: 30, top: 20 },
  xAxis: { type: 'value' },
  yAxis: { type: 'category', data: pkg.value.map((p) => p.name).reverse() },
  series: [{ type: 'bar', data: pkg.value.map((p) => p.value).reverse(), itemStyle: { color: '#67c23a' } }],
}))

async function loadAll() {
  try {
    const [s, m, p] = await Promise.all([
      category.serviceItem(),
      category.merchant({ limit: 15 }),
      category.package({ limit: 15 }),
    ])
    service.value = s.data
    merchant.value = m.data
    pkg.value = p.data
  } catch (e) {}
}

onMounted(loadAll)
</script>
