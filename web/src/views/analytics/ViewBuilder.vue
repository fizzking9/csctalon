<template>
  <div class="view-builder">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">视图构建器</div>
      <div>
        <el-button :icon="Refresh" @click="loadDataSources">重载字段</el-button>
        <el-button type="primary" :icon="Select" :loading="saving" @click="openSaveDialog">保存视图</el-button>
      </div>
    </div>

    <el-row :gutter="12" class="builder-body">
      <!-- 左栏:数据源 + 字段面板 -->
      <el-col :span="5">
        <el-card shadow="never" class="field-panel">
          <template #header>
            <el-select v-model="currentSourceCode" placeholder="选择数据源" @change="loadFields">
              <el-option v-for="ds in dataSources" :key="ds.code" :label="ds.name_cn" :value="ds.code" />
            </el-select>
          </template>
          <div v-loading="fieldsLoading" class="field-groups">
            <div v-for="group in fieldGroups" :key="group.key" class="field-group">
              <div class="group-title">
                <el-tag :type="roleTagType(group.key)" size="small">{{ group.label }}</el-tag>
                <span class="group-count">{{ group.fields.length }}</span>
              </div>
              <div v-for="f in group.fields" :key="f.field_name" class="field-item" @click="addField(f)">
                <el-icon><Plus /></el-icon>
                <span :title="f.field_name_cn || f.field_name">{{ f.field_name }}</span>
              </div>
            </div>
            <el-empty v-if="!fields.length" description="请选择数据源" :image-size="60" />
          </div>
        </el-card>
      </el-col>

      <!-- 中栏:配置区 -->
      <el-col :span="12">
        <el-card shadow="never" class="config-panel">
          <template #header>
            <div class="flex-between">
              <span>视图配置</span>
              <el-radio-group v-model="viewConfig.chart_type" size="small" @change="triggerPreview">
                <el-radio-button label="line">折线</el-radio-button>
                <el-radio-button label="bar">柱状</el-radio-button>
                <el-radio-button label="pie">饼图</el-radio-button>
                <el-radio-button label="table">表格</el-radio-button>
              </el-radio-group>
            </div>
          </template>

          <!-- Segments 标签页 -->
          <el-tabs v-model="activeSegment" type="card" @tab-change="onSegmentTabChange">
            <el-tab-pane v-for="(seg, idx) in viewConfig.segments" :key="idx" :name="String(idx)">
              <template #label>
                <span>片段 {{ idx + 1 }}{{ seg.label ? ` (${seg.label})` : '' }}</span>
                <el-icon v-if="viewConfig.segments.length > 1" class="tab-close" @click.stop="removeSegment(idx)"><Close /></el-icon>
              </template>

              <div class="segment-config">
                <el-row :gutter="12">
                  <el-col :span="7">
                    <el-form-item label="数据源" label-width="70px">
                      <el-select v-model="seg.source_code" size="small" @change="onSegSourceChange(seg)">
                        <el-option v-for="ds in dataSources" :key="ds.code" :label="ds.name_cn" :value="ds.code" />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :span="5">
                    <el-form-item label="标签" label-width="50px">
                      <el-input v-model="seg.label" size="small" placeholder="片段标签" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="5">
                    <el-form-item label="堆积组" label-width="60px">
                      <el-input v-model="seg.stack_group" size="small" placeholder="留空=独立" @input="triggerPreview" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="7">
                    <el-form-item label="时间粒度" label-width="70px">
                      <el-select v-model="seg.time_grain" size="small" placeholder="无时间维度" @change="triggerPreview">
                        <el-option label="按日" value="day" />
                        <el-option label="按周" value="week" />
                        <el-option label="按月" value="month" />
                        <el-option label="按季" value="quarter" />
                        <el-option label="按年" value="year" />
                        <el-option label="不聚合" value="none" />
                      </el-select>
                    </el-form-item>
                  </el-col>
                </el-row>

                <!-- 维度区 -->
                <div class="drop-zone">
                  <div class="zone-title">
                    <el-tag type="primary" size="small">维度 (GROUP BY)</el-tag>
                    <span class="zone-hint">从左栏点击添加</span>
                  </div>
                  <div class="zone-items">
                    <el-tag v-for="(d, di) in seg.dimensions" :key="di" closable size="small" @close="seg.dimensions.splice(di, 1); triggerPreview()">
                      {{ d.alias || d.field }}<span v-if="d.role === 'time'" class="dim-time">[时间]</span>
                    </el-tag>
                    <span v-if="!seg.dimensions.length" class="empty-hint">点击左栏字段添加维度</span>
                  </div>
                </div>

                <!-- 度量区 -->
                <div class="drop-zone">
                  <div class="zone-title">
                    <el-tag type="warning" size="small">度量 (聚合)</el-tag>
                    <span class="zone-hint">金额/数值字段</span>
                  </div>
                  <div class="zone-items">
                    <div v-for="(m, mi) in seg.measures" :key="mi" class="measure-item">
                      <el-tag size="small">{{ m.alias || m.field }}</el-tag>
                      <el-select v-model="m.agg" size="small" style="width: 90px" @change="triggerPreview">
                        <el-option label="求和" value="sum" />
                        <el-option label="计数" value="count" />
                        <el-option label="平均" value="avg" />
                        <el-option label="去重计数" value="count_distinct" />
                      </el-select>
                      <el-input v-model="m.alias" size="small" style="width: 100px" placeholder="别名" @input="triggerPreview" />
                      <el-icon class="remove-icon" @click="seg.measures.splice(mi, 1); triggerPreview()"><Close /></el-icon>
                    </div>
                    <span v-if="!seg.measures.length" class="empty-hint">点击左栏字段添加度量</span>
                  </div>
                </div>

                <!-- 筛选区 -->
                <div class="drop-zone">
                  <div class="zone-title">
                    <el-tag size="small">筛选条件</el-tag>
                    <el-button text type="primary" size="small" :icon="Plus" @click="addFilter(seg)">添加</el-button>
                  </div>
                  <div class="zone-items vertical">
                    <div v-for="(f, fi) in seg.filters" :key="fi" class="filter-item">
                      <el-input v-model="f.field" size="small" placeholder="字段名" style="width: 140px" @input="triggerPreview" />
                      <el-select v-model="f.op" size="small" style="width: 120px" @change="triggerPreview">
                        <el-option label="等于" value="eq" />
                        <el-option label="不等于" value="ne" />
                        <el-option label="属于" value="in" />
                        <el-option label="不属于" value="notin" />
                        <el-option label="包含" value="contains" />
                        <el-option label="不包含" value="not_contains" />
                        <el-option label="大于" value="gt" />
                        <el-option label="小于" value="lt" />
                        <el-option label="大于等于" value="gte" />
                        <el-option label="小于等于" value="lte" />
                        <el-option label="为空" value="is_null" />
                        <el-option label="非空" value="is_not_null" />
                      </el-select>
                      <el-input
                        v-if="!['is_null', 'is_not_null'].includes(f.op)"
                        v-model="f.valueModel"
                        size="small"
                        placeholder="值(in/notin 用逗号分隔)"
                        style="flex: 1"
                        @input="onFilterValueInput(f)"
                      />
                      <el-icon class="remove-icon" @click="seg.filters.splice(fi, 1); triggerPreview()"><Close /></el-icon>
                    </div>
                  </div>
                </div>

                <!-- 分类规则 -->
                <div class="drop-zone">
                  <div class="zone-title">
                    <el-tag type="success" size="small">分类规则(可选)</el-tag>
                    <span class="zone-hint">把维度值动态归类为业务标签</span>
                  </div>
                  <div class="zone-items">
                    <el-input v-model="seg.classification_target" size="small" placeholder="目标字段(如 operator)" style="width: 200px" @input="triggerPreview" />
                    <el-select v-model="classificationRuleIdsMultiple" multiple size="small" placeholder="选择规则" style="flex: 1" @change="triggerPreview">
                      <el-option v-for="r in classificationRules" :key="r.id" :label="r.name" :value="r.id" />
                    </el-select>
                  </div>
                </div>
              </div>
            </el-tab-pane>

            <el-tab-pane name="add">
              <template #label>
                <el-button text type="primary" size="small" :icon="Plus" @click="addSegment">添加片段</el-button>
              </template>
              <el-empty description="点击「添加片段」创建新的数据源片段(支持分表聚合后相加)" />
            </el-tab-pane>
          </el-tabs>

          <!-- 比率度量(基于已聚合度量算比率,如挽回率=已接受/总数) -->
          <div class="drop-zone ratio-zone">
            <div class="zone-title">
              <el-tag type="info" size="small">比率度量 (可选)</el-tag>
              <span class="zone-hint">分子÷分母,合并后计算;分母为0返回空</span>
              <el-button text type="primary" size="small" :icon="Plus" @click="addRatio">添加</el-button>
            </div>
            <div class="zone-items vertical">
              <div v-for="(r, ri) in viewConfig.ratios" :key="ri" class="ratio-item">
                <el-input v-model="r.alias" size="small" placeholder="别名(如 挽回率%)" style="width: 130px" @input="triggerPreview" />
                <el-select v-model="r.numerator" size="small" placeholder="分子" style="width: 130px" @change="triggerPreview">
                  <el-option v-for="a in allMeasureAliases" :key="a" :label="a" :value="a" />
                </el-select>
                <span class="ratio-div">÷</span>
                <el-select v-model="r.denominator" size="small" placeholder="分母" style="width: 130px" @change="triggerPreview">
                  <el-option v-for="a in allMeasureAliases" :key="a" :label="a" :value="a" />
                </el-select>
                <el-checkbox v-model="r.as_percent" @change="triggerPreview">百分比</el-checkbox>
                <el-icon class="remove-icon" @click="viewConfig.ratios.splice(ri, 1); triggerPreview()"><Close /></el-icon>
              </div>
              <span v-if="!viewConfig.ratios.length" class="empty-hint">无需比率度量(如金额、笔数直接展示即可)</span>
            </div>
          </div>

          <!-- 累计差额(池子水位,基于已聚合度量算运行总和) -->
          <div class="drop-zone ratio-zone">
            <div class="zone-title">
              <el-tag type="info" size="small">累计差额 (可选)</el-tag>
              <span class="zone-hint">流入-流出按时间累计,如池子水位;窗口内累计</span>
              <el-button text type="primary" size="small" :icon="Plus" @click="addCumulative">添加</el-button>
            </div>
            <div class="zone-items vertical">
              <div v-for="(c, ci) in viewConfig.cumulative" :key="ci" class="ratio-item">
                <el-input v-model="c.alias" size="small" placeholder="别名(如 净水位)" style="width: 130px" @input="triggerPreview" />
                <el-select v-model="c.inflow" size="small" placeholder="流入" style="width: 130px" @change="triggerPreview">
                  <el-option v-for="a in allMeasureAliases" :key="a" :label="a" :value="a" />
                </el-select>
                <span class="ratio-div">-</span>
                <el-select v-model="c.outflow" size="small" placeholder="流出(可选)" clearable style="width: 130px" @change="triggerPreview">
                  <el-option v-for="a in allMeasureAliases" :key="a" :label="a" :value="a" />
                </el-select>
                <el-icon class="remove-icon" @click="viewConfig.cumulative.splice(ci, 1); triggerPreview()"><Close /></el-icon>
              </div>
              <span v-if="!viewConfig.cumulative.length" class="empty-hint">无需累计差额(如纯趋势/占比分析)</span>
            </div>
          </div>

          <!-- 时间范围 -->
          <div class="time-range-bar">
            <span class="label">预览时间范围:</span>
            <el-radio-group v-model="previewDays" size="small" @change="triggerPreview">
              <el-radio-button :label="7">近7天</el-radio-button>
              <el-radio-button :label="30">近30天</el-radio-button>
              <el-radio-button :label="90">近90天</el-radio-button>
              <el-radio-button :label="0">全部</el-radio-button>
            </el-radio-group>
            <el-button type="primary" size="small" :loading="previewing" @click="triggerPreview">刷新预览</el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 右栏:实时预览 -->
      <el-col :span="7">
        <el-card shadow="never" class="preview-panel">
          <template #header>
            <div class="flex-between">
              <span>实时预览</span>
              <el-tag v-if="previewResult" size="small" type="info">{{ previewResult.chart_type }}</el-tag>
            </div>
          </template>
          <div v-loading="previewing" class="preview-body">
            <template v-if="previewResult">
              <BaseChart v-if="previewResult.chart_type !== 'table'" :option="chartOption" height="300px" />
              <el-table v-else :data="previewResult.data.rows" border stripe size="small" max-height="400">
                <el-table-column v-for="col in previewResult.data.columns" :key="col" :prop="col" :label="col" show-overflow-tooltip>
                  <template #default="{ row }">{{ fmtVal(row[col], col) }}</template>
                </el-table-column>
              </el-table>
            </template>
            <el-empty v-else description="配置维度和度量后自动预览" :image-size="80" />
            <div v-if="previewError" class="preview-error">{{ previewError }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 保存对话框 -->
    <el-dialog v-model="saveDialogVisible" title="保存视图" width="480px">
      <el-form :model="saveForm" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="saveForm.name" placeholder="如:退款趋势分析" />
        </el-form-item>
        <el-form-item label="代码">
          <el-input v-model="saveForm.code" placeholder="可选,预设视图用(如 my_view_1)" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="saveForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="saveDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Close, Select, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import BaseChart from '@/components/BaseChart.vue'
import { listDataSources, listDataSourceFields } from '@/api/data-source'
import { executeAnalysisView, createAnalysisView, listClassificationRules } from '@/api/analysis-view'
import dayjs from 'dayjs'

const router = useRouter()

const dataSources = ref([])
const fields = ref([])
const fieldsLoading = ref(false)
const currentSourceCode = ref('')
const classificationRules = ref([])
const classificationRuleIdsMultiple = ref([])

// 视图配置
const viewConfig = reactive({
  name: '',
  chart_type: 'line',
  merge_strategy: 'add',
  segments: [createEmptySegment()],
  ratios: [],
  cumulative: [],
})

const activeSegment = ref('0')
const previewDays = ref(30)
const previewing = ref(false)
const previewResult = ref(null)
const previewError = ref('')
const saving = ref(false)
const saveDialogVisible = ref(false)
const saveForm = reactive({ name: '', code: '', description: '' })

// 字段分组(按 semantic_role)
const roleLabels = {
  dimension: '维度',
  measure: '度量',
  amount: '金额',
  time: '时间',
  person: '人员',
  sn: 'SN',
  category: '类别',
}
const roleTagType = (r) => ({
  amount: 'danger', measure: 'warning', time: 'primary',
  person: 'success', sn: 'info', category: '', dimension: 'info',
}[r] || '')

const fieldGroups = computed(() => {
  const groups = {}
  for (const f of fields.value) {
    const role = f.semantic_role || 'dimension'
    if (!groups[role]) groups[role] = { key: role, label: roleLabels[role] || role, fields: [] }
    groups[role].fields.push(f)
  }
  // 排序:time, category, person, dimension, amount, measure, sn
  const order = ['time', 'category', 'person', 'dimension', 'amount', 'measure', 'sn']
  return order.map((k) => groups[k]).filter(Boolean)
})

// 收集所有 segment 的度量 alias(供比率度量的分子/分母下拉选择)
const allMeasureAliases = computed(() => {
  const aliases = []
  for (const seg of viewConfig.segments) {
    for (const m of seg.measures) {
      const a = m.alias || (m.field !== '*' ? m.field : 'count')
      if (!aliases.includes(a)) aliases.push(a)
    }
  }
  return aliases
})

// 百分比比率别名集合(用于表格列格式化:追加 % 后缀)
const percentAliases = computed(() => new Set(viewConfig.ratios.filter((r) => r.as_percent && r.alias).map((r) => r.alias)))

function addRatio() {
  viewConfig.ratios.push({ alias: '', numerator: '', denominator: '', as_percent: true })
}

function addCumulative() {
  viewConfig.cumulative.push({ alias: '', inflow: '', outflow: null })
}

function createEmptySegment() {
  return {
    source_code: '',
    label: '',
    stack_group: null,
    dimensions: [],
    measures: [],
    filters: [],
    classification_target: null,
    time_grain: 'day',
    limit: null,
  }
}

function addSegment() {
  const seg = createEmptySegment()
  if (currentSourceCode.value) seg.source_code = currentSourceCode.value
  viewConfig.segments.push(seg)
  activeSegment.value = String(viewConfig.segments.length - 1)
}

function removeSegment(idx) {
  viewConfig.segments.splice(idx, 1)
  activeSegment.value = '0'
  triggerPreview()
}

function onSegmentTabChange(name) {
  if (name !== 'add') activeSegment.value = name
}

async function loadDataSources() {
  const res = await listDataSources()
  dataSources.value = res.data
  if (dataSources.value.length && !currentSourceCode.value) {
    currentSourceCode.value = dataSources.value[0].code
    await loadFields(currentSourceCode.value)
    // 默认给第一个 segment 设置数据源
    if (viewConfig.segments[0] && !viewConfig.segments[0].source_code) {
      viewConfig.segments[0].source_code = currentSourceCode.value
    }
  }
}

async function loadFields(code) {
  if (!code) return
  fieldsLoading.value = true
  try {
    const res = await listDataSourceFields(code)
    fields.value = res.data
  } finally {
    fieldsLoading.value = false
  }
}

function onSegSourceChange(seg) {
  // 切换数据源时清空维度度量(字段可能不存在)
  seg.dimensions = []
  seg.measures = []
  seg.filters = []
  seg.classification_target = null
  // 如果切换的是当前左栏数据源,同步加载
  if (seg.source_code !== currentSourceCode.value) {
    currentSourceCode.value = seg.source_code
    loadFields(seg.source_code)
  }
  triggerPreview()
}

// 从左栏添加字段到当前 segment
function addField(field) {
  const idx = Number(activeSegment.value)
  if (Number.isNaN(idx) || !viewConfig.segments[idx]) {
    ElMessage.warning('请先选择或添加一个片段')
    return
  }
  const seg = viewConfig.segments[idx]
  const role = field.semantic_role || 'dimension'

  if (role === 'amount' || role === 'measure') {
    // 添加为度量
    if (seg.measures.some((m) => m.field === field.field_name)) {
      ElMessage.warning('该字段已在度量中')
      return
    }
    seg.measures.push({
      field: field.field_name,
      agg: role === 'amount' ? 'sum' : 'count',
      alias: field.field_name_cn || field.field_name,
    })
  } else {
    // 添加为维度
    if (seg.dimensions.some((d) => d.field === field.field_name)) {
      ElMessage.warning('该字段已在维度中')
      return
    }
    seg.dimensions.push({
      field: field.field_name,
      alias: field.field_name_cn || field.field_name,
      role: role,
      time_grain: role === 'time' ? (seg.time_grain || 'day') : undefined,
    })
  }
  triggerPreview()
}

function addFilter(seg) {
  seg.filters.push({ field: '', op: 'eq', value: null, valueModel: '' })
}

function onFilterValueInput(f) {
  // in/notin 转数组
  if (['in', 'notin'].includes(f.op)) {
    f.value = f.valueModel.split(',').map((s) => s.trim()).filter(Boolean)
  } else if (['gt', 'lt', 'gte', 'lte'].includes(f.op)) {
    f.value = f.valueModel === '' ? null : Number(f.valueModel)
  } else {
    f.value = f.valueModel
  }
  triggerPreview()
}

// 预览(防抖)
let previewTimer = null
function triggerPreview() {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(doPreview, 400)
}

async function doPreview() {
  const seg = viewConfig.segments[0]
  if (!seg || !seg.source_code) return
  if (!seg.dimensions.length || !seg.measures.length) {
    previewResult.value = null
    return
  }
  previewing.value = true
  previewError.value = ''
  try {
    // 构造 inline config
    const inlineConfig = {
      chart_type: viewConfig.chart_type,
      segments: viewConfig.segments.map((s) => ({
        source_code: s.source_code,
        label: s.label,
        dimensions: s.dimensions.map((d) => ({
          field: d.field,
          alias: d.alias,
          role: d.role,
          time_grain: d.role === 'time' ? (s.time_grain || 'day') : undefined,
        })),
        measures: s.measures.map((m) => ({ field: m.field, agg: m.agg, alias: m.alias })),
        filters: s.filters
          .filter((f) => f.field)
          .map((f) => ({ field: f.field, op: f.op, value: f.value })),
        classification_target: s.classification_target || null,
        stack_group: s.stack_group || null,
        limit: s.limit,
      })),
      merge_strategy: viewConfig.merge_strategy,
      classification_rule_ids: classificationRuleIdsMultiple.value,
      ratios: viewConfig.ratios.filter((r) => r.alias && r.numerator && r.denominator),
      cumulative: viewConfig.cumulative.filter((c) => c.alias && c.inflow),
    }
    const params = {}
    if (previewDays.value > 0) {
      const end = dayjs()
      const start = end.subtract(previewDays.value, 'day')
      params.start = start.format('YYYY-MM-DD')
      params.end = end.format('YYYY-MM-DD')
    }
    const res = await executeAnalysisView({ inline_config: inlineConfig, params })
    previewResult.value = res.data
  } catch (e) {
    previewError.value = e.response?.data?.detail || e.message || '预览失败'
    previewResult.value = null
  } finally {
    previewing.value = false
  }
}

// 图表配置(根据 previewResult 动态生成)
const chartOption = computed(() => {
  if (!previewResult.value) return {}
  const r = previewResult.value
  const data = r.data
  if (r.chart_type === 'pie') {
    return {
      tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
      legend: { type: 'scroll', orient: 'vertical', right: 10, top: 'middle' },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        data: data.items?.map((i) => ({ name: i.name, value: Number(i.value) })) || [],
      }],
    }
  }
  // line / bar
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const lines = [params[0]?.axisValue || '']
        params.forEach((p) => {
          lines.push(`${p.marker} ${p.seriesName}: ${Number(p.value).toFixed(2)}`)
        })
        return lines.join('<br/>')
      },
    },
    legend: { data: data.series?.map((s) => s.name) || [] },
    grid: { left: 50, right: 30, bottom: 30, top: 40 },
    xAxis: { type: 'category', data: data.x_axis || [] },
    yAxis: { type: 'value' },
    series: (data.series || []).map((s) => ({
      name: s.name,
      type: r.chart_type,
      smooth: r.chart_type === 'line',
      data: s.data,
      ...(s.stack ? { stack: s.stack } : {}),
    })),
  }
})

