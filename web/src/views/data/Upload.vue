<template>
  <el-card>
    <el-steps :active="step" finish-status="success" align-center>
        <el-step title="选择类型与文件" />
        <el-step title="预览与列映射" />
        <el-step title="Diff 复核" />
        <el-step title="确认导入" />
      </el-steps>

      <!-- 步骤1: 选择类型与文件 -->
      <div v-if="step === 0" class="step-content">
        <el-form label-width="120px" style="max-width: 600px; margin: 24px auto">
          <el-form-item label="数据类型" required>
            <el-select v-model="form.data_type" placeholder="请选择数据类型" style="width: 100%">
              <el-option label="退费单管理" value="refund" />
              <el-option label="提现操作记录" value="wallet" />
              <el-option label="服务请求管理" value="ticket" />
              <el-option label="商户号现金流" value="merchant_refund" />
              <el-option label="商户主数据" value="merchant" />
              <el-option label="智能助手处理记录" value="cs_record" />
              <el-option label="外部投诉(315/监督局)" value="external_complaint" />
              <el-option label="卡商群投诉" value="card_complaint" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.data_type" label="Sheet">
            <el-select v-model="form.sheet_name" placeholder="留空自动取第一个" clearable style="width: 100%">
              <el-option v-for="s in sheetNames" :key="s" :label="s" :value="s" />
            </el-select>
            <el-text type="info" size="small">仅 XLSX 需要选择,CSV 忽略</el-text>
          </el-form-item>
          <el-form-item label="选择文件" required>
            <el-upload
              ref="uploadRef"
              :auto-upload="false"
              :limit="1"
              :on-change="handleFileChange"
              :on-exceed="() => $message.warning('只能上传一个文件')"
              accept=".csv,.xlsx,.xls"
              drag
            >
              <el-icon class="el-icon--upload"><upload-filled /></el-icon>
              <div class="el-upload__text">拖拽文件到此处,或<em>点击选择</em></div>
              <template #tip>
                <div class="el-upload__tip">支持 CSV / XLSX 格式,单个文件最大 200MB</div>
              </template>
            </el-upload>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="parsing" :disabled="!form.data_type || !currentFile" @click="handleParse">
              解析文件
            </el-button>
          </el-form-item>
        </el-form>
      </div>

      <!-- 步骤2: 预览与列映射 -->
      <div v-if="step === 1" class="step-content">
        <el-descriptions :column="3" border class="mb-16">
          <el-descriptions-item label="数据类型">{{ typeLabel }}</el-descriptions-item>
          <el-descriptions-item label="文件名">{{ currentFile?.name }}</el-descriptions-item>
          <el-descriptions-item label="总行数">{{ preview.total_rows }}</el-descriptions-item>
          <el-descriptions-item label="编码">{{ preview.detected_encoding || '-' }}</el-descriptions-item>
          <el-descriptions-item label="列数">{{ preview.columns?.length }}</el-descriptions-item>
          <el-descriptions-item label="已映射字段">{{ mappingFieldCount }}</el-descriptions-item>
        </el-descriptions>

        <el-alert v-if="missingFields.length" type="warning" :closable="false" class="mb-16">
          以下字段未映射:{{ missingFields.join('、') }}(可选,但不映射则该字段不导入)
        </el-alert>

        <el-card class="mb-16" header="列名映射(源列名 → 目标字段)">
          <el-table :data="mappingRows" size="small" max-height="400">
            <el-table-column prop="source" label="源列名" width="220" />
            <el-table-column label="目标字段" width="220">
              <template #default="{ row }">
                <el-select v-model="row.target" clearable placeholder="不导入" style="width: 100%">
                  <el-option v-for="opt in targetFieldOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="样例值">
              <template #default="{ row }">
                <el-text type="info" size="small" truncated>{{ getSample(row.source) }}</el-text>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card header="数据预览(前10行)">
          <el-table :data="preview.sample_rows" size="small" max-height="300" border>
            <el-table-column
              v-for="col in preview.columns?.slice(0, 10)"
              :key="col"
              :prop="col"
              :label="col"
              :width="150"
              show-overflow-tooltip
            />
          </el-table>
        </el-card>

        <div class="mt-16 flex gap-8">
          <el-button @click="step = 0">上一步</el-button>
          <el-button type="primary" :loading="diffing" @click="goDiff">下一步:Diff 复核</el-button>
        </div>
      </div>

      <!-- 步骤3: Diff 复核 -->
      <div v-if="step === 2" class="step-content">
        <!-- loading -->
        <div v-if="diffing" class="diff-loading">
          <el-icon class="is-loading" :size="40"><Loading /></el-icon>
          <p>正在计算 diff,请稍候...</p>
          <p class="sub">大文件可能需要 1-2 分钟</p>
        </div>

        <template v-else-if="diffResult">
          <!-- 截断警告 -->
          <el-alert v-if="diffResult.truncated" type="warning" :closable="false" class="mb-16">
            文件超过 {{ diffResult.summary.total.toLocaleString() }} 行已截断,超出部分统一标为 new
          </el-alert>

          <!-- note 提示 -->
          <el-alert v-if="diffResult.note" :type="diffResult.strategy === 'unknown' ? 'info' : 'info'" :closable="false" class="mb-16">
            {{ diffResult.note }}
          </el-alert>

          <!-- 三卡片汇总 -->
          <el-row :gutter="16" class="mb-16">
            <el-col :span="8">
              <el-card shadow="hover" class="summary-card summary-existing">
                <div class="summary-num">{{ diffResult.summary.existing.toLocaleString() }}</div>
                <div class="summary-label">已存在(将覆盖)</div>
              </el-card>
            </el-col>
            <el-col :span="8">
              <el-card shadow="hover" class="summary-card summary-new">
                <div class="summary-num">{{ diffResult.summary.new.toLocaleString() }}</div>
                <div class="summary-label">新增</div>
              </el-card>
            </el-col>
            <el-col :span="8">
              <el-card shadow="hover" class="summary-card summary-abnormal">
                <div class="summary-num">{{ diffResult.summary.abnormal.toLocaleString() }}</div>
                <div class="summary-label">异常(字段差异)</div>
              </el-card>
            </el-col>
          </el-row>

          <!-- abnormal 明细(最高优先级) -->
          <el-card v-if="diffResult.abnormal_rows.length" class="mb-16" header="异常行明细(主键已存在但字段不同)">
            <template #header>
              <div class="flex justify-between items-center">
                <span>异常行明细(主键已存在但字段不同) · 共 {{ diffResult.summary.abnormal.toLocaleString() }} 行,展示前 {{ diffResult.abnormal_rows.length }} 条</span>
              </div>
            </template>
            <el-table :data="diffResult.abnormal_rows" size="small" max-height="400" row-key="row_idx" border>
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="diff-expand">
                    <el-table :data="row.diffs" size="small" border>
                      <el-table-column prop="field" label="字段" width="180" />
                      <el-table-column label="数据库现有值">
                        <template #default="{ row: d }">
                          <span class="diff-old">{{ d.old ?? '(空)' }}</span>
                        </template>
                      </el-table-column>
                      <el-table-column label="文件新值">
                        <template #default="{ row: d }">
                          <span class="diff-new">{{ d.new ?? '(空)' }}</span>
                        </template>
                      </el-table-column>
                    </el-table>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="row_idx" label="行号" width="80" />
              <el-table-column prop="pk" label="主键" width="240" show-overflow-tooltip />
              <el-table-column label="差异字段数" width="100">
                <template #default="{ row }">{{ row.diffs.length }}</template>
              </el-table-column>
              <el-table-column label="差异字段">
                <template #default="{ row }">
                  <el-tag v-for="d in row.diffs.slice(0, 5)" :key="d.field" size="small" type="danger" class="mr-4">{{ d.field }}</el-tag>
                  <el-text v-if="row.diffs.length > 5" type="info" size="small">+{{ row.diffs.length - 5 }}</el-text>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- new 明细 -->
          <el-card v-if="diffResult.new_rows.length" class="mb-16">
            <template #header>
              <span>新增行预览 · 共 {{ diffResult.summary.new.toLocaleString() }} 行,展示前 {{ diffResult.new_rows.length }} 条</span>
            </template>
            <el-table :data="diffResult.new_rows" size="small" max-height="300" border>
              <el-table-column prop="row_idx" label="行号" width="80" />
              <el-table-column prop="pk" label="主键" width="240" show-overflow-tooltip />
              <el-table-column
                v-for="field in newPreviewFields"
                :key="field"
                :label="field"
                :width="150"
                show-overflow-tooltip
              >
                <template #default="{ row }">{{ row.data?.[field] ?? '-' }}</template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- existing 折叠 -->
          <el-collapse v-if="diffResult.existing_sample.length" class="mb-16">
            <el-collapse-item :title="`已存在行(将覆盖) · 共 ${diffResult.summary.existing.toLocaleString()} 行,展示前 ${diffResult.existing_sample.length} 条主键`">
              <el-table :data="diffResult.existing_sample" size="small" border>
                <el-table-column prop="row_idx" label="行号" width="80" />
                <el-table-column prop="pk" label="主键" />
              </el-table>
            </el-collapse-item>
          </el-collapse>

          <div class="mt-16 flex gap-8">
            <el-button @click="step = 1">上一步</el-button>
            <el-button :loading="diffing" @click="runDiff">重新计算</el-button>
            <el-button type="primary" @click="goImport">下一步:确认导入</el-button>
          </div>
        </template>
      </div>

      <!-- 步骤4: 确认导入 -->
      <div v-if="step === 3" class="step-content">
        <el-result v-if="importResult" :icon="importResult.failed_rows > 0 ? 'warning' : 'success'" :title="importTitle">
          <template #sub-title>
            <p>数据类型:{{ typeLabel }} | 文件:{{ importResult.filename }}</p>
          </template>
          <template #extra>
            <el-descriptions :column="2" border style="max-width: 600px; margin: 0 auto">
              <el-descriptions-item label="总行数">{{ importResult.total_rows }}</el-descriptions-item>
              <el-descriptions-item label="新增">{{ importResult.inserted_rows }}</el-descriptions-item>
              <el-descriptions-item label="更新">{{ importResult.updated_rows }}</el-descriptions-item>
              <el-descriptions-item label="失败">{{ importResult.failed_rows }}</el-descriptions-item>
              <el-descriptions-item label="新增关联">{{ importResult.matched_rows }}</el-descriptions-item>
              <el-descriptions-item label="状态">{{ importResult.status }}</el-descriptions-item>
            </el-descriptions>
            <div v-if="importResult.errors?.length" class="mt-16" style="text-align: left; max-width: 600px; margin: 16px auto">
              <el-text type="danger">错误明细(前{{ importResult.errors.length }}条):</el-text>
              <el-table :data="importResult.errors" size="small" max-height="200" class="mt-16">
                <el-table-column prop="row" label="行" width="80" />
                <el-table-column prop="message" label="错误" show-overflow-tooltip />
              </el-table>
            </div>
            <div class="mt-16">
              <el-button type="primary" @click="reset">继续上传</el-button>
              <el-button @click="$router.push('/data/records')">查看记录</el-button>
              <el-button @click="$router.push('/analytics/operation')">查看分析</el-button>
            </div>
          </template>
        </el-result>
        <div v-else class="import-loading">
          <el-icon class="is-loading" :size="40"><Loading /></el-icon>
          <p>正在导入并执行关联匹配,请稍候...</p>
          <p class="sub">大文件可能需要 1-3 分钟,请勿关闭页面</p>
        </div>
      </div>
    </el-card>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled, Loading } from '@element-plus/icons-vue'
