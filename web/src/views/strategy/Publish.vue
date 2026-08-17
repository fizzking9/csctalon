<template>
  <div class="publish-page">
    <!-- 顶部概览 -->
    <div class="page-container publish-header">
      <div class="flex-between">
        <div>
          <div class="page-title" style="margin: 0">策略发布</div>
          <div class="page-sub">当前在执行的策略与规范,点击文档查看完整内容</div>
        </div>
        <div class="stat-cards">
          <div class="stat-card">
            <div class="stat-num">{{ stats.total }}</div>
            <div class="stat-label">在执行文档</div>
          </div>
          <div class="stat-card" :class="{ 'is-unread': stats.unread > 0 }">
            <div class="stat-num">{{ stats.unread }}</div>
            <div class="stat-label">未读</div>
          </div>
          <div class="stat-card">
            <div class="stat-num">{{ stats.read }}</div>
            <div class="stat-label">已读</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 筛选条 -->
    <div class="page-container" style="padding-top: 0">
      <div class="filter-bar flex gap-8" style="flex-wrap: wrap; align-items: center">
        <el-radio-group v-model="filter.category" @change="loadData">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button v-for="c in categories" :key="c" :label="c">{{ c }}</el-radio-button>
        </el-radio-group>
        <el-input
          v-model="filter.keyword"
          placeholder="搜索标题"
          clearable
          style="width: 200px"
          @keyup.enter="loadData"
          @clear="loadData"
        />
        <el-checkbox v-model="filter.onlyUnread" @change="applyFilter">仅看未读</el-checkbox>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </div>

    <!-- 主体:左列表 + 右详情 -->
    <div class="page-container publish-body" v-loading="loading">
      <div class="doc-list">
        <div v-if="!filteredList.length && !loading" class="empty-tip">
          <el-empty description="暂无已发布文档" />
        </div>
        <div
          v-for="d in filteredList"
          :key="d.id"
          class="doc-item"
          :class="{ active: selectedId === d.id, unread: !d.has_read }"
          @click="selectDoc(d)"
        >
          <div class="doc-item-title">
            <span class="unread-dot" v-if="!d.has_read" />
            {{ d.title }}
          </div>
          <div class="doc-item-meta">
            <el-tag size="small" effect="plain">{{ d.category || '通用' }}</el-tag>
            <span class="meta-text">v{{ d.version }}</span>
            <span class="meta-text">{{ formatTime(d.published_at) }}</span>
          </div>
          <div class="doc-item-status">
            <el-tag v-if="d.has_read" size="small" type="success" effect="plain">已读</el-tag>
            <el-tag v-else size="small" type="warning" effect="plain">未读</el-tag>
          </div>
        </div>
      </div>

      <div class="doc-detail">
        <div v-if="!current" class="empty-tip">
          <el-empty description="选择左侧文档查看内容" />
        </div>
        <template v-else>
          <div class="detail-header">
            <div class="detail-title">{{ current.title }}</div>
            <div class="detail-meta">
              <el-tag size="small">{{ current.category || '通用' }}</el-tag>
              <span>版本 v{{ current.version }}</span>
              <span>发布人 {{ current.published_by || '-' }}</span>
              <span>发布于 {{ formatTime(current.published_at) }}</span>
            </div>
          </div>
          <el-divider />
          <div class="markdown-body" v-html="renderedContent" />
          <el-divider />
          <div class="detail-footer">
            <el-tag v-if="current.has_read" type="success" :icon="Check">已确认知晓 ({{ formatTime(current.read_at) }})</el-tag>
            <el-button v-else type="primary" :loading="marking" @click="handleMarkRead">我已知晓</el-button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Check } from '@element-plus/icons-vue'
import { renderMarkdown } from '@/utils/sanitize'
import { listStrategy, getStrategy, markRead } from '@/api/strategy'
import dayjs from 'dayjs'

const list = ref([])
const loading = ref(false)
const selectedId = ref(null)
const current = ref(null)
const marking = ref(false)
const filter = reactive({ category: '', keyword: '', onlyUnread: false })
const categories = ['退款策略', '清退策略', '服务规范', '通用']

const stats = computed(() => {
  const total = list.value.length
  const unread = list.value.filter((d) => !d.has_read).length
  return { total, unread, read: total - unread }
})

const filteredList = computed(() => {
  let arr = list.value
  if (filter.onlyUnread) arr = arr.filter((d) => !d.has_read)
  if (filter.keyword) {
    const kw = filter.keyword.toLowerCase()
    arr = arr.filter((d) => d.title.toLowerCase().includes(kw))
  }
  return arr
})

