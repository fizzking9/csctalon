<template>
  <el-dialog
    v-model="visible"
    append-to-body
    :width="'min(1100px, 96vw)'"
    top="2vh"
    class="audit-dialog"
    destroy-on-close
  >
    <template #header>
      <div class="audit-dialog-header">
        <span class="audit-dialog-title">分析</span>
        <el-radio-group v-model="kind" size="small">
          <el-radio-button value="device">设备分析</el-radio-button>
          <el-radio-button value="person">人员分析</el-radio-button>
        </el-radio-group>
      </div>
    </template>

    <AuditView
      :key="viewKey"
      :subject-kind="kind"
      :use-route-query="false"
      :initial-id="state.id"
      :initial-keyword="state.name"
      :default-days="1"
      search-width="100%"
      compact
    />
  </el-dialog>
</template>

<script setup>
import { computed } from 'vue'
import { useAudit } from '@/composables/useAudit'
import AuditView from '@/components/AuditView.vue'

const { state, closeAudit } = useAudit()

const visible = computed({
  get: () => state.visible,
  set: (v) => {
    if (!v) closeAudit()
  },
})

const kind = computed({
  get: () => state.kind,
  set: (v) => {
    state.kind = v
  },
})

// 主体或类型变化时重建 AuditView, 触发重新加载
const viewKey = computed(
  () => `${state.kind}:${state.id ?? ''}:${state.name}`,
)
</script>

<style scoped>
.audit-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.audit-dialog-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
</style>

<style>
/* 弹窗内容区紧凑化:缩小 header/body/footer 间距, body 可滚动 */
.audit-dialog :deep(.el-dialog__header) {
  padding: 10px 16px 8px;
  margin-right: 40px;
  border-bottom: 1px solid #f0f0f0;
}
.audit-dialog :deep(.el-dialog__body) {
  padding: 8px 12px 10px;
  max-height: calc(96vh - 100px);
  overflow-y: auto;
}
.audit-dialog :deep(.el-dialog__headerbtn) {
  top: 10px;
}
</style>