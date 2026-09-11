<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">投诉处理核查</div>
        <div class="page-sub">投诉核查: 设备SN / 涉及人员 / 接待过程 / 归因 / 核查结论</div>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="10">
        <el-card class="mb-16">
          <template #header><span style="font-weight: 600">新增核查</span></template>
          <el-form :model="form" label-width="90px">
            <el-form-item label="来源">
              <el-select v-model="form.source_type" style="width: 100%">
                <el-option label="内部投诉" value="internal" />
                <el-option label="商户号投诉" value="merchant" />
                <el-option label="外部投诉" value="external" />
              </el-select>
            </el-form-item>
            <el-form-item label="设备SN"><el-input v-model="form.device_sn" placeholder="输入SN查看设备时序" /></el-form-item>
            <el-form-item label="投诉标识"><el-input v-model="form.ref_id" placeholder="工单号/投诉ID" /></el-form-item>
            <el-form-item label="归因">
              <el-select v-model="form.attribution" style="width: 100%">
                <el-option v-for="a in attributions" :key="a" :label="a" :value="a" />
              </el-select>
            </el-form-item>
            <el-form-item label="评判">
              <el-radio-group v-model="form.judgment">
                <el-radio-button value="通过" />
                <el-radio-button value="有问题" />
                <el-radio-button value="需改进" />
              </el-radio-group>
            </el-form-item>
            <el-form-item label="核查结论">
              <el-input v-model="form.conclusion" type="textarea" :rows="4" placeholder="核查结论" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="submitting" @click="handleSave">保存核查</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card>
          <template #header><span style="font-weight: 600">核查记录</span></template>
          <el-table v-loading="loading" :data="audits" border stripe size="small" max-height="360">
            <el-table-column label="来源" width="90">
              <template #default="{ row }">{{ sourceLabel(row.source_type) }}</template>
            </el-table-column>
            <el-table-column prop="device_sn" label="SN" width="130" />
            <el-table-column prop="attribution" label="归因" width="100" />
            <el-table-column prop="conclusion" label="结论" show-overflow-tooltip />
            <el-table-column label="核查时间" width="140">
              <template #default="{ row }">{{ formatTime(row.audited_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="14">
        <el-card>
          <template #header>
            <div class="flex-between">
              <span style="font-weight: 600">设备时序(接待/工单/投诉)</span>
              <el-button v-if="form.device_sn" text type="primary" @click="loadTimeline">查看时序</el-button>
            </div>
          </template>
          <div v-if="!timeline.length" class="timeline-empty">输入SN后点击「查看时序」</div>
          <el-timeline v-else>
            <el-timeline-item
              v-for="(e, i) in timeline"
              :key="i"
              :timestamp="e.time || e.timestamp || ''"
              :type="timelineType(e.kind)"
            >
              <div class="tl-line">
                <el-tag size="small" :type="kindTag(e.kind)">{{ kindLabel(e.kind) }}</el-tag>
                <span class="tl-text">{{ e.summary || e.title || JSON.stringify(e).slice(0, 120) }}</span>
              </div>
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listAudits, createAudit } from '@/api/complaint'
import { getDeviceTimeline } from '@/api/audit'
import dayjs from 'dayjs'

const form = reactive({ source_type: 'internal', device_sn: '', ref_id: '', attribution: '其他', conclusion: '', judgment: '通过' })
const audits = ref([])
const timeline = ref([])
const typeMeta = ref({})
const loading = ref(false)
const submitting = ref(false)
const attributions = ['网络质量', '套餐争议', '退款协商失败', '降成争议', '其他', '未关联SN']
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')
const sourceLabel = (s) => ({ internal: '内部投诉', merchant: '商户号投诉', external: '外部投诉' }[s] || s)
// 设备时序类型: 优先用后端返回的 type_meta(含 设备套餐/手动退款/闪猫退款 等新增类型), 兜底老映射
const kindLabel = (k) => typeMeta.value?.[k]?.label || ({ refund: '退费', ticket: '工单', wallet: '钱包', merchant_refund: '商户退款', cs_record: '接待', external_complaint: '外部投诉', card_complaint: '卡商投诉', device_combo: '设备套餐', manual_refund: '手动退款', shanmao_refund: '闪猫退款' }[k] || k)
const kindTag = (k) => typeMeta.value?.[k]?.color || ({ external_complaint: 'danger', card_complaint: 'warning', ticket: 'primary', cs_record: 'success', device_combo: 'primary', manual_refund: 'warning', shanmao_refund: 'danger' }[k] || 'info')
const timelineType = (k) => kindTag(k) || 'primary'

async function loadAudits() {
  loading.value = true
  try {
    const res = await listAudits({ page: 1, page_size: 50 })
    audits.value = res.data.items || []
  } finally {
    loading.value = false
  }
}

async function loadTimeline() {
  if (!form.device_sn.trim()) {
    ElMessage.warning('请输入SN')
    return
  }
  const res = await getDeviceTimeline(form.device_sn.trim(), { limit: 50 })
  timeline.value = (res.data && res.data.items) || []
  typeMeta.value = (res.data && res.data.type_meta) || {}
}

async function handleSave() {
  if (!form.device_sn.trim() && !form.ref_id.trim()) {
    ElMessage.warning('请输入设备SN或投诉标识')
    return
  }
  submitting.value = true
  try {
    await createAudit({ ...form })
    ElMessage.success('核查已保存')
    form.conclusion = ''
    loadAudits()
  } finally {
    submitting.value = false
  }
}

onMounted(loadAudits)
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.timeline-empty { color: #909399; text-align: center; padding: 40px 0; }
.tl-line { display: flex; align-items: center; gap: 8px; }
.tl-text { color: #606266; font-size: 13px; }
</style>