const renderedContent = computed(() => {
  if (!current.value?.content) return '<p class="empty-content">该文档无正文内容</p>'
  // P0-5: marked 解析后经 DOMPurify 消毒, 防存储型 XSS(策略正文对所有用户展示)
  return renderMarkdown(current.value.content)
})

const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

async function loadData() {
  loading.value = true
  try {
    // 只拉已发布:后端对客服角色自动只返回 published;管理层传 status=published
    const params = { page: 1, page_size: 100, status: 'published' }
    if (filter.category) params.category = filter.category
    if (filter.keyword) params.keyword = filter.keyword
    const res = await listStrategy(params)
    list.value = res.data.items
    // 默认选中第一个未读,否则第一个
    const firstUnread = list.value.find((d) => !d.has_read)
    const target = firstUnread || list.value[0]
    if (target) selectDoc(target)
    else {
      selectedId.value = null
      current.value = null
    }
  } finally {
    loading.value = false
  }
}

function applyFilter() {
  // 仅前端筛选,无需重新请求
}

async function selectDoc(d) {
  selectedId.value = d.id
  // 拉完整内容
  try {
    const res = await getStrategy(d.id)
    current.value = res.data
    // 后端对客服查看已发布会自动标记已读,同步列表状态
    if (!d.has_read) {
      d.has_read = true
    }
  } catch {
    current.value = d
  }
}

async function handleMarkRead() {
  if (!current.value) return
  marking.value = true
  try {
    await markRead(current.value.id)
    current.value.has_read = true
    // 同步列表
    const item = list.value.find((d) => d.id === current.value.id)
    if (item) item.has_read = true
    ElMessage.success('已确认')
  } finally {
    marking.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.publish-page {
  min-height: 100%;
}
.publish-header {
  padding-bottom: 12px;
}
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.stat-cards {
  display: flex;
  gap: 12px;
}
.stat-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px 18px;
  text-align: center;
  min-width: 80px;
}
.stat-card.is-unread {
  border-color: #e6a23c;
  background: #fdf6ec;
}
.stat-num {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
}
.stat-card.is-unread .stat-num {
  color: #e6a23c;
}
.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.filter-bar {
  background: #fff;
  padding: 12px 16px;
  border-radius: 4px;
  border: 1px solid #ebeef5;
}
.publish-body {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  margin-top: 12px;
}
.doc-list {
  width: 320px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  max-height: calc(100vh - 240px);
  overflow-y: auto;
}
.doc-item {
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  position: relative;
  transition: background 0.15s;
}
.doc-item:hover {
  background: #f5f7fa;
}
.doc-item.active {
  background: #ecf5ff;
  border-left: 3px solid #409eff;
  padding-left: 13px;
}
.doc-item.unread {
  background: #fdf6ec;
}
.doc-item.unread.active {
  background: #ecf5ff;
}
.doc-item-title {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.unread-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e6a23c;
  display: inline-block;
  flex-shrink: 0;
}
.doc-item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #909399;
}
.meta-text {
  font-size: 12px;
}
.doc-item-status {
  position: absolute;
  top: 12px;
  right: 16px;
}
.doc-detail {
  flex: 1;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 20px 28px;
  min-height: 400px;
  max-height: calc(100vh - 240px);
  overflow-y: auto;
}
.empty-tip {
  padding: 60px 0;
  text-align: center;
}
.detail-title {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}
.detail-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 13px;
  color: #909399;
}
.detail-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 8px;
}
.empty-content {
  color: #c0c4cc;
}

/* Markdown 正文样式 */
.markdown-body {
  color: #303133;
  font-size: 14px;
  line-height: 1.8;
  word-break: break-word;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 18px 0 10px;
  font-weight: 600;
  color: #1f2d3d;
}
.markdown-body :deep(h1) {
  font-size: 20px;
}
.markdown-body :deep(h2) {
  font-size: 17px;
}
.markdown-body :deep(h3) {
  font-size: 15px;
}
.markdown-body :deep(p) {
  margin: 8px 0;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 24px;
  margin: 8px 0;
}
.markdown-body :deep(li) {
  margin: 4px 0;
}
.markdown-body :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 13px;
  color: #c7254e;
}
.markdown-body :deep(pre) {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  overflow-x: auto;
}
.markdown-body :deep(pre code) {
  background: none;
  color: #303133;
  padding: 0;
}
.markdown-body :deep(blockquote) {
  border-left: 4px solid #dcdfe6;
  padding-left: 12px;
  margin: 10px 0;
  color: #606266;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 10px 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 6px 10px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}
.markdown-body :deep(a) {
  color: #409eff;
  text-decoration: none;
}
.markdown-body :deep(a:hover) {
  text-decoration: underline;
}

@media (max-width: 900px) {
  .publish-body {
    flex-direction: column;
  }
  .doc-list {
    width: 100%;
    max-height: 280px;
  }
}
</style>
