<template>
  <div class="audit-timeline" :class="{ 'at-compact': compact }">
    <!-- 类型筛选 + 关键词搜索 -->
    <div class="filter-bar">
      <el-radio-group v-model="activeType" size="small" class="filter-types">
        <el-radio-button label="">全部 ({{ items.length }})</el-radio-button>
        <el-radio-button
          v-for="t in typeList"
          :key="t.key"
          :label="t.key"
        >
          {{ t.label }} ({{ countOf(t.key) }})
        </el-radio-button>
      </el-radio-group>
      <el-input
        v-model="keyword"
        placeholder="搜索摘要/单号/操作人"
        size="small"
        clearable
        class="filter-search"
        :prefix-icon="Search"
      />
    </div>

    <!-- 双视图切换 -->
    <el-tabs v-model="view" class="view-tabs">
      <el-tab-pane label="时序流" name="timeline">
        <div class="timeline-wrap" v-loading="loading">
          <el-empty v-if="!filtered.length" description="无记录" :image-size="compact ? 60 : 80" />
          <el-timeline v-else class="event-timeline">
            <el-timeline-item
              v-for="(it, idx) in filtered"
              :key="idx"
              :type="colorOf(it.type)"
              :timestamp="fmtTime(it.time)"
              placement="top"
            >
              <el-card shadow="hover" class="event-card">
                <div class="event-head">
                  <el-tag :type="colorOf(it.type)" size="small" effect="light">
                    {{ labelOf(it.type) }}
                  </el-tag>
                  <span class="event-title">{{ it.title }}</span>
                  <span
                    v-if="it.device_sn"
                    class="event-sn"
                    title="点击查看设备分析"
                    @click="emit('sn-click', it.device_sn)"
                  >SN: {{ it.device_sn }}</span>
                  <span v-if="it.amount != null" class="event-amount">
                    ¥ {{ Number(it.amount).toFixed(2) }}
                  </span>
                </div>
                <div class="event-summary">{{ it.summary }}</div>
                <div class="event-fields" v-if="it.fields">
                  <span v-for="(v, k) in visibleFields(it.fields)" :key="k" class="field-chip">
                    <span class="field-label">{{ k }}:</span>
                    <span class="field-value">{{ fmtVal(v) }}</span>
                  </span>
                </div>
              </el-card>
            </el-timeline-item>
          </el-timeline>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="`明细表 (${filtered.length})`" name="table">
        <el-table
          :data="filtered"
          border
          stripe
          size="small"
          v-loading="loading"
          :max-height="compact ? 600 : 560"
          class="detail-table"
        >
          <el-table-column label="时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.time) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <el-tag :type="colorOf(row.type)" size="small" effect="light">{{ labelOf(row.type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="ref_id" label="单号/标识" width="160" show-overflow-tooltip />
          <el-table-column v-if="hasSn" label="设备SN" width="140" show-overflow-tooltip>
            <template #default="{ row }">
              <el-button
                v-if="row.device_sn"
                link
                type="primary"
                size="small"
                @click="emit('sn-click', row.device_sn)"
              >{{ row.device_sn }}</el-button>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column prop="summary" label="摘要" min-width="240" show-overflow-tooltip />
          <el-table-column label="金额" width="100" align="right">
            <template #default="{ row }">
              <span v-if="row.amount != null">¥ {{ Number(row.amount).toFixed(2) }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="详情" width="70">
            <template #default="{ row }">
              <el-popover trigger="click" :width="compact ? 280 : 320" placement="left">
                <template #reference>
                  <el-button link type="primary" size="small">查看</el-button>
                </template>
                <div class="popover-fields">
                  <div v-for="(v, k) in row.fields" :key="k" class="popover-row">
                    <span class="popover-label">{{ k }}</span>
                    <span class="popover-value">{{ fmtVal(v) }}</span>
                  </div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import dayjs from 'dayjs'

const props = defineProps({
  items: { type: Array, default: () => [] },
  typeMeta: { type: Object, default: () => ({}) },
  loading: { type: Boolean, default: false },
  /** 紧凑模式(弹窗内使用):缩小间距/字号,不限制内容高度 */
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['sn-click'])

const activeType = ref('')
const keyword = ref('')
const view = ref('timeline')

// 是否存在带设备SN的记录(决定明细表是否显示SN列)
const hasSn = computed(() => props.items.some((it) => it.device_sn))

const typeList = computed(() => {
  return Object.entries(props.typeMeta || {}).map(([key, meta]) => ({ key, label: meta.label }))
})

function countOf(type) {
  return props.items.filter((it) => it.type === type).length
}

function colorOf(type) {
  return props.typeMeta?.[type]?.color || 'info'
}

function labelOf(type) {
  return props.typeMeta?.[type]?.label || type
}

const filtered = computed(() => {
  let list = props.items
  if (activeType.value) {
    list = list.filter((it) => it.type === activeType.value)
  }
  if (keyword.value) {
    const kw = keyword.value.toLowerCase()
    list = list.filter((it) => {
      const blob = `${it.title || ''} ${it.summary || ''} ${it.ref_id || ''} ${JSON.stringify(it.fields || {})}`.toLowerCase()
      return blob.includes(kw)
    })
  }
  return list
})

// 时序卡片只显示前 6 个字段,避免过长;"设备SN"单独以可点击chip展示,不重复出现在字段里
function visibleFields(fields) {
  if (!fields) return {}
  const entries = Object.entries(fields).filter(
    ([k, v]) => v != null && v !== '' && k !== '设备SN'
  )
  return Object.fromEntries(entries.slice(0, 6))
}

function fmtTime(t) {
  if (!t) return '-'
  return dayjs(t).format('YYYY-MM-DD HH:mm:ss')
}

function fmtVal(v) {
  if (v == null || v === '') return '-'
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v)) {
    return dayjs(v).format('YYYY-MM-DD HH:mm:ss')
  }
  return String(v)
}
</script>

