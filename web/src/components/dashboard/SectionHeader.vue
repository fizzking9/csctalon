<template>
  <h2 class="section-title" :class="{ 'with-sub': !!sub }">
    {{ title }}
    <span v-if="sub" class="section-sub">（{{ sub }}）</span>
    <slot name="suffix" />
  </h2>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  sub: { type: String, default: '' },
})
</script>

<style scoped>
.section-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 14px;
  padding-left: 10px;
  border-left: 3px solid #409eff;
  line-height: 1.2;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.section-sub {
  font-size: 12px;
  color: #909399;
  font-weight: 400;
}
</style>