function fmtVal(v, col) {
  if (v == null) return '-'
  if (typeof v === 'number') {
    // 百分比比率列:追加 % 后缀
    if (col && percentAliases.value.has(col)) return `${v.toFixed(2)}%`
    return v.toFixed(2)
  }
  return v
}

function openSaveDialog() {
  const seg = viewConfig.segments[0]
  if (!seg || !seg.source_code || !seg.dimensions.length || !seg.measures.length) {
    ElMessage.warning('请先配置至少一个维度和一个度量')
    return
  }
  saveForm.name = ''
  saveForm.code = ''
  saveForm.description = ''
  saveDialogVisible.value = true
}

async function handleSave() {
  if (!saveForm.name.trim()) {
    ElMessage.warning('请填写视图名称')
    return
  }
  saving.value = true
  try {
    await createAnalysisView({
      code: saveForm.code || null,
      name: saveForm.name,
      description: saveForm.description,
      chart_type: viewConfig.chart_type,
      segments: viewConfig.segments.map((s) => ({
        source_code: s.source_code,
        label: s.label,
        dimensions: s.dimensions.map((d) => ({
          field: d.field, alias: d.alias, role: d.role,
          time_grain: d.role === 'time' ? (s.time_grain || 'day') : undefined,
        })),
        measures: s.measures.map((m) => ({ field: m.field, agg: m.agg, alias: m.alias })),
        filters: s.filters.filter((f) => f.field).map((f) => ({ field: f.field, op: f.op, value: f.value })),
        classification_target: s.classification_target || null,
        stack_group: s.stack_group || null,
        limit: s.limit,
      })),
      merge_strategy: viewConfig.merge_strategy,
      classification_rule_ids: classificationRuleIdsMultiple.value,
      time_range_default: previewDays.value > 0 ? { days: previewDays.value } : null,
      ratios: viewConfig.ratios.filter((r) => r.alias && r.numerator && r.denominator),
      cumulative: viewConfig.cumulative.filter((c) => c.alias && c.inflow),
    })
    ElMessage.success('视图已保存')
    saveDialogVisible.value = false
    router.push('/analytics/views')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function loadClassificationRules() {
  try {
    const res = await listClassificationRules({ page: 1, page_size: 100 })
    classificationRules.value = res.data.items
  } catch (e) {
    // 静默失败
  }
}

onMounted(() => {
  loadDataSources()
  loadClassificationRules()
})
</script>

<style scoped>
.view-builder {
  padding: 0;
}
.builder-body {
  min-height: calc(100vh - 140px);
}
.field-panel,
.config-panel,
.preview-panel {
  height: calc(100vh - 140px);
  overflow-y: auto;
}
.field-groups {
  max-height: calc(100vh - 240px);
  overflow-y: auto;
}
.field-group {
  margin-bottom: 12px;
}
.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
}
.group-count {
  color: #909399;
  font-size: 11px;
}
.field-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  margin: 2px 0;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.2s;
}
.field-item:hover {
  background: #ecf5ff;
  color: #409eff;
}
.field-item span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.segment-config {
  padding: 8px 4px;
}
.drop-zone {
  border: 1px dashed #dcdfe6;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 12px;
  background: #fafafa;
}
.zone-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.zone-hint {
  font-size: 11px;
  color: #c0c4cc;
}
.zone-items {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  min-height: 28px;
}
.zone-items.vertical {
  flex-direction: column;
  align-items: stretch;
}
.empty-hint {
  color: #c0c4cc;
  font-size: 12px;
  padding: 4px;
}
.dim-time {
  color: #409eff;
  font-size: 11px;
  margin-left: 2px;
}
.measure-item,
.filter-item,
.ratio-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
}
.ratio-div {
  color: #909399;
  font-size: 14px;
  font-weight: 600;
}
.ratio-zone {
  margin-top: 8px;
}
.remove-icon {
  cursor: pointer;
  color: #f56c6c;
}
.tab-close {
  margin-left: 4px;
  cursor: pointer;
  color: #f56c6c;
}
.time-range-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-top: 1px solid #ebeef5;
  margin-top: 8px;
}
.time-range-bar .label {
  font-size: 13px;
  color: #606266;
}
.preview-body {
  min-height: 300px;
}
.preview-error {
  color: #f56c6c;
  font-size: 12px;
  margin-top: 8px;
  padding: 8px;
  background: #fef0f0;
  border-radius: 4px;
}
</style>
