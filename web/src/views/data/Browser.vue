<template>
  <div class="data-browser">
    <!--
      设计说明（Design Rationale）· 工单 #100
      ────────────────────────────────────────────────────────────
      目的：运维 / 数据 / 主管在「数据采集 → 数据查看」里，按**业务口径**找到
            对应的物理表并下钻原始行；判断「这批数据到底长什么样」。

      逻辑：左侧树按「钱从哪出 → 谁在抱怨 → 谁在处理 → 交易售后 → 设备底座」
            五段切分，直接复用既有口径文档的思维框架：
              · A~E 经营五路（docs/arch/A-20260908-004，LOCKED）
              · 投诉三源（docs/standards/投诉渠道口径与对账说明.md）
            组内顺序由**服务端**决定（接口按 sort_order 返回），本页不重排 ——
            否则分组顺序会出现两处定义，#46 的菜单漂移问题会重演。

      口径：数据源清单来自 GET /data-sources；分组顺序、组内顺序、投影项
            均由服务端给出。本页只负责渲染与「真身/投影」的可见化声明。

      取舍：
        · **不做「全库表目录」**。库中 102 张表，本页只列 21 张可下钻的原始业务表。
          其余（工作流/配置/日志/维度/中间表）已有各自功能页，全塞进来会稀释
          "选一张表看数据"这个唯一用途。为消除"表是不是都在这里"的反复疑问，
          改为在页头 ⓘ 显式说明**边界与去向**，而不是把边界抹掉。
        · **不为投诉工单另建数据源**。投诉工作流需要从「投诉」组进入工单，但
          service_tickets 只能注册一次（双注册会让统计口径分裂）。故用**投影**
          （同表别名，只读）解决入口问题，并强制显示「投影」标签 + 真身位置，
          避免变成 AGENTS.md 界面纪律第 5 条禁止的"无声明重复展示"。
        · **不预置投诉筛选**。投影页展示工单全量明细，不自动过滤成"仅投诉类" ——
          因为投诉口径是 `service_category` 空白或='投诉'（BMS 导出未选类别即空白），
          预置筛很容易与「投诉分析」页的口径打架。要看投诉口径请去投诉分析页。
        · **不做树内搜索/拖拽排序**。当前 21 项，五分组可一屏扫完；加搜索是过度设计。
        · **#333 采纳状态只读展示**。经营五路原始表行级展示采纳三态徽标 + 规则码
          中文 tooltip，回答"这行原始数据到底进没进统计"（口径权威：
          docs/standards/退款五路数据标准与采纳规则.md）。规则码释义由后端
          /fields 的 enum_values 下发（单一来源在采纳作业模块 refund_adoption），
          前端零硬编码，规则升级只改后端。
          取舍：**不提供改采纳状态的任何入口** —— 人工治理一律走后端 admin API
          （adoption_overrides）留痕 + 幂等重算（铁律 15），界面直改会绕过覆盖
          审计与规则版本化，故本页四列全部只读。
    -->
    <div class="flex-row">
      <!-- 左侧数据源列表 -->
      <div class="source-sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">数据源</span>
          <span class="sidebar-count">{{ activeSourceCount }} 个</span>
          <el-tooltip placement="right" effect="light" :width="380">
            <template #content>
              <div class="boundary-tip">
                <p><b>本页列出的是「可下钻的原始业务表」（当前 {{ activeSourceCount }} 张）。</b></p>
                <p>其余约 80 张表<u>不在此列</u>，因为它们不是「选一张表看数据」的用途：</p>
                <ul>
                  <li><b>工作流</b>（任务/待办/报告/措施/事件）→ 各自有功能页</li>
                  <li><b>配置与日志</b>（用户/同步配置/采集日志/通知）→ 系统管理与采集日志页</li>
                  <li><b>维度主数据</b>（人员/分组/专项/设备）→ 人员管理、设备专项标签页</li>
                  <li><b>中间过程表</b>（勾稽对账/缺口/拒收行）→ 数据质量与对账页</li>
                </ul>
                <p class="boundary-tip-foot">全量清单见 <code>docs/standards/数据库表结构说明.md</code></p>
              </div>
            </template>
            <el-icon class="boundary-icon"><InfoFilled /></el-icon>
          </el-tooltip>
        </div>
        <el-menu
          :default-active="activeMenuKey"
          :default-openeds="openedCategories"
          class="source-menu"
          @select="handleMenuSelect"
        >
          <el-sub-menu
            v-for="grp in groupedSources"
            :key="grp.category"
            :index="grp.category"
          >
            <template #title>
              <span class="group-title">{{ grp.category }}</span>
              <span class="group-count">{{ grp.items.length }}</span>
            </template>
            <el-menu-item
              v-for="src in grp.items"
              :key="menuKey(grp.category, src.code)"
              :index="menuKey(grp.category, src.code)"
            >
              <span>{{ src.name_cn }}</span>
              <el-tag
                v-if="src.status === 'placeholder'"
                size="small"
                type="info"
                effect="plain"
                class="placeholder-tag"
              >规划中</el-tag>
              <!-- 投影: 同表别名,必须声明真身位置(否则属违规的重复展示) -->
              <el-tooltip
                v-else-if="src.is_projection"
                placement="right"
                effect="light"
                :width="300"
              >
                <template #content>
                  <div>
                    <b>投影</b>：本项是「{{ src.true_body_category }} → {{ src.true_body_name }}」的别名，
                    读取同一张表 <code>{{ src.table_name }}</code>，为只读。
                    <br>
                    放在此处是因为投诉工作流从这里进入；真身与其它口径统计都在原分组，
                    避免同一张表被注册两次导致口径分裂。
                  </div>
                </template>
                <el-tag size="small" type="warning" effect="plain" class="proj-tag">投影</el-tag>
              </el-tooltip>
              <el-badge
                v-else-if="src.table_name"
                :value="getTableCount(src.code)"
                class="count-badge"
              />
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </div>

      <!-- 右侧主区域 -->
      <div v-if="currentSource" class="main-area">
        <!-- 占位数据源:规划中,暂未实现 -->
        <div v-if="isPlaceholder" class="placeholder-area">
          <el-empty :image-size="140" description="该数据源正在规划中，暂未实现">
            <template #image><el-icon size="80" color="#c0c4cc"><DataAnalysis /></el-icon></template>
            <p class="placeholder-hint">{{ currentSource.name_cn }}（{{ currentSource.code }}）尚未接入数据</p>
          </el-empty>
        </div>

        <!-- 数据汇总卡片 -->
        <div v-if="summary" class="summary-cards">
          <div class="summary-item">
            <span class="summary-label">总记录数</span>
            <span class="summary-value">{{ summary.total_count.toLocaleString() }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">时间范围</span>
            <span class="summary-value" style="font-size: 13px;">
              {{ fmtDate(summary.time_range?.min) }} ~ {{ fmtDate(summary.time_range?.max) }}
            </span>
          </div>
          <div class="summary-item">
            <span class="summary-label">设备SN数</span>
            <span class="summary-value">{{ summary.sn_stats?.unique_count?.toLocaleString() || 0 }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">导入批次</span>
            <span class="summary-value">{{ uploadRecords.length }}</span>
          </div>
        </div>

        <!-- 导入记录列表 -->
        <div v-if="uploadRecords.length" class="upload-section">
          <div class="section-header">
            <el-icon><Upload /></el-icon>
            <span class="section-title">导入记录</span>
          </div>
          <el-table :data="uploadRecords" size="small" border stripe :max-height="180">
            <el-table-column prop="filename" label="文件名" width="200" show-overflow-tooltip />
            <el-table-column prop="total_rows" label="总行数" width="80" />
            <el-table-column prop="inserted_rows" label="新增" width="70" />
            <el-table-column prop="updated_rows" label="更新" width="70" />
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.status === 'imported' ? 'success' : (row.status === 'failed' ? 'danger' : 'warning')" size="small">
                  {{ row.status === 'imported' ? '已导入' : (row.status === 'failed' ? '失败' : '处理中') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="导入时间" width="160">
              <template #default="{ row }">{{ fmtDateTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 表格区域 -->
        <div v-if="!isPlaceholder" class="table-area">
          <div class="table-header">
            <div class="header-info">
              <el-icon><Coin /></el-icon>
              <span class="source-name">{{ currentSource.name_cn }}</span>
              <!-- 显示物理表名而非 code: 落实「中文名↔表名一一对应」,让人能直接对照库 -->
              <el-tag type="info" size="small">{{ currentSource.table_name }}</el-tag>
              <el-tag
                v-if="currentSource.is_projection"
                type="warning"
                size="small"
                effect="plain"
              >只读投影 · 真身「{{ currentSource.true_body_name }}」</el-tag>
            </div>
            <div class="header-actions">
              <!-- #333: 采纳状态筛选(仅经营五路原始表显示) —— 待定行治理跟进入口 -->
              <el-select
                v-if="hasAdoptState"
                v-model="filters.adopt_state"
                size="small"
                style="width: 160px"
                placeholder="采纳状态: 全部"
                clearable
                @change="handleFilterChange"
              >
                <el-option label="采纳(进统计)" value="adopted" />
                <el-option label="排除(不进统计)" value="excluded" />
                <el-option label="待定(需治理跟进)" value="pending" />
              </el-select>
              <el-button
                type="primary"
                size="small"
                :loading="exporting"
                :disabled="!tableData.length"
                @click="handleExport"
              >
                <el-icon><Download /></el-icon> 导出 Excel
              </el-button>
              <el-button text type="primary" size="small" @click="refreshData">
                <el-icon><Refresh /></el-icon> 刷新
              </el-button>
            </div>
          </div>

          <!-- 数据表格 -->
          <div class="table-container">
            <el-table
              :data="tableData"
              :columns="tableColumns"
              border
              stripe
              size="small"
              height="100%"
              @sort-change="handleSortChange"
            >
              <el-table-column
                v-for="col in tableColumns"
                :key="col.prop"
                :prop="col.prop"
                :label="col.label"
                :width="col.width"
                :sortable="col.sortable"
                show-overflow-tooltip
              >
                <!-- #333: 采纳状态列表头带口径说明(ⓘ) —— 页面层可见说明 -->
                <template v-if="col.prop === 'adopt_state'" #header>
                  <span>{{ col.label }}</span>
                  <el-tooltip placement="top" effect="light" :width="340">
                    <template #content>
                      <div style="line-height: 1.7">
                        <b>这行原始数据进没进统计？</b>（口径：退款五路数据标准与采纳规则.md）<br>
                        <el-tag size="small" type="success">采纳</el-tag> 进入 std_* 标准表，计入经营统计<br>
                        <el-tag size="small" type="info">排除</el-tag> 规则排除，不计入统计（原因见「采纳规则码」列）<br>
                        <el-tag size="small" type="warning">待定</el-tag> 未知枚举，待业务确认，暂不计入<br>
                        <span style="opacity:.7">未判定 = 尚未跑过采纳作业（新同步行）。<br>
                        本页只读：改判走后台人工覆盖 API 留痕，规则版本化可重算。</span>
                      </div>
                    </template>
                    <el-icon class="adopt-info-icon"><InfoFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <!-- #333: 采纳三态徽标 -->
                  <template v-if="col.prop === 'adopt_state'">
                    <el-tag
                      v-if="row.adopt_state"
                      size="small"
                      :type="adoptStateMeta[row.adopt_state]?.type || 'info'"
                    >{{ adoptStateMeta[row.adopt_state]?.label || row.adopt_state }}</el-tag>
                    <span v-else class="cell-dim">未判定</span>
                  </template>
                  <!-- #333: 规则码 + 中文释义 tooltip(释义来自后端 enum 下发) -->
                  <template v-else-if="col.prop === 'adopt_reason'">
                    <el-tooltip v-if="row.adopt_reason" placement="top" effect="light" :width="320">
                      <template #content>
                        <div style="max-width: 300px; line-height: 1.6">
                          {{ adoptReasonCn(row.adopt_reason) }}
                          <div v-if="row.adopt_rule_ver" style="margin-top: 4px; opacity: .7">
                            规则版本: {{ row.adopt_rule_ver }}
                          </div>
                        </div>
                      </template>
                      <span class="adopt-reason-cell">{{ row.adopt_reason }}</span>
                    </el-tooltip>
                    <span v-else>-</span>
                  </template>
                  <template v-else>{{ fmtCell(row[col.prop], col.dataType) }}</template>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 分页 -->
          <div class="pagination-bar">
            <span class="total-info">共 {{ pagination.total }} 条记录</span>
            <el-pagination
              v-model:current-page="pagination.page"
              v-model:page-size="pagination.pageSize"
              :total="pagination.total"
              :page-sizes="[10, 20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              size="small"
              @size-change="handlePageSizeChange"
              @current-change="handlePageChange"
            />
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-else class="empty-area">
        <el-empty description="请选择一个数据源查看数据" :image-size="120">
          <template #image><el-icon size="64"><Coin /></el-icon></template>
        </el-empty>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import dayjs from 'dayjs'
import { InfoFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import request from '@/api'

const sources = ref([])
const currentSource = ref(null)
const currentFields = ref([])
const tableData = ref([])
const pagination = ref({ page: 1, pageSize: 20, total: 0 })
const filters = ref({})
const searchKeyword = ref('')
const sortField = ref('')
const sortDir = ref('desc')
const tableCounts = ref({})
const summary = ref(null)
const uploadRecords = ref([])
const exporting = ref(false)

// 数据源业务分组顺序 —— ⚠️ 不再本地硬编码！
//   顺序（分组顺序 + 组内顺序）由服务端 GET /data-sources 返回，本页只做「按后端给的顺序分组」：
//   后端已按 (分组顺序, sort_order) 排好，这里按首次出现顺序收集分组即可。
//   历史教训: 前端再定义一份 CATEGORY_ORDER → 两份定义漂移（同 A-20260910-004 菜单漂移）。
const groupedSources = computed(() => {
  const groups = new Map()
  for (const src of sources.value) {
    const cat = src.category || '其他'
    if (!groups.has(cat)) groups.set(cat, [])
    groups.get(cat).push(src)
  }
  return [...groups.entries()].map(([category, items]) => ({ category, items }))
})

/** 菜单项唯一 key：分组::code
 *  ⚠️ 必须带分组前缀 —— 因为同一 code 可出现在两个分组（投影机制，如 ticket 同时
 *  在「投诉」和「工单与客服」）。若 index 只用 code，el-menu 的 default-active
 *  会同时点亮两项，用户不知道自己在看哪个分组下的同一张表。
 */
function menuKey(category, code) {
  return `${category}::${code}`
}

/** 当前选中项对应的菜单 key（若无选中则空，el-menu 不高亮） */
const activeMenuKey = computed(() =>
  currentSource.value ? menuKey(currentSource.value.category, currentSource.value.code) : ''
)

/** 菜单选中 → 按 (分组, code) 定位数据源 */
function handleMenuSelect(key) {
  const sep = key.lastIndexOf('::')
  if (sep < 0) return
  const category = key.slice(0, sep)
  const code = key.slice(sep + 2)
  // 同一 code 可能在多个分组，必须用分组+code 双条件定位
  const src = sources.value.find((s) => s.code === code && (s.category || '其他') === category)
  if (src) handleSourceSelect(src)
}

// 默认展开全部分组
const openedCategories = computed(() => groupedSources.value.map((g) => g.category))

// 当前是否为占位数据源(规划中,无实际表)
const isPlaceholder = computed(() => currentSource.value?.status === 'placeholder')

// 已实现数据源数量(排除占位项与投影项 —— 投影是别名,重复计入会让用户以为表变多了)
const activeSourceCount = computed(
  () => sources.value.filter((s) => s.status !== 'placeholder' && !s.is_projection).length
)

async function loadSources() {
  try {
    const res = await request.get('/data-sources', { params: { include_placeholder: true } })
    // 保留服务端顺序（后端已按 分组顺序 → 组内 sort_order 排好）
    sources.value = res.data || []
    if (sources.value.length) {
      handleSourceSelect(sources.value[0])
    }
  } catch (e) {
    console.error('加载数据源失败', e)
  }
}

async function loadFields(code) {
  try {
    const res = await request.get(`/data-sources/${code}/fields`)
    currentFields.value = res.data || []
  } catch (e) {
    console.error('加载字段失败', e)
  }
}

async function loadSummary(code) {
  try {
    const res = await request.get(`/data-sources/${code}/summary`)
    summary.value = res.data || null
  } catch (e) {
    console.error('加载汇总失败', e)
    summary.value = null
  }
}

async function loadUploadRecords(code) {
  try {
    const res = await request.get(`/uploads/records?data_type=${code}&page=1&page_size=100`)
    uploadRecords.value = res.data?.items || []
  } catch (e) {
    console.error('加载导入记录失败', e)
    uploadRecords.value = []
  }
}

async function loadData() {
  if (!currentSource.value) return
  try {
    const params = {
      page: pagination.value.page,
      page_size: pagination.value.pageSize,
    }
    if (sortField.value) {
      params.sort_field = sortField.value
      params.sort_dir = sortDir.value
    }
    const filterObj = {}
    for (const [key, value] of Object.entries(filters.value)) {
      if (value) filterObj[key] = value
    }
    if (searchKeyword.value) {
      const searchFields = currentFields.value
        .filter((f) => f.is_filterable && f.data_type === 'string')
        .slice(0, 3)
      searchFields.forEach((f) => {
        filterObj[`${f.field_name}__contains`] = searchKeyword.value
      })
    }
    if (Object.keys(filterObj).length) {
      params.filters = JSON.stringify(filterObj)
    }
    const res = await request.get(`/data-sources/${currentSource.value.code}/records`, { params })
    tableData.value = res.data.items || []
    pagination.value.total = res.data.total || 0
    tableCounts.value[currentSource.value.code] = res.data.total || 0
  } catch (e) {
    console.error('加载数据失败', e)
  }
}

function handleSourceSelect(src) {
  // 兼容传 code 的旧调用方式（按 code 取第一个匹配项）
  if (typeof src === 'string') {
    src = sources.value.find((s) => s.code === src)
  }
  if (src) {
    currentSource.value = src
    filters.value = {}
    searchKeyword.value = ''
    sortField.value = ''
    pagination.value.page = 1
    // 占位数据源无实际表,不加载字段/汇总/记录/明细
    if (src.status === 'placeholder') return
    // 投影与真身共用接口（后端把投影 code 解析回真身），故此处无需特殊分支
    loadFields(src.code)
    loadSummary(src.code)
    loadUploadRecords(src.code)
    loadData()
  }
}

const filterableFields = computed(() => {
  return currentFields.value.filter((f) => f.is_filterable)
})

function getFieldOptions(field) {
  if (field.enum_values) {
    return field.enum_values.map((v) => ({ value: v, label: v }))
  }
  return []
}

const tableColumns = computed(() => {
  return currentFields.value.map((f) => {
    let width = null
    if (f.data_type === 'timestamp') width = 170
    if (f.data_type === 'numeric') width = 120
    if (f.semantic_role === 'sn') width = 180
    // #333: 采纳治理四列固定列宽(徽标/规则码/版本/时间)
    if (f.field_name === 'adopt_state') width = 150
    if (f.field_name === 'adopt_reason') width = 150
    if (f.field_name === 'adopt_rule_ver') width = 130
    return {
      prop: f.field_name,
      label: f.field_name_cn || f.field_name,
      width,
      sortable: f.is_filterable && f.data_type !== 'jsonb',
      dataType: f.data_type,
    }
  })
})

// ── #333: 采纳治理四列展示辅助 ────────────────────────────────────────────
// 三态徽标文案与颜色(纯展示映射; 枚举值本身由后端权威定义)
const adoptStateMeta = {
  adopted: { label: '采纳', type: 'success' },
  excluded: { label: '排除', type: 'info' },
  pending: { label: '待定', type: 'warning' },
}

// 当前数据源是否含采纳状态列(经营五路原始表; 其它表不显示筛选与徽标逻辑)
const hasAdoptState = computed(() =>
  currentFields.value.some((f) => f.field_name === 'adopt_state')
)

// 规则码 → 中文释义: 从后端 /fields 的 adopt_reason enum_values("code=中文")解析,
// 前端零硬编码 —— 规则码升级只改采纳作业模块, 界面自动跟随。
const adoptReasonMap = computed(() => {
  const f = currentFields.value.find((x) => x.field_name === 'adopt_reason')
  const map = {}
  for (const e of f?.enum_values || []) {
    const i = e.indexOf('=')
    if (i > 0) map[e.slice(0, i)] = e.slice(i + 1)
  }
  return map
})

function adoptReasonCn(code) {
  // 人工覆盖行带 manual: 前缀(机器规则码仍保留, 见采纳作业模块人工覆盖约定)
  if (code?.startsWith('manual:')) {
    const raw = code.slice(7)
    return `人工覆盖(原规则: ${adoptReasonMap.value[raw] || raw})`
  }
  return adoptReasonMap.value[code] || code
}

function fmtCell(value, dataType) {
  if (value == null) return '-'
  if (dataType === 'numeric') {
    if (typeof value === 'number') return value.toFixed(2)
    return value
  }
  if (dataType === 'timestamp') {
    // 用 dayjs 解析,自动处理 UTC(带 Z/+00:00)转本地时区
    const d = dayjs(value)
    return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(value)
  }
  if (dataType === 'jsonb') {
    return JSON.stringify(value)
  }
  return value
}

function fmtDate(dateStr) {
  if (!dateStr) return '-'
  const d = dayjs(dateStr)
  return d.isValid() ? d.format('YYYY-MM-DD') : dateStr.substring(0, 10)
}

function fmtDateTime(dateStr) {
  if (!dateStr) return '-'
  const d = dayjs(dateStr)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(dateStr)
}

function getTableCount(code) {
  return tableCounts.value[code] || 0
}

function handleFilterChange() {
  pagination.value.page = 1
  loadData()
}

function handleSearch() {
  pagination.value.page = 1
  loadData()
}

function handleSortChange({ prop, order }) {
  sortField.value = prop
  sortDir.value = order === 'ascending' ? 'asc' : 'desc'
  loadData()
}

function handlePageSizeChange(size) {
  pagination.value.pageSize = size
  pagination.value.page = 1
  loadData()
}

function handlePageChange(page) {
  pagination.value.page = page
  loadData()
}

function refreshData() {
  loadSummary(currentSource.value?.code)
  loadUploadRecords(currentSource.value?.code)
  loadData()
}

async function handleExport() {
  if (!currentSource.value || isPlaceholder.value) return
  exporting.value = true
  try {
    // 复用 loadData 的筛选/排序参数(去掉分页,导出全部匹配行)
    const params = {}
    if (sortField.value) {
      params.sort_field = sortField.value
      params.sort_dir = sortDir.value
    }
    const filterObj = {}
    for (const [key, value] of Object.entries(filters.value)) {
      if (value) filterObj[key] = value
    }
    if (searchKeyword.value) {
      const searchFields = currentFields.value
        .filter((f) => f.is_filterable && f.data_type === 'string')
        .slice(0, 3)
      searchFields.forEach((f) => {
        filterObj[`${f.field_name}__contains`] = searchKeyword.value
      })
    }
    if (Object.keys(filterObj).length) {
      params.filters = JSON.stringify(filterObj)
    }

    const res = await request.get(`/data-sources/${currentSource.value.code}/export`, {
      params,
      responseType: 'blob',
      timeout: 120000,
    })
    // axios 响应拦截器对 blob 成功响应返回 response.data(即 Blob);兼容返回完整 response 的情况
    const blobData = res instanceof Blob ? res : res?.data
    const blob = new Blob([blobData], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const ts = dayjs().format('YYYYMMDD_HHmmss')
    const fname = `${currentSource.value.name_cn}_导出_${ts}.xlsx`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fname
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    ElMessage.success('已导出')
  } catch (e) {
    // 后端错误以 blob 形式返回时,读取 blob 文本提取 detail(如"超过导出上限")
    let msg = '导出失败'
    const errBlob = e?.response?.data
    if (errBlob instanceof Blob) {
      try {
        const text = await errBlob.text()
        const j = JSON.parse(text)
        msg = j?.detail || j?.message || msg
      } catch {
        // blob 非 JSON,忽略
      }
    } else {
      msg = e?.response?.data?.detail || e?.message || msg
    }
    ElMessage.error(msg)
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  loadSources()
})
</script>

<style scoped>
.data-browser {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.flex-row {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.source-sidebar {
  width: 260px;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  background: #fafafa;
}
.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e4e7ed;
}
.sidebar-title { font-size: 16px; font-weight: 600; }
.sidebar-count { font-size: 12px; color: #909399; }
/* 边界说明 ⓘ —— 消除「表是不是都在这里」的反复疑问（见设计说明「取舍」段） */
.boundary-icon {
  cursor: help;
  color: #909399;
  font-size: 15px;
}
.boundary-icon:hover { color: #409eff; }
.boundary-tip { font-size: 12px; line-height: 1.7; }
.boundary-tip p { margin: 0 0 6px; }
.boundary-tip ul { margin: 0 0 6px; padding-left: 16px; }
.boundary-tip li { margin-bottom: 2px; }
.boundary-tip-foot { color: #909399; }
/* 投影标签：必须让使用者看出"这不是第二张表，只是别名" */
.proj-tag { margin-left: 6px; }
.source-menu { flex: 1; border-right: none; }
.count-badge { margin-left: 8px; }
.group-title { font-size: 13px; font-weight: 600; color: #303133; }
.group-count {
  margin-left: 6px;
  font-size: 12px;
  color: #909399;
}
.source-menu :deep(.el-sub-menu__title) {
  height: 40px;
  line-height: 40px;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.summary-cards {
  display: flex;
  gap: 24px;
  padding: 8px 20px;
  background: #fafafa;
  border-bottom: 1px solid #e4e7ed;
}
.summary-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.summary-label {
  font-size: 12px;
  color: #909399;
}
.summary-value {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.upload-section {
  padding: 8px 20px 12px;
  border-bottom: 1px solid #f0f0f0;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #606266;
}
.section-title { font-size: 13px; font-weight: 500; }

.table-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.table-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  border-bottom: 1px solid #e4e7ed;
}
.header-info { display: flex; align-items: center; gap: 8px; }
.source-name { font-size: 14px; font-weight: 600; }
.table-container { flex: 1; overflow: auto; padding: 12px 20px; }
.pagination-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-top: 1px solid #e4e7ed;
}
.total-info { font-size: 13px; color: #606266; }
.empty-area { flex: 1; display: flex; align-items: center; justify-content: center; }
.placeholder-area {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
}
.placeholder-hint {
  margin-top: 12px;
  font-size: 13px;
  color: #909399;
}
.placeholder-tag { margin-left: 6px; }

/* #333: 采纳治理列展示样式 */
.adopt-info-icon {
  margin-left: 2px;
  vertical-align: -2px;
  color: #909399;
  cursor: help;
}
.adopt-reason-cell {
  font-family: monospace;
  font-size: 12px;
  cursor: help;
}
.cell-dim { color: #c0c4cc; }
</style>
