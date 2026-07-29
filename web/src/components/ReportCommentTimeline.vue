<template>
  <div class="comment-timeline">
    <div v-if="!comments.length && !loading" class="empty-tip">暂无跟进注释</div>
    <el-timeline v-else>
      <el-timeline-item
        v-for="c in comments"
        :key="c.id"
        :timestamp="formatTime(c.created_at)"
        placement="top"
      >
        <div class="comment-item">
          <div class="comment-author">{{ c.author_name || '匿名' }}</div>
          <div v-if="c.section_key" class="comment-section">
            <el-tag size="small" effect="plain">{{ sectionLabel(c.section_key) }}</el-tag>
          </div>
          <div class="comment-content">{{ c.content }}</div>
          <div v-if="c.output_measure_id" class="comment-measure">
            <el-tag type="success" size="small" @click="$router.push(`/management/measures?id=${c.output_measure_id}`)">
              已派生措施 #{{ c.output_measure_id }}
            </el-tag>
          </div>
          <el-button v-if="canDelete" link type="danger" size="small" class="del-btn" @click="handleDelete(c)">
            删除
          </el-button>
        </div>
      </el-timeline-item>
    </el-timeline>

    <!-- 添加注释 -->
    <div class="add-area">
      <el-input
        v-model="newContent"
        type="textarea"
        :rows="2"
        :placeholder="placeholder"
      />
      <div class="add-actions">
        <el-checkbox v-if="allowMeasure" v-model="deriveMeasure">派生管理措施</el-checkbox>
        <el-button type="primary" size="small" :loading="submitting" :disabled="!newContent.trim()" @click="handleSubmit">
          提交注释
        </el-button>
      </div>
      <!-- 派生措施表单 -->
      <el-card v-if="deriveMeasure" class="measure-form" shadow="never">
        <el-form label-width="90px" size="small">
          <el-form-item label="措施标题" required>
            <el-input v-model="measureForm.title" placeholder="简述要采取的管理措施" />
          </el-form-item>
          <el-form-item label="负责人">
            <el-select v-model="measureForm.owner_id" placeholder="选择负责人" clearable filterable style="width: 100%">
              <el-option v-for="u in userOptions" :key="u.id" :label="u.display_name" :value="u.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="预期目标">
            <el-input v-model="measureForm.expected_goal" placeholder="量化或定性目标" />
          </el-form-item>
          <el-form-item label="验证时间">
            <el-date-picker v-model="measureForm.verify_time" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="验证方式">
            <el-input v-model="measureForm.verify_method" placeholder="看哪个视图/指标/抽查" />
          </el-form-item>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listComments, addComment, deleteComment } from '@/api/comment'
import dayjs from 'dayjs'

const props = defineProps({
  targetType: { type: String, required: true }, // 'weekly' | 'report'
  targetId: { type: [Number, String], required: true },
  sectionKey: { type: String, default: '' }, // 可选: 定位到周报某分项
  allowMeasure: { type: Boolean, default: true }, // 是否允许派生措施
  canDelete: { type: Boolean, default: true },
  userOptions: { type: Array, default: () => [] },
  placeholder: { type: String, default: '添加跟进注释...' },
})

const SECTION_LABELS = {
  measure_followup: '措施跟进',
  reception_trend: '接待人均趋势',
  reception_summary: '分项接待汇总',
  group_reception: '各组接待量',
  refund_recovery: '退款挽回',
  complaint: '投诉专项',
  issues: '问题汇总',
  measure_plan: '措施计划',
  cross_dept: '跨部门协调',
  incidents: '异常事故',
}
const sectionLabel = (k) => SECTION_LABELS[k] || k

const comments = ref([])
const loading = ref(false)
const newContent = ref('')
const submitting = ref(false)
const deriveMeasure = ref(false)
const measureForm = ref({ title: '', owner_id: null, expected_goal: '', verify_time: '', verify_method: '' })

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

async function loadComments() {
  if (!props.targetId) return
  loading.value = true
  try {
    const params = { target_type: props.targetType, target_id: props.targetId }
    if (props.sectionKey) params.section_key = props.sectionKey
    const res = await listComments(params)
    comments.value = res.data || []
  } finally {
    loading.value = false
  }
}

async function handleSubmit() {
  if (!newContent.value.trim()) return
  submitting.value = true
  try {
    const payload = {
      target_type: props.targetType,
      target_id: props.targetId,
      section_key: props.sectionKey || null,
      content: newContent.value.trim(),
    }
    if (deriveMeasure.value) {
      if (!measureForm.value.title.trim()) {
        ElMessage.warning('请填写措施标题')
        submitting.value = false
        return
      }
      payload.create_measure = { ...measureForm.value }
    }
    await addComment(payload)
    ElMessage.success('注释已添加')
    newContent.value = ''
    deriveMeasure.value = false
    measureForm.value = { title: '', owner_id: null, expected_goal: '', verify_time: '', verify_method: '' }
    loadComments()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(c) {
  await ElMessageBox.confirm('确认删除该注释?', '提示', { type: 'warning' })
  await deleteComment(c.id)
  ElMessage.success('已删除')
  loadComments()
}

watch(() => [props.targetType, props.targetId, props.sectionKey], loadComments)
onMounted(loadComments)
</script>

<style scoped>
.comment-timeline {
  margin-top: 8px;
}
.empty-tip {
  color: #909399;
  font-size: 13px;
  padding: 8px 0;
}
.comment-item {
  position: relative;
}
.comment-author {
  font-weight: 600;
  font-size: 13px;
  color: #303133;
}
.comment-section {
  margin: 4px 0;
}
.comment-content {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  white-space: pre-wrap;
  margin: 4px 0;
}
.comment-measure {
  margin-top: 4px;
}
.del-btn {
  position: absolute;
  top: 0;
  right: 0;
}
.add-area {
  margin-top: 16px;
}
.add-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.measure-form {
  margin-top: 12px;
  background: #fafafa;
}
</style>
