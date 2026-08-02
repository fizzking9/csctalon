<template>
  <el-card shadow="never" class="chart-card" :body-style="bodyStyle" :v-loading="loading">
    <template #header>
      <div class="card-header">
        <span class="chart-title">{{ title }}</span>
        <div class="chart-extra">
          <slot name="extra" />
        </div>
      </div>
    </template>
    <slot />
  </el-card>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  bodyStyle: { type: String, default: 'padding: 4px 6px' },
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: -8px 0;
}
.chart-title {
  font-size: 13px;
  color: #606266;
  font-weight: 500;
  padding: 4px 8px;
}
.chart-extra {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
