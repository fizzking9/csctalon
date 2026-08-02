<template>
  <div class="filter-bar-wrap">
    <slot name="left" />
    <el-radio-group v-if="quickOptions && quickOptions.length" v-model="innerQuick" size="small" @change="onQuickChange">
      <el-radio-button v-for="opt in quickOptions" :key="opt.value" :label="opt.value">
        {{ opt.label }}
      </el-radio-button>
    </el-radio-group>

    <el-date-picker
      v-if="showCustom"
      v-model="innerRange"
      :type="datePickerType"
      size="small"
      value-format="YYYY-MM-DD"
      :range-separator="datePickerType === 'daterange' ? '至' : undefined"
      :start-placeholder="startPlaceholder"
      :end-placeholder="endPlaceholder"
      :placeholder="datePickerType === 'date' ? '选择日期' : '选择范围'"
      :style="{ width: datePickerType === 'date' ? '160px' : '240px', marginLeft: '8px' }"
      @change="onRangeChange"
    />

    <el-radio-group v-if="showGrain && grainOptions && grainOptions.length" v-model="innerGrain" size="small" style="margin-left: 8px" @change="onGrainChange">
      <el-radio-button v-for="g in grainOptions" :key="g.value" :label="g.value">{{ g.label }}</el-radio-button>
    </el-radio-group>

    <slot name="middle" />

    <el-button v-if="showRefresh" :icon="Refresh" :loading="loading" @click="onRefresh" style="margin-left:8px">刷新</el-button>

    <slot name="right" />
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: { type: [String, Array, Number], default: '' },        // 当前 quick 值 (v-model:modelValue)
  range: { type: Array, default: () => [] },                        // 自定义日期 (v-model:range)
  grain: { type: String, default: 'day' },                           // 粒度 (v-model:grain)
  quickOptions: { type: Array, default: () => [] },                  // [{ value:'today', label:'今日' }, ...]
  grainOptions: { type: Array, default: () => [] },                  // [{ value:'day', label:'按日' }, ...]
  showCustom: { type: Boolean, default: true },
  showGrain: { type: Boolean, default: false },
  showRefresh: { type: Boolean, default: true },
  loading: { type: Boolean, default: false },
  datePickerType: { type: String, default: 'daterange' },            // 'date' | 'daterange'
  startPlaceholder: { type: String, default: '开始' },
  endPlaceholder: { type: String, default: '结束' },
})
const emit = defineEmits([
  'update:modelValue', 'update:range', 'update:grain',
  'change', 'rangeChange', 'grainChange', 'refresh'
])

const innerQuick = ref(props.modelValue)
const innerRange = ref(props.range?.slice?.() || [])
const innerGrain = ref(props.grain)

watch(() => props.modelValue, v => innerQuick.value = v)
watch(() => props.range, v => innerRange.value = v?.slice?.() || [], { deep: true })
watch(() => props.grain, v => innerGrain.value = v)

function onQuickChange(v) {
  emit('update:modelValue', v)
  emit('change', v)
}
function onRangeChange(v) {
  emit('update:range', v)
  emit('rangeChange', v)
}
function onGrainChange(v) {
  emit('update:grain', v)
  emit('grainChange', v)
}
function onRefresh() { emit('refresh') }
</script>

<style scoped>
.filter-bar-wrap {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0;
}
</style>