import { parseUpload, previewDiff, confirmImport } from '@/api/upload'
import { COLUMN_TEMPLATES } from './templates'

const step = ref(0)
const parsing = ref(false)
const diffing = ref(false)
const importing = ref(false)
const currentFile = ref(null)
const sheetNames = ref([])
const preview = ref({})
const diffResult = ref(null)
const importResult = ref(null)

const form = reactive({ data_type: '', sheet_name: null })

const typeLabel = computed(() => ({ refund: '退费单', wallet: '钱包提现操作', ticket: '服务退款工单', merchant_refund: '商户号现金流', merchant: '商户主数据' }[form.data_type] || ''))
const targetFields = computed(() => Object.keys(COLUMN_TEMPLATES[form.data_type] || {}))
const mappingRows = computed(() => (preview.value.columns || []).map((c) => ({ source: c, target: suggestedMapping()[c] || '' })))
const mappingFieldCount = computed(() => mappingRows.value.filter((r) => r.target).length)
const missingFields = computed(() => targetFields.value.filter((f) => !mappingRows.value.some((r) => r.target === f)))

// 获取字段中文名称
function getFieldLabel(field) {
  const template = COLUMN_TEMPLATES[form.data_type] || {}
  return template[field]?.[0] || field
}

// 获取字段列表(带中文名称)
const targetFieldOptions = computed(() => {
  const template = COLUMN_TEMPLATES[form.data_type] || {}
  return Object.entries(template).map(([field, labels]) => ({
    value: field,
    label: labels[0] || field
  }))
})

