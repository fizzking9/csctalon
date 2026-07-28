<template>
  <el-dialog v-model="visible" title="发布为看板" width="480px" :close-on-click-modal="false">
    <el-form :model="form" label-width="80px">
      <el-form-item label="视图">
        <el-tag size="small">{{ view?.view_code || view?.code || '-' }}</el-tag>
        <span style="margin-left: 8px">{{ view?.name }}</span>
      </el-form-item>
      <el-form-item label="看板名称" required>
        <el-input v-model="form.name" placeholder="如:退款趋势看板" />
      </el-form-item>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="分类">
            <el-select v-model="form.category" style="width: 100%">
              <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="可见性">
            <el-radio-group v-model="form.visibility">
              <el-radio-button label="public">全员</el-radio-button>
              <el-radio-button label="manager">管理层</el-radio-button>
              <el-radio-button label="admin">管理员</el-radio-button>
            </el-radio-group>
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="宽度span">
        <el-input-number v-model="form.span" :min="1" :max="24" style="width: 140px" />
        <span class="hint">1-24 栅格,24=整行</span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">发布</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createDashboard } from '@/api/dashboard'

const CATEGORIES = ['退款', '投诉', '接待', '绩效', '专项', '自定义']

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 视图对象:{ id, code/view_code, name, time_range_default }
  view: { type: Object, default: null },
})

const emit = defineEmits(['update:modelValue', 'success'])

const visible = ref(props.modelValue)
const submitting = ref(false)
const form = reactive({ name: '', category: '自定义', visibility: 'manager', span: 24 })

watch(() => props.modelValue, (v) => {
  visible.value = v
  if (v && props.view) {
    // 每次打开重置:名称默认=视图名
    form.name = props.view.name ? `${props.view.name}看板` : ''
    form.category = '自定义'
    form.visibility = 'manager'
    form.span = 24
  }
})
watch(visible, (v) => emit('update:modelValue', v))

async function handleSubmit() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写看板名称')
    return
  }
  if (!props.view?.id) {
    ElMessage.warning('视图信息缺失')
    return
  }
  submitting.value = true
  try {
    const payload = {
      name: form.name,
      description: `由视图「${props.view.name}」发布`,
      category: form.category,
      items: [{
        view_id: props.view.id,
        view_code: props.view.view_code || props.view.code || null,
        title: null,
        span: form.span,
        visible: true,
      }],
      time_range_default: props.view.time_range_default || null,
      visibility: form.visibility,
    }
    const res = await createDashboard(payload)
    ElMessage.success('已发布为看板')
    visible.value = false
    emit('success', res.data)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发布失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.hint {
  font-size: 12px;
  color: #909399;
  margin-left: 8px;
}
</style>
