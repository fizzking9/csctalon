<template>
  <div class="audit-timeline">
    <!-- 类型筛选 + 关键词搜索 -->
    <div class="filter-bar">
      <el-radio-group v-model="activeType" size="small">
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
        style="width: 240px; margin-left: 12px"
        :prefix-icon="Search"
      />
    </div>

    <!-- 双视图切换 -->
    <el-tabs v-model="view" class="view-tabs">
      <el-tab-pane label="时序流" name="timeline">
        <div class="timeline-wrap" v-loading="loading">
          <el-empty v-if="!filtered.length" description="无记录" />
          <el-timeline v-else>
            <el-timeline-item
              v-for="(it, idx) in filtered"
              :key="idx"
              :type="colorOf(it.type)"
              :timestamp="fmtTime(it.time)"
              placement="top"
            >
              <el-card class="event-card" shadow="hover">
                <div class="event-head">
                  <el-tag :type="colorOf(it.type)" size="small" effect="light">
                    {{ labelOf(it.type) }}
                  </el-tag>
                  <span class="event-title">{{ it.title }}</span>
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
          max-height="560"
          v-loading="loading"
        >
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.time) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="100">
            <template #default="{ row }">
              <el-tag :type="colorOf(row.type)" size="small" effect="light">{{ labelOf(row.type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="ref_id" label="单号/标识" width="180" show-overflow-tooltip />
          <el-table-column prop="summary" label="摘要" min-width="260" show-overflow-tooltip />
          <el-table-column label="金额" width="120" align="right">
            <template #default="{ row }">
              <span v-if="row.amount != null">¥ {{ Number(row.amount).toFixed(2) }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="详情" width="80">
            <template #default="{ row }">
              <el-popover trigger="click" width="320" placement="left">
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
})

const activeType = ref('')
const keyword = ref('')
const view = ref('timeline')

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

// 时序卡片只显示前 6 个字段,避免过长
function visibleFields(fields) {
  if (!fields) return {}
  const entries = Object.entries(fields).filter(([, v]) => v != null && v !== '')
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
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.view-tabs { margin-top: 4px; }
.timeline-wrap {
  max-height: 600px;
  overflow-y: auto;
  padding-right: 8px;
}
.event-card { margin-bottom: 0; }
.event-card :deep(.el-card__body) { padding: 10px 14px; }
.event-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.event-title { font-weight: 600; color: #303133; }
.event-amount {
  margin-left: auto;
  font-weight: 700;
  color: #f56c6c;
}
.event-summary {
  color: #606266;
  font-size: 13px;
  margin-bottom: 6px;
}
.event-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
}
.field-chip {
  font-size: 12px;
  color: #909399;
}
.field-label { color: #c0c4cc; }
.field-value { color: #606266; }
.popover-fields { max-height: 360px; overflow-y: auto; }
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
</style>
