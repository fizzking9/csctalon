<template>
  <div class="framework-editor">
    <div v-if="!sections.length && !readonly" class="empty-tip">
      <el-empty description="暂无分项,点击下方按钮添加" :image-size="60" />
    </div>

    <el-card
      v-for="(sec, idx) in sections"
      :key="sec._uid"
      shadow="hover"
      class="section-card"
    >
      <template #header>
        <div class="section-header">
          <span class="section-index">#{{ idx + 1 }}</span>
          <el-tag :type="sectionTypeTag(sec.section_type)" size="small" effect="plain">
            {{ sectionTypeLabel(sec.section_type) }}
          </el-tag>
          <span v-if="sec.owner_id" class="section-owner">
            · 负责人 {{ userLabel(sec.owner_id) }}
          </span>
          <div class="section-actions">
            <el-tooltip content="上移" placement="top">
              <el-button :icon="ArrowUp" text size="small" :disabled="readonly || idx === 0" @click="moveUp(idx)" />
            </el-tooltip>
            <el-tooltip content="下移" placement="top">
              <el-button :icon="ArrowDown" text size="small" :disabled="readonly || idx === sections.length - 1" @click="moveDown(idx)" />
            </el-tooltip>
            <el-tooltip content="删除分项" placement="top">
              <el-button :icon="Delete" text size="small" type="danger" :disabled="readonly" @click="removeSection(idx)" />
            </el-tooltip>
          </div>
        </div>
      </template>

      <el-form label-width="90px" size="default" :disabled="readonly">
        <el-form-item label="分项标题">
          <el-input v-model="sec.title" placeholder="如 退款挽回专项" />
        </el-form-item>
        <el-form-item label="分项类型">
          <el-select v-model="sec.section_type" placeholder="选择类型" style="width: 220px">
            <el-option v-for="opt in sectionTypeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="sec.section_type === 'view'" label="关联视图">
          <el-select
            v-model="sec.view_ids"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择关联的分析视图(可多选)"
            style="width: 100%"
          >
            <el-option v-for="v in viewOptions" :key="v.value" :label="v.label" :value="v.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="负责人">
          <el-select v-model="sec.owner_id" placeholder="选择分项负责人(可空)" clearable filterable style="width: 260px">
            <el-option v-for="u in userOptions" :key="u.value" :label="u.label" :value="u.value" />
          </el-select>
        </el-form-item>
        <!-- 实例模式: 摘要(跟进说明); 模板模式: 摘要占位提示 -->
        <el-form-item v-if="mode === 'instance'" label="跟进摘要">
          <el-input
            v-model="sec.summary"
            type="textarea"
            :rows="2"
            :placeholder="sec.summary_placeholder || '本周该专项的跟进摘要/关键说明'"
          />
        </el-form-item>
        <el-form-item v-else label="摘要占位">
          <el-input
            v-model="sec.summary_placeholder"
            type="textarea"
            :rows="2"
            placeholder="实例化时填入摘要输入框的占位提示"
          />
        </el-form-item>
      </el-form>
    </el-card>

    <div v-if="!readonly" class="add-bar">
      <el-button :icon="Plus" type="primary" plain @click="addSection">添加分项</el-button>
      <span class="add-tip">共 {{ sections.length }} 项</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Plus, ArrowUp, ArrowDown, Delete } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
  // template: 模板编辑(无 summary/data, 有 summary_placeholder); instance: 实例编辑(有 summary/data/related_report_ids)
  mode: { type: String, default: 'instance' },
  viewOptions: { type: Array, default: () => [] }, // [{value, label}]
  userOptions: { type: Array, default: () => [] }, // [{value, label}]
})

const emit = defineEmits(['update:modelValue', 'change'])

const sections = computed({
  get: () => props.modelValue || [],
  set: (val) => emit('update:modelValue', val),
})

const sectionTypeOptions = [
  { value: 'view', label: '视图(数据看板)' },
  { value: 'text', label: '文本(说明记录)' },
  { value: 'measures', label: '措施(跟进/计划)' },
  { value: 'incidents', label: '影响事件' },
]

function sectionTypeLabel(t) {
  return sectionTypeOptions.find((o) => o.value === t)?.label || t || '未定义'
}
function sectionTypeTag(t) {
  return { view: '', text: 'info', measures: 'success', incidents: 'warning' }[t] || 'info'
}
function userLabel(id) {
  return props.userOptions.find((u) => u.value === id)?.label || id
}

let _uidSeq = Date.now()
function _nextUid() {
  _uidSeq += 1
  return `s${_uidSeq}`
}

function _emitChange() {
  // 剥离 _uid 后 emit(后端不需要 _uid)
  const cleaned = sections.value.map(({ _uid, ...rest }) => rest)
  emit('update:modelValue', cleaned)
  emit('change', cleaned)
}

function addSection() {
  const newSec = {
    _uid: _nextUid(),
    key: `section_${Date.now()}`,
    title: '新分项',
    section_type: 'view',
    view_ids: [],
    owner_id: null,
    summary: props.mode === 'instance' ? '' : undefined,
    summary_placeholder: props.mode === 'template' ? '' : undefined,
    data: props.mode === 'instance' ? null : undefined,
    related_report_ids: props.mode === 'instance' ? [] : undefined,
  }
  sections.value = [...sections.value, newSec]
  _emitChange()
}

async function removeSection(idx) {
  try {
    await ElMessageBox.confirm(`确认删除分项[${sections.value[idx].title}]?`, '删除分项', { type: 'warning' })
  } catch {
    return
  }
  const arr = [...sections.value]
  arr.splice(idx, 1)
  sections.value = arr
  _emitChange()
}

function moveUp(idx) {
  if (idx <= 0) return
  const arr = [...sections.value]
  ;[arr[idx - 1], arr[idx]] = [arr[idx], arr[idx - 1]]
  sections.value = arr
  _emitChange()
}

function moveDown(idx) {
  if (idx >= sections.value.length - 1) return
  const arr = [...sections.value]
  ;[arr[idx], arr[idx + 1]] = [arr[idx + 1], arr[idx]]
  sections.value = arr
  _emitChange()
}
</script>

<style scoped>
.framework-editor {
  width: 100%;
}
.section-card {
  margin-bottom: 12px;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-index {
  font-weight: 600;
  color: #909399;
  font-size: 13px;
}
.section-owner {
  color: #909399;
  font-size: 13px;
}
.section-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
}
.add-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
}
.add-tip {
  color: #909399;
  font-size: 13px;
}
.empty-tip {
  padding: 8px 0;
}
</style>
