<template>
  <div class="page-container registry-center">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">登记中心</div>
        <div class="page-sub">异常事件 · 管理措施 · 管理制度(SOP/规范/话术) · 部门间信息同步,统一入口录入 / 查看 / 调整 / 审核审计,所有变更操作自动落操作日志</div>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建登记</el-button>
    </div>

    <!-- 顶部筛选(共用,按 Tab 类型切换字段) -->
    <el-card class="mb-16" shadow="never">
      <div class="flex gap-8" style="flex-wrap: wrap">
        <!-- 状态 -->
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 140px" @change="resetAndLoad">
          <el-option v-for="(v, k) in statusOptions" :key="k" :label="v" :value="k" />
        </el-select>
        <!-- 分类(制度/部门同步/措施) -->
        <el-select v-if="['policy', 'deptSync', 'measure'].includes(activeTab)" v-model="filter.category" placeholder="分类" clearable style="width: 160px" @change="resetAndLoad">
          <el-option v-for="(v, k) in categoryOptions" :key="k" :label="v" :value="k" />
        </el-select>
        <!-- 优先级(部门同步) -->
        <el-select v-if="activeTab === 'deptSync'" v-model="filter.priority" placeholder="优先级" clearable style="width: 120px" @change="resetAndLoad">
          <el-option v-for="(v, k) in priorityOptions" :key="k" :label="v" :value="k" />
        </el-select>
        <!-- 措施来源 -->
        <el-select v-if="activeTab === 'measure'" v-model="filter.source" placeholder="来源" clearable style="width: 140px" @change="resetAndLoad">
          <el-option label="审计输出" value="audit" />
          <el-option label="周报输出" value="weekly" />
          <el-option label="异常事件转化" value="incident" />
          <el-option label="手工新建" value="manual" />
        </el-select>
        <!-- 关键词 -->
        <el-input v-if="activeTab === 'policy' || activeTab === 'deptSync'" v-model="filter.keyword" placeholder="标题/内容关键词" clearable style="width: 220px" @clear="resetAndLoad" @keyup.enter="resetAndLoad" />
        <!-- 时间范围 -->
        <el-date-picker
          v-model="filter.range"
          type="daterange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          value-format="YYYY-MM-DD"
          style="width: 260px"
          @change="resetAndLoad"
        />
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </el-card>

    <!-- 四 Tab:Incidents/Measures/Policies/DeptInfoSyncs -->
    <el-card shadow="never">
      <el-tabs v-model="activeTab" type="border-card">
        <el-tab-pane label="异常事件" name="incident">
          <div class="todo-hint">
            <el-alert type="info" :closable="false" show-icon>
              <template #title>TODO 占位:复杂的行级动作(转预期/复盘批注)保留在 <code>management/Incidents.vue</code> 独立页,此处仅作「快速录入 / 简易列表预览 + 跳转」。</template>
            </el-alert>
          </div>
          <IncidentSimpleTable :filter="filter" @view="goIncidentDetail" />
        </el-tab-pane>

        <el-tab-pane label="管理措施" name="measure">
          <div class="todo-hint">
            <el-alert type="info" :closable="false" show-icon>
              <template #title>TODO 占位:到期回看、状态流转、关联报告等细节保留在 <code>management/Measures.vue</code>。此处先提供预览+新增。</template>
            </el-alert>
          </div>
          <CommonListTable
            :columns="measureColumns"
            :row-key="'id'"
            :api="listMeasures"
            :map-params="mapMeasureParams"
            :map-row="mapMeasureRow"
            :filter="filter"
            @view="(row) => $router.push(`/management/measures`)"
          />
        </el-tab-pane>

        <el-tab-pane label="管理制度" name="policy">
          <CommonListTable
            :columns="policyColumns"
            :row-key="'id'"
            :api="listPolicies"
            :map-params="mapPolicyParams"
            :map-row="mapPolicyRow"
            :filter="filter"
            @view="openPolicyDetail"
          />
        </el-tab-pane>

        <el-tab-pane label="部门同步" name="deptSync">
          <CommonListTable
            :columns="deptSyncColumns"
            :row-key="'id'"
            :api="listDeptInfoSyncs"
            :map-params="mapDeptSyncParams"
            :map-row="mapDeptSyncRow"
            :filter="filter"
            @view="openDeptSyncDetail"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 新增 / 编辑抽屉(按 Tab 类型切换表单) -->
    <el-drawer v-model="formVisible" :title="formTitle" size="560px" destroy-on-close>
      <!-- TODO 占位:四类表单字段尚未定制,先给一个通用 schema 草稿。
           后续细化时,可抽出 4 个独立表单组件并在此处按 activeTab 切换渲染。 -->
      <el-alert type="warning" :closable="false" show-icon class="mb-16">
        <template #title>表单占位:字段已预留(title/category/content/remark 等通用字段已能保存),细节字段待下一版本按真实录入样式细化。</template>
      </el-alert>
      <el-form :model="formData" label-width="96px" label-position="right">
        <el-form-item label="标题" required>
          <el-input v-model="formData.title" placeholder="必填,简明扼要" />
        </el-form-item>
        <el-form-item v-if="['policy', 'deptSync', 'measure'].includes(activeTab)" label="分类">
          <el-select v-model="formData.category" style="width: 100%">
            <el-option v-for="(v, k) in categoryOptions" :key="k" :label="v" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="activeTab === 'deptSync'" label="优先级">
          <el-radio-group v-model="formData.priority">
            <el-radio v-for="(v, k) in priorityOptions" :key="k" :value="k">{{ v }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="activeTab === 'deptSync'" label="发起部门">
          <el-input v-model="formData.from_department" />
        </el-form-item>
        <el-form-item v-if="activeTab === 'deptSync'" label="接收部门">
          <el-input v-model="formData.to_departments" placeholder="多部门可用逗号分隔" />
        </el-form-item>
        <el-form-item label="内容" required>
          <el-input v-model="formData.content" type="textarea" :rows="6" placeholder="描述、明细、需要配合的动作等" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="formData.remark" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <div style="text-align: right">
          <el-button @click="formVisible = false">取消</el-button>
          <el-button type="primary" :loading="formSubmitting" @click="submitForm">保存</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { createIncident } from '@/api/incident'
import { createMeasure, listMeasures } from '@/api/measure'
import { createPolicy, listPolicies } from '@/api/policy'
import { createDeptInfoSync, listDeptInfoSyncs } from '@/api/deptInfoSync'
import CommonListTable from './RegistryCenter/_CommonListTable.vue'
import IncidentSimpleTable from './RegistryCenter/_IncidentSimpleTable.vue'

const router = useRouter()

const activeTab = ref('incident')
const filter = reactive({
  page: 1, page_size: 20,
  status: '', category: '', priority: '', source: '', keyword: '',
  range: [],
})

const formVisible = ref(false)
const formSubmitting = ref(false)
const formData = reactive({})

const formTitle = computed(() => {
  const t = { incident: '登记异常事件', measure: '新建管理措施', policy: '新建管理制度', deptSync: '登记部门同步' }
  return t[activeTab.value] || '新建登记'
})

// ---------- 下拉选项 ----------
const statusMapAll = {
  incident: { registered: '登记', processing: '处理中', calmed: '已平息', reviewed: '已复盘' },
  measure:  { draft: '草稿', executing: '执行中', pending_review: '待回看', effective: '已生效', ineffective: '未生效', archived: '已归档' },
  policy:   { draft: '草稿', pending_approval: '待审批', published: '已发布', archived: '已归档' },
  deptSync: { draft: '草稿', sent: '已发布', read: '已读', responded: '已回复', closed: '已闭环' },
}
const statusOptions = computed(() => statusMapAll[activeTab.value] || {})

const categoryOptions = computed(() => {
  const map = {
    measure:  { internal: '内部管理措施', external: '外部协调对接' },
    policy:   { service_standard: '服务规范', process_sop: '流程SOP', management_rule: '管理规定', communication_script: '沟通话术', incident_response: '应急预案', other: '其他' },
    deptSync: { rule_change: '规则变动', process_change: '流程变动', activity_notice: '活动通知', risk_alert: '风险预警', data_sync: '数据同步', coordination: '协作请求', other: '其他' },
  }
  return map[activeTab.value] || {}
})

const priorityOptions = { low: '低', normal: '普通', high: '高', urgent: '紧急' }

// ---------- Tab 切换后重置 filter ----------
watch(activeTab, () => {
  filter.status = ''
  filter.category = ''
  filter.priority = ''
  filter.source = ''
  filter.keyword = ''
  filter.range = []
})

function resetAndLoad() { /* CommonListTable 自身会监听 filter 变化;IncidentSimpleTable 也一样 */ }

function openCreateDialog() {
  Object.keys(formData).forEach((k) => delete formData[k])
  formData.category = Object.keys(categoryOptions.value)[0] || 'other'
  if (activeTab.value === 'deptSync') formData.priority = 'normal'
  formVisible.value = true
}

async function submitForm() {
  if (!formData.title) {
    ElMessage && ElMessage.warning('请填写标题')
    return
  }
  formSubmitting.value = true
  try {
    if (activeTab.value === 'incident') {
      const payload = { title: formData.title, detail: formData.content, remark: undefined, ...formData }
      delete payload.content
      if (formData.content) payload.detail = formData.content
      await createIncident(payload)
    } else if (activeTab.value === 'measure') {
      await createMeasure({ title: formData.title, measure_type: formData.category || 'internal', expected_goal: formData.content, remark: formData.remark })
    } else if (activeTab.value === 'policy') {
      await createPolicy({ title: formData.title, category: formData.category || 'other', content: formData.content, remark: formData.remark })
    } else if (activeTab.value === 'deptSync') {
      await createDeptInfoSync({
        title: formData.title, category: formData.category || 'other', priority: formData.priority || 'normal',
        from_department: formData.from_department, to_departments: formData.to_departments,
        content: formData.content, remark: formData.remark,
      })
    }
    formVisible.value = false
    // 触发列表刷新
    filter.page = 1
    filter._t = Date.now()
  } finally {
    formSubmitting.value = false
  }
}

// ---------- 列定义(表格展示) ----------
const measureColumns = [
  { prop: 'status', label: '状态', width: 100, type: 'tag', map: statusMapAll.measure },
  { prop: 'title', label: '措施标题', minWidth: 220 },
  { prop: 'source', label: '来源', width: 100, type: 'tag', map: { audit: '审计', weekly: '周报', incident: '异常', manual: '手工' } },
  { prop: 'verify_time', label: '验证时间', width: 160 },
  { prop: 'review_result', label: '回看结论', width: 100 },
  { prop: 'created_by', label: '创建人', width: 100 },
]
function mapMeasureParams(f) {
  const p = { page: f.page, page_size: f.page_size, status: f.status || undefined, source: f.source || undefined }
  return p
}
function mapMeasureRow(r) { return r }

const policyColumns = [
  { prop: 'status', label: '状态', width: 100, type: 'tag', map: statusMapAll.policy },
  { prop: 'category', label: '分类', width: 110, type: 'tag', map: categoryOptions.value },
  { prop: 'title', label: '标题', minWidth: 240 },
  { prop: 'version', label: '版本', width: 90 },
  { prop: 'valid_from', label: '生效起', width: 160 },
  { prop: 'view_count', label: '查阅', width: 80, align: 'right' },
  { prop: 'created_by', label: '创建人', width: 100 },
]
function mapPolicyParams(f) {
  return { page: f.page, page_size: f.page_size, status: f.status || undefined, category: f.category || undefined, keyword: f.keyword || undefined }
}
function mapPolicyRow(r) { return r }
function openPolicyDetail(row) {
  // TODO 占位:后续补 PolicyDetail.vue 或抽屉详情
  console.log('[TODO:PolicyDetail]', row)
}

const deptSyncColumns = [
  { prop: 'priority', label: '优先级', width: 90, type: 'tag', map: priorityOptions, color: { low: 'info', normal: '', high: 'warning', urgent: 'danger' } },
  { prop: 'status', label: '状态', width: 90, type: 'tag', map: statusMapAll.deptSync },
  { prop: 'category', label: '分类', width: 100, type: 'tag', map: categoryOptions.value },
  { prop: 'title', label: '标题', minWidth: 220 },
  { prop: 'from_department', label: '发起部门', width: 120 },
  { prop: 'to_departments', label: '接收部门', minWidth: 160, showOverflowTooltip: true },
  { prop: 'deadline', label: '截止', width: 160 },
  { prop: 'created_by', label: '发起人', width: 100 },
]
function mapDeptSyncParams(f) {
  return {
    page: f.page, page_size: f.page_size,
    status: f.status || undefined, category: f.category || undefined,
    priority: f.priority || undefined, keyword: f.keyword || undefined,
  }
}
function mapDeptSyncRow(r) { return r }
function openDeptSyncDetail(row) {
  // TODO 占位:后续补 DeptSyncDetail.vue 或抽屉详情(含阅读回执/回复时间线)
  console.log('[TODO:DeptSyncDetail]', row)
}

function goIncidentDetail(row) {
  router.push({ path: '/management/incidents', query: { id: row.id } })
}
</script>

<style scoped>
.flex { display: flex; }
.flex-between { display: flex; align-items: center; justify-content: space-between; }
.gap-8 { gap: 8px; }
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.page-title { font-size: 18px; font-weight: 600; color: #303133; }
.page-sub { margin-top: 4px; font-size: 12px; color: #909399; }
.todo-hint { margin-bottom: 12px; }
</style>
