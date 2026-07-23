<template>
  <div class="page-container">
    <div class="page-title">退款分类分析</div>

    <el-row :gutter="16" class="mb-16">
      <el-col :span="12">
        <el-card header="按服务项分类(退款原因)">
          <BaseChart :option="serviceOption" height="320px" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card header="按商户分类">
          <BaseChart :option="merchantOption" height="320px" />
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

const serviceOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {c}元 ({d}%)' },
  legend: { type: 'scroll', bottom: 0 },
  series: [{ type: 'pie', radius: ['35%', '65%'], data: service.value.map((s) => ({ name: s.name, value: s.value })) }],
}))

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
