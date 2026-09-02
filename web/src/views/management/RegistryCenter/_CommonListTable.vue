<template>
  <div class="common-list-table">
    <el-table
      v-loading="loading"
      :data="list"
      border
      stripe
      style="cursor: pointer"
      @row-click="(row) => $emit('view', row)"
    >
      <el-table-column
        v-for="col in columns"
        :key="col.prop"
        :prop="col.prop"
        :label="col.label"
        :width="col.width"
        :min-width="col.minWidth"
        :align="col.align || 'left'"
        :show-overflow-tooltip="col.showOverflowTooltip !== false"
      >
        <template v-if="col.type === 'tag'" #default="{ row }">
          <el-tag
            :type="tagColor(col, row[col.prop])"
            size="small"
            effect="plain"
          >
            {{ (col.map && col.map[row[col.prop]]) || row[col.prop] || '-' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" align="center" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click.stop="$emit('view', row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="mt-16 flex-between">
      <span>共 {{ total }} 条</span>
      <el-pagination
        layout="total, prev, pager, next"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        @current-change="onPageChange"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  columns: { type: Array, required: true },
  rowKey: { type: [String, Number], default: 'id' },
  api: { type: Function, required: true },
  mapParams: { type: Function, default: (f) => ({ ...f }) },
  mapRow: { type: Function, default: (r) => r },
  filter: { type: Object, required: true },
})

defineEmits(['view'])

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(props.filter.page || 1)
const pageSize = ref(props.filter.page_size || 20)

function tagColor(col, v) {
  if (col.color && col.color[v]) return col.color[v]
  // 状态简易配色
  const map = {
    draft: 'info', pending_approval: 'warning', published: 'success', archived: 'info',
    registered: 'info', processing: 'warning', calmed: '', reviewed: 'success',
    executing: 'warning', pending_review: 'warning', effective: 'success', ineffective: 'danger',
    sent: '', read: 'warning', responded: '', closed: 'success',
  }
  return map[v] || 'info'
}

function onPageChange(p) {
  page.value = p
  props.filter.page = p
}

async function loadData() {
  loading.value = true
  try {
    const params = props.mapParams({ ...props.filter, page: page.value, page_size: pageSize.value })
    const res = await props.api(params)
    const data = res.data || {}
    total.value = data.total || 0
    list.value = (data.items || []).map((r) => props.mapRow(r))
  } finally {
    loading.value = false
  }
}

// 页面加载 + filter 变化(包括 parent 改 _t 强制刷新) + page/pageSize 变化均触发
watch(
  [
    () => [props.filter.status, props.filter.category, props.filter.priority, props.filter.source,
           props.filter.keyword, (props.filter.range || []).join('|'), props.filter._t],
    () => page.value,
    () => pageSize.value,
  ],
  loadData,
  { immediate: true, deep: true },
)
</script>

<style scoped>
.flex-between { display: flex; align-items: center; justify-content: space-between; }
.mt-16 { margin-top: 16px; }
</style>
