<template>
  <div class="page-container">
    <ExternalTokenPanel />
  </div>
</template>

<script setup>
// 外部接入页 —— 实现已抽到 components/ExternalTokenPanel.vue,
// 供本页与「接入中心」(#44) 共用同一份逻辑, 避免双份实现漂移。
import ExternalTokenPanel from '@/components/ExternalTokenPanel.vue'
</script>
