<template>
  <div class="page-container">
    <AuditView subject-kind="device" :use-route-query="true" />
  </div>
</template>

<script setup>
import AuditView from '@/components/AuditView.vue'
</script>
