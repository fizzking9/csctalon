<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">缓存管理</div>
        <div class="page-sub">
          看板缓存由预热机制(同步后/启动后/空闲期)自动填充 ·
          数据滞后或缓存异常时, 管理员可手动强制重建指定范围
        </div>
      </div>
      <el-button :icon="Refresh" size="small" @click="loadInfo">刷新状态</el-button>
    </div>

    <!-- 状态条 -->
    <el-row :gutter="12" class="mb-16">
      <el-col :xs="12" :sm="8">
        <div class="kpi-card" :style="redisOk ? greenCard : redCard">
          <div class="kpi-label" :style="redisOk ? greenText : redText">Redis 缓存</div>
          <div class="kpi-value">{{ redisOk ? '可用' : '不可用' }}</div>
          <div class="kpi-sub">{{ redisOk ? '缓存正常读写' : '已降级直连 DB' }}</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="8">
        <div class="kpi-card" :style="syncBusy ? amberCard : greenCard">
          <div class="kpi-label" :style="syncBusy ? amberText : greenText">数据同步</div>
          <div class="kpi-value">{{ syncBusy ? '进行中' : '空闲' }}</div>
          <div class="kpi-sub">{{ syncBusy ? '重建会被拒绝, 请稍后' : '可安全重建' }}</div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="8">
        <div class="kpi-card" :style="info.is_warming ? amberCard : greenCard">
          <div class="kpi-label" :style="info.is_warming ? amberText : greenText">预热任务</div>
          <div class="kpi-value">{{ info.is_warming ? '运行中' : '空闲' }}</div>
          <div class="kpi-sub">{{ info.is_warming ? '单飞锁占用, 重建可能被拒' : '无预热在跑' }}</div>
        </div>
      </el-col>
    </el-row>

    <el-alert
      v-if="!redisOk"
      type="warning"
      :closable="false"
      class="mb-16"
      title="Redis 不可用"
      description="重建会重算数据但无法写入缓存, 看板仍走直连 DB(变慢)。请先恢复 Redis 连接。"
      show-icon
    />

    <el-card shadow="never">
      <div class="page-title" style="margin: 0 0 12px">选择重建范围</div>
      <el-radio-group v-model="scope" class="scope-group">
        <el-radio
          v-for="s in scopes"
          :key="s.value"
          :value="s.value"
          border
          class="scope-item"
        >
          <span class="scope-label">{{ s.label }}</span>
        </el-radio>
      </el-radio-group>

      <div style="margin: 16px 0">
        <el-switch v-model="invalidateOnly" active-text="仅失效(下次访问自然回填)" inactive-text="失效 + 强制重算预热" />
        <span class="page-sub" style="margin-left: 12px">
          {{ invalidateOnly ? '清空缓存不重算, 首个访问者会触发回填(可能稍慢)' : '清空后立即重算并写入缓存, 后续访问直接命中' }}
        </span>
      </div>

      <div>
        <el-button
          type="primary"
          :icon="RefreshRight"
          :loading="rebuilding"
          :disabled="syncBusy"
          @click="doRebuild"
        >
          {{ invalidateOnly ? '失效缓存' : '重建缓存' }}
        </el-button>
        <span v-if="syncBusy" class="page-sub" style="margin-left: 12px">
          数据同步进行中, 暂时无法重建
        </span>
      </div>

      <!-- 最近一次结果 -->
      <div v-if="lastResult" class="result-box">
        <div class="page-title" style="margin: 0 0 8px">
          重建结果 · {{ scopeLabel(lastResult.scope) }}
          <el-tag size="small" :type="lastResult.redis_available ? 'success' : 'warning'" effect="plain" style="margin-left: 8px">
            {{ lastResult.redis_available ? 'Redis 已写入' : 'Redis 未写入' }}
          </el-tag>
        </div>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="操作">{{ lastResult.invalidate_only ? '仅失效' : '失效 + 预热' }}</el-descriptions-item>
          <el-descriptions-item label="失效条数">{{ lastResult.invalidated }}</el-descriptions-item>
          <el-descriptions-item label="预热条数">{{ lastResult.warmed }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ lastResult.elapsed_ms }} ms</el-descriptions-item>
          <el-descriptions-item label="范围">{{ lastResult.scope_label || lastResult.scope }}</el-descriptions-item>
          <el-descriptions-item label="Redis">{{ lastResult.redis_available ? '可用' : '不可用' }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { cacheInfo, rebuildCache } from '@/api/cacheAdmin'

const scopes = ref([
  { value: 'today', label: '今日看板 · 今日视图' },
  { value: 'week', label: '今日看板 · 本周视图' },
  { value: 'reports', label: '月报(运营/现金流/退款分类/人员操作)' },
  { value: 'all', label: '全部看板缓存' },
])
const scope = ref('today')
const invalidateOnly = ref(false)
const rebuilding = ref(false)
const lastResult = ref(null)
const info = ref({ redis_available: true, is_warming: false, sync_busy: false })

const redisOk = computed(() => info.value.redis_available)
const syncBusy = computed(() => info.value.sync_busy)

const greenCard = 'background:#f0fdf4'
const greenText = 'color:#16a34a'
const redCard = 'background:#fef2f2'
const redText = 'color:#dc2626'
const amberCard = 'background:#fffbeb'
const amberText = 'color:#d97706'

function scopeLabel(v) {
  return scopes.value.find((s) => s.value === v)?.label || v
}

async function loadInfo() {
  try {
    const res = await cacheInfo()
    const d = res.data || res
    if (d?.scopes) scopes.value = d.scopes
    info.value = {
      redis_available: d?.redis_available ?? true,
      is_warming: d?.is_warming ?? false,
      sync_busy: d?.sync_busy ?? false,
    }
  } catch (e) {
    // 拦截器已弹错误
  }
}

async function doRebuild() {
  rebuilding.value = true
  try {
    const res = await rebuildCache(scope.value, invalidateOnly.value)
    const r = res.data || res
    lastResult.value = r
    if (!r?.redis_available) {
      ElMessage.warning('Redis 不可用, 数据已重算但未写入缓存')
    } else if (r?.invalidate_only) {
      ElMessage.success(`已失效 ${r?.invalidated ?? 0} 条缓存`)
    } else {
      ElMessage.success(`重建完成: 失效 ${r?.invalidated ?? 0} / 预热 ${r?.warmed ?? 0} 条, 耗时 ${r?.elapsed_ms ?? 0}ms`)
    }
    loadInfo()
  } catch (e) {
    // 409(同步中/单飞锁)等错误已由拦截器弹出
  } finally {
    rebuilding.value = false
  }
}

onMounted(loadInfo)
</script>

<style scoped>
.scope-group {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.scope-item {
  margin-right: 0;
}
.scope-label {
  font-size: 13px;
}
.result-box {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}
</style>
