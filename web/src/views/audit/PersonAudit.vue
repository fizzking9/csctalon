<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">人员审计时序</div>
      <div class="flex" style="gap: 8px; align-items: center">
        <el-select
          v-model="personId"
          filterable
          remote
          clearable
          placeholder="搜索人员姓名"
          :remote-method="searchPersons"
          :loading="searching"
          style="width: 240px"
          @change="onPersonChange"
        >
          <el-option
            v-for="p in personOptions"
            :key="p.id"
            :label="`${p.display_name}${p.department ? ' · ' + p.department : ''}`"
            :value="p.id"
          />
        </el-select>
        <el-select v-model="days" style="width: 110px" @change="loadTimeline">
          <el-option label="近 30 天" :value="30" />
          <el-option label="近 90 天" :value="90" />
          <el-option label="近 180 天" :value="180" />
          <el-option label="近 365 天" :value="365" />
          <el-option label="不限" :value="0" />
        </el-select>
        <el-select v-model="limit" style="width: 100px" @change="loadTimeline">
          <el-option :value="50" label="50 条" />
          <el-option :value="100" label="100 条" />
          <el-option :value="200" label="200 条" />
          <el-option :value="500" label="500 条" />
        </el-select>
        <el-button type="primary" :icon="Refresh" @click="loadTimeline" :disabled="!personId">刷新</el-button>
      </div>
    </div>

    <!-- 人员基础信息卡片 -->
    <el-card v-if="subject" class="mb-16" shadow="never">
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="姓名">{{ subject.name }}</el-descriptions-item>
        <el-descriptions-item label="部门">{{ subject.department || '-' }}</el-descriptions-item>
        <el-descriptions-item label="角色">{{ subject.role || '-' }}</el-descriptions-item>
        <el-descriptions-item label="记录总数">{{ total }}</el-descriptions-item>
        <el-descriptions-item label="归并别名" :span="4">
          <el-tag
            v-for="a in subject.aliases || []"
            :key="a"
            size="small"
            effect="plain"
            style="margin-right: 4px; margin-bottom: 4px"
          >
            {{ a }}
          </el-tag>
          <span v-if="!(subject.aliases || []).length">-</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 空状态 -->
    <el-empty v-if="!personId" description="请选择人员查看审计时序" />

    <!-- 时序流 + 明细表双视图 -->
    <AuditTimeline
      v-else
      :items="items"
      :type-meta="typeMeta"
      :loading="loading"
    />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import AuditTimeline from '@/components/AuditTimeline.vue'
import { getPersonTimeline } from '@/api/audit'
import { listPersons } from '@/api/person'

const route = useRoute()
const personId = ref(null)
const days = ref(90)
const limit = ref(100)
const items = ref([])
const typeMeta = ref({})
const subject = ref(null)
const total = ref(0)
const loading = ref(false)

// 人员搜索下拉
const personOptions = ref([])
const searching = ref(false)

async function searchPersons(query) {
  if (!query) {
    personOptions.value = []
    return
  }
  searching.value = true
  try {
    const res = await listPersons({ keyword: query, page: 1, page_size: 30 })
    personOptions.value = res.data?.items || []
  } finally {
    searching.value = false
  }
}

function onPersonChange(val) {
  if (val) loadTimeline()
}

async function loadTimeline() {
  if (!personId.value) return
  loading.value = true
  try {
    const res = await getPersonTimeline(personId.value, { days: days.value, limit: limit.value })
    const data = res.data || {}
    items.value = data.items || []
    typeMeta.value = data.type_meta || {}
    subject.value = data.subject || null
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  // 支持从 query 预填 person_id(从人员管理页跳转)
  const q = route.query.person_id
  if (q) {
    personId.value = Number(q)
    personOptions.value = [{ id: Number(q), display_name: route.query.name || `人员 #${q}` }]
    loadTimeline()
  }
})
</script>

<style scoped>
.flex {
  display: flex;
}
</style>