// new 行预览要展示的字段(取前 6 个映射字段,避免表格过宽)
const newPreviewFields = computed(() => {
  const mapped = mappingRows.value.filter((r) => r.target).map((r) => r.target)
  return mapped.slice(0, 6)
})

function suggestedMapping() {
  const map = preview.value.suggested_mapping || {}
  const rev = {}
  for (const [target, source] of Object.entries(map)) {
    rev[source] = target
  }
  return rev
}

function getSample(col) {
  const row = preview.value.sample_rows?.[0]
  return row ? String(row[col] ?? '') : ''
}

function handleFileChange(file) {
  currentFile.value = file.raw
  sheetNames.value = []
}

async function handleParse() {
  parsing.value = true
  try {
    const formData = new FormData()
    formData.append('data_type', form.data_type)
    formData.append('file', currentFile.value)
    if (form.sheet_name) formData.append('sheet_name', form.sheet_name)
    const res = await parseUpload(formData)
    preview.value = res.data
    if (res.data.sheet_names) sheetNames.value = res.data.sheet_names
    diffResult.value = null
    step.value = 1
    ElMessage.success(`解析成功,共 ${res.data.total_rows} 行`)
  } catch (e) {
    /* 错误已处理 */
  } finally {
    parsing.value = false
  }
}

