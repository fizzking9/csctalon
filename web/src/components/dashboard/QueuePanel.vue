<template>
  <el-row :gutter="16">
    <!-- 待办 -->
    <el-col :xs="24" :md="8">
      <el-card shadow="hover" class="queue-card">
        <template #header>
          <div class="flex-between">
            <span class="queue-title">
              <el-icon color="#e6a23c"><Bell /></el-icon>
              {{ todoTitle || '今日截止待办' }}
            </span>
            <el-badge :value="(todos || []).length" :max="99" />
          </div>
        </template>
        <div class="queue-list">
          <div v-for="t in todos || []" :key="t.id" class="queue-item" @click="$router.push(`/todos/${t.id}`)">
            <el-tag size="small" :type="priorityTagType(t.priority)" effect="dark" class="mr-8">
              {{ priorityLabel(t.priority) }}
            </el-tag>
            <span class="queue-item-title">{{ t.title }}</span>
            <el-tag size="small" effect="plain" class="queue-status">{{ todoStatusLabel(t.status) }}</el-tag>
          </div>
          <el-empty v-if="!(todos || []).length" :description="todoEmptyDesc || '今日无待办截止'" :image-size="60" />
        </div>
      </el-card>
    </el-col>

    <!-- 专项任务 -->
    <el-col :xs="24" :md="8">
      <el-card shadow="hover" class="queue-card">
        <template #header>
          <div class="flex-between">
            <span class="queue-title">
              <el-icon color="#409eff"><List /></el-icon>
              {{ taskTitle || '进行中专项' }}
            </span>
            <el-badge :value="(tasks || []).length" :max="99" />
          </div>
        </template>
        <div class="queue-list">
          <div v-for="t in tasks || []" :key="t.id" class="queue-item" @click="$router.push(`/tasks/${t.id}`)">
            <el-tag size="small" :type="taskStatusTagType(t.status)" effect="dark" class="mr-8">
              {{ taskStatusLabel(t.status) }}
            </el-tag>
            <span class="queue-item-title">{{ t.title }}</span>
            <span v-if="t.deadline" class="queue-deadline">{{ fmtDeadline(t.deadline) }}</span>
          </div>
          <el-empty v-if="!(tasks || []).length" :description="taskEmptyDesc || '暂无进行中专项'" :image-size="60" />
        </div>
      </el-card>
    </el-col>

    <!-- 异常事件 -->
    <el-col :xs="24" :md="8">
      <el-card shadow="hover" class="queue-card">
        <template #header>
          <div class="flex-between">
            <span class="queue-title">
              <el-icon color="#f56c6c"><Warning /></el-icon>
              {{ incidentTitle || '今日异常事件' }}
            </span>
            <el-badge :value="(incidents || []).length" :max="99" />
          </div>
        </template>
        <div class="queue-list">
          <div v-for="inc in incidents || []" :key="inc.id" class="queue-item" @click="$router.push('/management/incidents')">
            <el-tag size="small" :type="incidentStatusTagType(inc.status)" effect="dark" class="mr-8">
              {{ incidentStatusLabel(inc.status) }}
            </el-tag>
            <span class="queue-item-title">{{ inc.title }}</span>
            <span class="queue-deadline">{{ fmtDeadline(inc.occurred_at, 'time') }}</span>
          </div>
          <el-empty v-if="!(incidents || []).length" :description="incidentEmptyDesc || '今日无异常事件 ✅'" :image-size="60" />
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { Bell, List, Warning } from '@element-plus/icons-vue'
import dayjs from 'dayjs'

defineProps({
  todos: { type: Array, default: () => [] },
  tasks: { type: Array, default: () => [] },
  incidents: { type: Array, default: () => [] },
  todoTitle: { type: String, default: '' },
  taskTitle: { type: String, default: '' },
  incidentTitle: { type: String, default: '' },
  todoEmptyDesc: { type: String, default: '' },
  taskEmptyDesc: { type: String, default: '' },
  incidentEmptyDesc: { type: String, default: '' },
})

function priorityTagType(p) { return { urgent:'danger', high:'warning', medium:'primary', low:'info' }[p] || 'info' }
function priorityLabel(p) { return { urgent:'紧急', high:'高', medium:'中', low:'低' }[p] || '中' }
function todoStatusLabel(s) { return { pending:'待办', processing:'处理中', confirming:'待确认', done:'已完成' }[s] || s }
function taskStatusTagType(s) { return { pending_start:'info', executing:'warning', following:'primary', done:'success', archived:'info' }[s] || 'info' }
function taskStatusLabel(s) { return { pending_start:'待启动', executing:'执行中', following:'跟进中', done:'已完成', archived:'已归档' }[s] || s }
function incidentStatusTagType(s) { return { registered:'danger', processing:'warning', calmed:'primary', reviewed:'success' }[s] || 'info' }
function incidentStatusLabel(s) { return { registered:'登记', processing:'处理中', calmed:'已平息', reviewed:'已复盘' }[s] || s }
function fmtDeadline(d, type='date') {
  if (!d) return ''
  return type === 'time' ? dayjs(d).format('MM-DD HH:mm') : dayjs(d).format('MM-DD')
}
</script>

<style scoped>
.queue-card { height: 100%; }
.queue-title { display:flex; align-items:center; gap:6px; font-size:14px; font-weight:500; color:#303133; }
.queue-list { max-height:320px; overflow-y:auto; }
.queue-item {
  display:flex; align-items:center; gap:8px;
  padding:9px 8px; border-bottom:1px solid #f5f7fa;
  cursor:pointer; transition:background .15s;
}
.queue-item:hover { background:#f5f7fa; }
.queue-item:last-child { border-bottom:none; }
.queue-item-title {
  flex:1; font-size:13px; color:#303133;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.queue-status { margin-left:auto; font-size:11px; }
.queue-deadline { font-size:12px; color:#909399; }
.flex-between { display:flex; justify-content:space-between; align-items:center; }
.mr-8 { margin-right:8px; }
</style>
