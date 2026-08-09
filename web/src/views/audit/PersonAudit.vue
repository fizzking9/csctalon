<template>
  <div class="page-container">
    <AuditView subject-kind="person" :use-route-query="true" />
  </div>
</template>

<script setup>
import AuditView from '@/components/AuditView.vue'
</script>
