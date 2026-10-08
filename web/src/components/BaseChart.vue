<template>
  <div ref="chartRef" :style="{ height: height, width: '100%' }"></div>
</template>

<script setup>
// BaseChart: ECharts 包装组件。
// #233 B1.5-2 新增 chartClick 事件 emit, 用于趋势图 markPoint/markLine 点击
// 弹事件详情;其他用法保持不变, 默认不监听 click。
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: String, default: '320px' },
})
const emit = defineEmits(['chartClick'])

const chartRef = ref(null)
let chart = null

function renderChart() {
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value)
    // #233 绑定 click 事件一次 (避免 setOption 时重复绑定)
    chart.on('click', (params) => {
      // 只把 markPoint / markLine 类点击透出去 (componentType 区分)
      // series 也透出, 调用方按需过滤
      emit('chartClick', params)
    })
  }
  chart.setOption(props.option, true)
}

function handleResize() {
  chart?.resize()
}

onMounted(async () => {
  await nextTick()
  renderChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

watch(() => props.option, renderChart, { deep: true })
</script>