<style scoped>
.audit-timeline { width: 100%; }

/* 筛选栏 */
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.filter-types {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
}
.filter-types :deep(.el-radio-button__inner) {
  padding: 5px 10px;
  font-size: 12px;
}
.filter-search {
  width: 200px;
  flex-shrink: 0;
}

/* Tab */
.view-tabs { margin-top: 0; }
.view-tabs :deep(.el-tabs__header) {
  margin: 0 0 6px;
}
.view-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
}
.view-tabs :deep(.el-tabs__item) {
  height: 30px;
  line-height: 30px;
  font-size: 13px;
  padding: 0 12px;
}

/* 时序流 */
.timeline-wrap {
  overflow-y: auto;
  padding-right: 4px;
}
.event-timeline :deep(.el-timeline-item__timestamp) {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
  margin-bottom: 2px;
}
.event-timeline :deep(.el-timeline-item__node--normal) {
  left: 5px;
}
.event-timeline :deep(.el-timeline-item__wrapper) {
  padding-left: 20px;
}
.event-card { margin-bottom: 0; }
.event-card :deep(.el-card__body) { padding: 8px 10px; }

.event-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
  flex-wrap: wrap;
}
.event-title { font-weight: 600; color: #303133; font-size: 13px; }
.event-sn {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: #409eff;
  background: #ecf5ff;
  padding: 1px 5px;
  border-radius: 3px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s;
  word-break: break-all;
}
.event-sn:hover { background: #d9ecff; }
.event-amount {
  margin-left: auto;
  font-weight: 700;
  color: #f56c6c;
  font-size: 13px;
}

.event-summary {
  color: #606266;
  font-size: 12px;
  margin-bottom: 4px;
  line-height: 1.5;
}
.event-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 10px;
}
.field-chip {
  font-size: 11px;
  color: #909399;
}
.field-label { color: #c0c4cc; }
.field-value { color: #606266; }

/* 明细表 */
.detail-table :deep(.el-table th.el-table__cell),
.detail-table :deep(.el-table td.el-table__cell) {
  padding: 4px 0;
  font-size: 12px;
}

.popover-fields { max-height: 320px; overflow-y: auto; }
.popover-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 3px 0;
  border-bottom: 1px dashed #ebeef5;
  font-size: 12px;
}
.popover-row:last-child { border-bottom: none; }
.popover-label { color: #909399; flex-shrink: 0; }
.popover-value { color: #303133; text-align: right; word-break: break-all; }

/* ---------- 紧凑模式(弹窗) ---------- */
.at-compact .filter-bar {
  margin-bottom: 4px;
  gap: 4px;
}
.at-compact .filter-types :deep(.el-radio-button__inner) {
  padding: 4px 8px;
  font-size: 11px;
}
.at-compact .filter-search {
  width: 160px;
}
.at-compact .view-tabs :deep(.el-tabs__header) {
  margin-bottom: 4px;
}
.at-compact .view-tabs :deep(.el-tabs__item) {
  height: 26px;
  line-height: 26px;
  font-size: 12px;
  padding: 0 10px;
}
.at-compact .event-card :deep(.el-card__body) {
  padding: 5px 8px;
}
.at-compact .event-head {
  gap: 4px;
  margin-bottom: 2px;
}
.at-compact .event-title { font-size: 12px; }
.at-compact .event-amount { font-size: 12px; }
.at-compact .event-summary {
  font-size: 11px;
  margin-bottom: 2px;
  line-height: 1.4;
}
.at-compact .event-fields {
  gap: 2px 8px;
}
.at-compact .field-chip { font-size: 10px; }
.at-compact .event-timeline :deep(.el-timeline-item__timestamp) {
  font-size: 11px;
}
.at-compact .event-timeline :deep(.el-timeline-item__node) {
  width: 10px;
  height: 10px;
}
.at-compact .event-timeline :deep(.el-timeline-item__wrapper) {
  padding-left: 16px;
}
.at-compact .event-timeline :deep(.el-timeline-item__tail) {
  left: 4px;
}
.at-compact .event-sn {
  font-size: 10px;
  padding: 0 4px;
}
.at-compact .detail-table :deep(.el-table th.el-table__cell),
.at-compact .detail-table :deep(.el-table td.el-table__cell) {
  padding: 2px 0;
  font-size: 11px;
}
.at-compact .popover-fields { max-height: 280px; }
.at-compact .popover-row {
  padding: 2px 0;
  font-size: 11px;
}

@media (max-width: 768px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
  }
  .filter-types {
    overflow-x: auto;
    white-space: nowrap;
  }
  .filter-search {
    width: 100%;
  }
}
</style>