function buildMapping() {
  const mapping = {}
  for (const r of mappingRows.value) {
    if (r.target) mapping[r.target] = r.source
  }
  return mapping
}

async function goDiff() {
  step.value = 2
  await runDiff()
}

async function runDiff() {
  diffing.value = true
  try {
    const res = await previewDiff({
      data_type: form.data_type,
      filename: currentFile.value.name,
      stored_path: preview.value.stored_path,
      sheet_name: form.sheet_name,
      encoding: preview.value.detected_encoding,
      column_mapping: buildMapping(),
    })
    diffResult.value = res.data
    const s = res.data.summary
    ElMessage.success(`Diff 完成:新增 ${s.new} / 已存在 ${s.existing} / 异常 ${s.abnormal}`)
  } catch (e) {
    /* 错误已处理 */
  } finally {
    diffing.value = false
  }
}

function goImport() {
  step.value = 3
  doImport()
}

async function doImport() {
  importing.value = true
  try {
    const res = await confirmImport({
      data_type: form.data_type,
      filename: currentFile.value.name,
      stored_path: preview.value.stored_path,
      sheet_name: form.sheet_name,
      encoding: preview.value.detected_encoding,
      column_mapping: buildMapping(),
    })
    importResult.value = res.data
    ElMessage.success('导入完成')
  } catch (e) {
    importResult.value = { failed_rows: 1, errors: [{ row: 0, message: e.message }], status: 'failed', total_rows: 0, inserted_rows: 0, updated_rows: 0, matched_rows: 0, filename: currentFile.value?.name }
  } finally {
    importing.value = false
  }
}

const importTitle = computed(() => {
  if (!importResult.value) return ''
  return importResult.value.failed_rows > 0 ? '导入完成(部分失败)' : '导入成功'
})

function reset() {
  step.value = 0
  currentFile.value = null
  preview.value = {}
  diffResult.value = null
  importResult.value = null
  form.data_type = ''
  form.sheet_name = null
}
</script>

<style scoped>
.step-content {
  min-height: 300px;
  padding: 16px 0;
}
.diff-loading,
.import-loading {
  text-align: center;
  padding: 60px 0;
}
.diff-loading p,
.import-loading p {
  margin-top: 16px;
  color: #606266;
}
.diff-loading .sub,
.import-loading .sub {
  font-size: 12px;
  color: #909399;
}

/* diff 三卡片 */
.summary-card {
  text-align: center;
  padding: 8px 0;
}
.summary-num {
  font-size: 32px;
  font-weight: 700;
  line-height: 1.2;
}
.summary-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}
.summary-existing .summary-num {
  color: #909399;
}
.summary-new .summary-num {
  color: #67c23a;
}
.summary-abnormal .summary-num {
  color: #f56c6c;
}

/* diff 展开区 */
.diff-expand {
  padding: 8px 16px;
  background: #fafafa;
}
.diff-old {
  color: #f56c6c;
  text-decoration: line-through;
}
.diff-new {
  color: #67c23a;
  font-weight: 600;
}
</style>
