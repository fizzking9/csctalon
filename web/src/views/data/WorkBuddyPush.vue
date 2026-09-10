<template>
  <div class="push-page">
    <el-alert type="info" :closable="false" show-icon class="mb-16">
      被动推送模式：WorkBuddy 或其他外部工具定时检查共享文件，内容变化时按 JSON 推送到本系统，系统自动清洗/去重/关联入库。
      适合<strong>不需要系统主动联网</strong>、由外部工具（如 WorkBuddy）负责监测的场景。
    </el-alert>

    <!-- 接入信息 -->
    <el-card shadow="never" class="mb-16">
      <template #header>
        <div class="card-header">
          <span>推送接口信息</span>
          <el-tag v-if="config.token_configured" type="success" size="small" effect="plain">Token 已配置</el-tag>
          <el-tag v-else type="danger" size="small" effect="plain">Token 未配置(推送将返回 503)</el-tag>
        </div>
      </template>
      <el-descriptions :column="1" size="small" border>
        <el-descriptions-item label="推送地址(HTTP POST)">
          <code class="code-text">{{ pushUrl }}</code>
        </el-descriptions-item>
        <el-descriptions-item label="鉴权方式">
          <code class="code-text">Authorization: Bearer &lt;WORKBUDDY_SYNC_TOKEN&gt;</code>
          <span class="hint-text">(或请求头 X-Sync-Token)</span>
        </el-descriptions-item>
        <el-descriptions-item label="请求体">
          <code class="code-text">{"source_file": "文件名.xlsx", "external_complaints": [...], "card_complaints": [...]}</code>
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="!config.token_configured"
        type="warning"
        :closable="false"
        show-icon
        class="mt-12"
        title="管理员需在服务器 .env 中配置 WORKBUDDY_SYNC_TOKEN(openssl rand -hex 32)后重启后端, 并告知外部工具携带该 token 推送"
      />
    </el-card>

    <!-- 推送日志 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>推送日志(最近 15 条)</span>
          <el-button text type="primary" size="small" @click="loadLogs">刷新</el-button>
        </div>
      </template>
      <el-table :data="logs" size="small" border stripe empty-text="暂无推送记录">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="来源" width="120" prop="sync_source" />
        <el-table-column label="文件" prop="source_file" min-width="180" show-overflow-tooltip />
        <el-table-column label="315投诉" width="140">
          <template #default="{ row }">{{ stat(row.external) }}</template>
        </el-table-column>
        <el-table-column label="卡商投诉" width="140">
          <template #default="{ row }">{{ stat(row.card) }}</template>
        </el-table-column>
        <!-- #42 abc-c: 拒收行与入库前校验(校验告警≠失败, 故单独列出) -->
        <el-table-column label="拒收/校验" width="130">
          <template #default="{ row }">
            <div class="reject-cell">
              <el-tooltip
                v-if="rejectedTag(row.rejected).show"
                :content="rejectedTag(row.rejected).tip"
                placement="top"
              >
                <el-tag type="danger" size="small" effect="plain">
                  {{ rejectedTag(row.rejected).text }}
                </el-tag>
              </el-tooltip>
              <el-tooltip
                v-if="validationTag(row.validation_count, sevBlock(row), sevWarn(row)).show"
                :content="validationTag(row.validation_count, sevBlock(row), sevWarn(row)).tip"
                placement="top"
              >
                <el-tag type="warning" size="small" effect="plain">
                  {{ validationTag(row.validation_count, sevBlock(row), sevWarn(row)).text }}
                </el-tag>
              </el-tooltip>
              <span v-if="showRejectPlaceholder(row.rejected, row.validation_count)" class="dim">—</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : row.status === 'partial' ? 'warning' : 'danger'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getPushConfig, listComplaintSyncLogs } from '@/api/docSync'
import { rejectedTag, validationTag, showRejectPlaceholder } from '@/utils/collectionStats'

const config = ref({ token_configured: false })
const logs = ref([])
const pushUrl = `${window.location.origin}/api/v1/sync/complaints/push`

// #42 abc-c: 该接口的 validation_severity 是 JSON 对象({block,warn})
function sevBlock(row) {
  return row?.validation_severity?.block || 0
}
function sevWarn(row) {
  return row?.validation_severity?.warn || 0
}

function fmtTime(t) {
  if (!t) return ''
  const d = new Date(t)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function stat(s) {
  if (!s) return '-'
  return `${s.total} 行(+${s.inserted}/~${s.updated})`
}

async function loadLogs() {
  try {
    const res = await listComplaintSyncLogs({ page: 1, page_size: 15 })
    logs.value = (res.data?.items || []).filter((l) => l.sync_source === 'workbuddy')
  } catch (e) {
    logs.value = []
  }
}

onMounted(async () => {
  try {
    const res = await getPushConfig()
    config.value = res.data || { token_configured: false }
  } catch (e) {
    /* 静默 */
  }
  await loadLogs()
})
</script>

<style scoped>
.push-page {
  max-width: 960px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
/* #42 abc-c: 拒收/校验列 */
.reject-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.dim {
  color: var(--el-text-color-secondary);
}
.code-text {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  word-break: break-all;
}
.hint-text {
  font-size: 12px;
  color: #909399;
  margin-left: 8px;
}
.mb-16 {
  margin-bottom: 16px;
}
.mt-12 {
  margin-top: 12px;
}
</style>
