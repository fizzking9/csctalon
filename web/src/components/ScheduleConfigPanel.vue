<template>
  <div class="schedule-config">
    <!-- 当前节拍概览 -->
    <el-descriptions :column="3" border size="small" class="mb-12">
      <el-descriptions-item label="高频节拍">
        <el-tag size="small" type="primary" effect="plain">
          {{ cfg.start_hour }}:00 ~ {{ cfg.end_hour }}:00
        </el-tag>
        <span class="ml-8">每 <b>{{ cfg.interval_minutes }}</b> 分钟</span>
      </el-descriptions-item>
      <el-descriptions-item label="每日采集次数">
        <b>{{ cfg.slot_count }}</b> 次
      </el-descriptions-item>
      <el-descriptions-item label="首槽 / 末槽">
        <code>{{ cfg.slots?.[0] || '—' }}</code>
        <span class="dim"> ~ </span>
        <code>{{ cfg.slots?.[cfg.slots.length - 1] || '—' }}</code>
      </el-descriptions-item>
    </el-descriptions>

    <el-alert type="info" :closable="false" show-icon class="mb-12">
      <template #title>
        改后<b>自下一个槽位生效</b>，无需重启后端。低频源（每日 1 次）、凌晨补跑、
        首槽全量兜底<b>不受本配置影响</b>。
      </template>
    </el-alert>

    <!-- 编辑区(仅 admin) -->
    <el-form :inline="true" size="small" class="mb-8">
      <el-form-item label="起始">
        <el-input-number
          v-model="form.start_hour" :min="0" :max="23" :disabled="!isAdmin"
          controls-position="right" style="width: 110px"
        />
        <span class="dim">:00</span>
      </el-form-item>
      <el-form-item label="结束">
        <el-input-number
          v-model="form.end_hour" :min="1" :max="24" :disabled="!isAdmin"
          controls-position="right" style="width: 110px"
        />
        <span class="dim">:00（不含）</span>
      </el-form-item>
      <el-form-item label="间隔">
        <el-select v-model="form.interval_minutes" :disabled="!isAdmin" style="width: 150px">
          <el-option :value="15" label="每 15 分钟" />
          <el-option :value="30" label="每 30 分钟" />
          <el-option :value="60" label="每 60 分钟（旧行为）" />
          <el-option :value="120" label="每 120 分钟" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" :disabled="!isAdmin" @click="save">
          保存
        </el-button>
        <el-button :disabled="!isAdmin" @click="resetToDefault">恢复 8~24 每 30 分钟</el-button>
      </el-form-item>
    </el-form>

    <div class="dim">
      <span v-if="!isAdmin" class="warn-text">仅管理员可修改节拍配置（当前为只读查看）。</span>
      <span v-else>
        间隔下限 5 分钟：每轮实测约 4.2 分钟（5 个源 × BMS 65 秒操作间隔），
        低于此值上一轮未结束下一轮已到，必然重叠。
      </span>
      <span v-if="previewCount !== null" class="ml-8">
        当前设置将产生 <b>{{ previewCount }}</b> 次/天。
      </span>
    </div>

    <!-- 槽位预览 -->
    <div v-if="cfg.slots?.length" class="slots-preview mt-8">
      <span class="dim">槽位清单（{{ cfg.slots.length }}）：</span>
      <el-tag
        v-for="s in cfg.slots" :key="s" size="small" effect="plain"
        class="slot-tag"
      >{{ s }}</el-tag>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getScheduleConfig, updateScheduleConfig } from '@/api/fetch'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const cfg = reactive({
  start_hour: 8,
  end_hour: 24,
  interval_minutes: 30,
  slots: [],
  slot_count: 0,
})
const form = reactive({ start_hour: 8, end_hour: 24, interval_minutes: 30 })
const saving = ref(false)

// 本地预演: 与后端 compute_slots 同语义(左闭右开, 不含 24:00)
const previewCount = computed(() => {
  const { start_hour: s, end_hour: e, interval_minutes: i } = form
  if (!i || i <= 0 || e <= s) return null
  let n = 0
  for (let m = s * 60; m < e * 60; m += i) {
    if (Math.floor(m / 60) >= 24) break
    n += 1
  }
  return n
})

async function load() {
  try {
    const res = await getScheduleConfig()
    const d = res.data || {}
    Object.assign(cfg, d)
    form.start_hour = d.start_hour
    form.end_hour = d.end_hour
    form.interval_minutes = d.interval_minutes
  } catch (e) { /* 拦截器已提示 */ }
}

async function save() {
  if (form.end_hour <= form.start_hour) {
    ElMessage.warning('结束时间必须大于起始时间')
    return
  }
  if (form.interval_minutes < 5) {
    ElMessage.warning('间隔下限 5 分钟')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将采集节拍改为 ${form.start_hour}:00 ~ ${form.end_hour}:00 每 ${form.interval_minutes} 分钟` +
      `（约 ${previewCount.value} 次/天），确认？`,
      '确认修改节拍',
      { type: 'warning' },
    )
  } catch {
    return
  }
  saving.value = true
  try {
    const res = await updateScheduleConfig({ ...form })
    Object.assign(cfg, res.data || {})
    ElMessage.success('节拍已更新，自下一个槽位生效')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function resetToDefault() {
  form.start_hour = 8
  form.end_hour = 24
  form.interval_minutes = 30
  await save()
}

onMounted(load)

defineExpose({ load })
</script>

<style scoped>
.schedule-config { width: 100%; }
.mb-8 { margin-bottom: 8px; }
.mb-12 { margin-bottom: 12px; }
.mt-8 { margin-top: 8px; }
.ml-8 { margin-left: 8px; }
.dim { color: var(--el-text-color-secondary); font-size: 12px; }
.warn-text { color: var(--el-color-warning); }
.slots-preview {
  line-height: 2;
  max-height: 140px;
  overflow-y: auto;
}
.slot-tag { margin-right: 4px; margin-bottom: 4px; }
</style>
