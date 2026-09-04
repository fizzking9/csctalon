<template>
  <el-drawer
    v-model="visible"
    direction="rtl"
    :size="isMobile ? '92%' : '620px'"
    class="assistant-drawer"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <template #header>
      <div class="ai-header">
        <span class="ai-title">
          <el-icon style="margin-right: 6px; vertical-align: -2px"><MagicStick /></el-icon>
          经营数据 AI 助手
        </span>
        <el-tooltip content="数据基于内部聚合(与对外简报同口径); 输出由 AI 生成, 仅供参考" placement="bottom">
          <el-icon style="color:#909399;cursor:pointer"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
    </template>

    <!-- 未启用 -->
    <el-result v-if="cfgLoaded && !cfg.enabled" icon="warning" title="AI 助手未启用">
      <template #sub-title>
        <p class="dim-text">需在服务端 .env 设置 <code>LLM_ENABLED=true</code> 并配置 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL。</p>
      </template>
    </el-result>

    <template v-else>
      <!-- 快捷问 -->
      <div v-if="quickQuestions.length" class="ai-quick">
        <div class="ai-quick-label">快捷询问</div>
        <el-tag
          v-for="(q, i) in quickQuestions"
          :key="i"
          class="ai-quick-tag"
          effect="plain"
          @click="ask(q)"
        >{{ q }}</el-tag>
      </div>

      <!-- 会话区 -->
      <div ref="threadEl" class="ai-thread">
        <div v-if="!messages.length" class="ai-empty dim-text">
          输入问题(如“今日退款和涉诉怎么样”“昨日投诉分析”), 或将提供内部经营聚合数据由 AI 分析。
        </div>
        <div
          v-for="(m, i) in messages"
          :key="i"
          class="ai-msg"
          :class="m.role"
        >
          <div class="ai-msg-role">{{ m.role === 'user' ? '我' : 'AI' }}</div>
          <div class="ai-msg-body" :class="{ 'ai-loading': m.loading }">
            <!-- eslint-disable-next-line vue/no-v-html -- AI 输出经 renderMarkdown(DOMPurify 白名单) 消毒, 禁裸 v-html -->
            <div v-if="m.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(m.content)"></div>
            <div v-else>{{ m.content }}</div>
            <el-icon v-if="m.loading" class="is-loading" style="color:#2563eb"><Loading /></el-icon>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="ai-input-bar">
        <el-input
          v-model="question"
          type="textarea"
          :rows="2"
          resize="none"
          placeholder="输入经营数据问题…"
          maxlength="500"
          show-word-limit
          @keydown.enter.exact.prevent="submit"
        />
        <div class="ai-input-actions">
          <el-select v-model="period" size="small" style="width: 130px">
            <el-option label="当日" value="daily" />
            <el-option label="本周" value="weekly" />
            <el-option label="本月" value="monthly" />
          </el-select>
          <el-button type="primary" size="small" :loading="submitting" :disabled="!question.trim()" @click="submit">
            分析
          </el-button>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { MagicStick, QuestionFilled, Loading } from '@element-plus/icons-vue'
import { useAssistant } from '@/composables/useAssistant'
import { aiConfig, aiAnalyze, aiTaskStatus } from '@/api/aiAssistant'
import { renderMarkdown } from '@/utils/sanitize'

const { state, closeAssistant } = useAssistant()

const visible = computed({
  get: () => state.visible,
  set: (v) => {
    if (!v) closeAssistant()
  },
})

const isMobile = ref(false)
let _mq = null
function _onMediaChange(e) { isMobile.value = e.matches }
onMounted(() => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
  loadConfig()
})
onBeforeUnmount(() => { _mq?.removeEventListener('change', _onMediaChange) })

// 配置
const cfgLoaded = ref(false)
const cfg = ref({ enabled: false, periods: [], notice: '' })
const quickQuestions = ref([])
async function loadConfig() {
  try {
    const res = await aiConfig()
    cfg.value = res.data || {}
    const periods = cfg.value?.periods || []
    const qq = (periods.find(p => p.quick_questions)?.quick_questions) || []
    quickQuestions.value = qq
  } catch (e) { /* 拦截器已提示 */ } finally {
    cfgLoaded.value = true
  }
}

// 会话
const messages = ref([])
const question = ref('')
const period = ref('daily')
const submitting = ref(false)
const threadEl = ref(null)
let _pollTimer = null

function scrollBottom() {
  nextTick(() => {
    if (threadEl.value) threadEl.value.scrollTop = threadEl.value.scrollHeight
  })
}

function ask(q) {
  question.value = q
  submit()
}

async function submit() {
  const q = (question.value || '').trim()
  if (!q) return
  if (submitting.value) return
  submitting.value = true
  messages.value.push({ role: 'user', content: q })
  const aiMsg = { role: 'assistant', content: '', loading: true }
  messages.value.push(aiMsg)
  question.value = ''
  scrollBottom()
  try {
    const res = await aiAnalyze({ question: q, period: period.value })
    const d = res.data || {}
    // 后端 analyze 已同步等待: status=success 直接带 result_md(无需轮询); 否则轮询
    if (d.status === 'success') {
      aiMsg.loading = false
      aiMsg.content = d.result_md || '(无输出)'
      scrollBottom()
    } else if (d.status === 'failed') {
      aiMsg.loading = false
      aiMsg.content = '⚠️ 分析失败: ' + (d.error || '未知错误')
      scrollBottom()
    } else if (d.task_id) {
      await pollTask(d.task_id, aiMsg)
    } else {
      throw new Error('未返回 task_id')
    }
  } catch (e) {
    aiMsg.loading = false
    aiMsg.content = '⚠️ 提交失败: ' + (e?.message || e || '未知错误')
    scrollBottom()
  } finally {
    submitting.value = false
  }
}

async function pollTask(taskId, aiMsg) {
  const deadline = Date.now() + 180000 // 3 分钟兜底
  try {
    for (;;) {
      const res = await aiTaskStatus(taskId)
      const t = res.data || {}
      if (t.status === 'success') {
        aiMsg.loading = false
        aiMsg.content = t.result_md || '(无输出)'
        scrollBottom()
        return
      }
      if (t.status === 'failed') {
        aiMsg.loading = false
        aiMsg.content = '⚠️ 分析失败: ' + (t.error || '未知错误')
        scrollBottom()
        return
      }
      if (Date.now() > deadline) {
        aiMsg.loading = false
        aiMsg.content = '⏱️ 分析超时, 请稍后重试'
        scrollBottom()
        return
      }
      await sleep(1000)
    }
  } finally {
    clearTimer()
  }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
function clearTimer() {
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null }
}
</script>

<style scoped>
.ai-header { display: flex; align-items: center; gap: 6px; }
.ai-title { font-size: 16px; font-weight: 600; }
.ai-quick { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.ai-quick-label { width: 100%; font-size: 12px; color: #909399; }
.ai-quick-tag { cursor: pointer; max-width: 100%; white-space: normal; height: auto; line-height: 1.5; padding: 2px 8px; }
.ai-thread {
  height: calc(100vh - 320px);
  min-height: 260px;
  overflow-y: auto;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 10px;
  background: #fafafa;
}
.ai-msg { display: flex; gap: 8px; margin-bottom: 10px; }
.ai-msg.user { flex-direction: row-reverse; }
.ai-msg-role {
  flex-shrink: 0; width: 24px; height: 24px; border-radius: 50%;
  background: #eef2ff; color: #2563eb; font-size: 12px;
  display: flex; align-items: center; justify-content: center;
}
.ai-msg.user .ai-msg-role { background: #ecfdf5; color: #16a34a; }
.ai-msg-body {
  max-width: 82%; padding: 8px 12px; border-radius: 8px;
  background: #fff; border: 1px solid #eee; font-size: 13px; line-height: 1.6;
  white-space: normal; word-break: break-word;
}
.ai-msg.user .ai-msg-body { background: #f0f9ff; }
.ai-msg-body.ai-loading { color: #9ca3af; }
.ai-empty { text-align: center; padding: 40px 10px; font-size: 13px; }
.ai-input-bar { display: flex; flex-direction: column; gap: 6px; }
.ai-input-actions { display: flex; justify-content: flex-end; gap: 8px; align-items: center; }
.dim-text { color: #909399; font-size: 13px; }

/* AI 输出 Markdown 排版(与策略页一致; scoped, 不引全局) */
.markdown-body {
  color: #303133;
  font-size: 13px;
  line-height: 1.8;
  word-break: break-word;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 14px 0 8px;
  font-weight: 600;
  color: #1f2d3d;
}
.markdown-body :deep(h1) { font-size: 18px; }
.markdown-body :deep(h2) { font-size: 16px; }
.markdown-body :deep(h3) { font-size: 14px; }
.markdown-body :deep(p) { margin: 6px 0; }
.markdown-body :deep(ul),
.markdown-body :deep(ol) { padding-left: 22px; margin: 6px 0; }
.markdown-body :deep(li) { margin: 3px 0; }
.markdown-body :deep(code) {
  background: #f5f7fa;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
  color: #c7254e;
}
.markdown-body :deep(pre) {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
  overflow-x: auto;
}
.markdown-body :deep(pre code) { background: none; color: #303133; padding: 0; }
.markdown-body :deep(blockquote) {
  border-left: 4px solid #dcdfe6;
  padding-left: 10px;
  margin: 8px 0;
  color: #606266;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 5px 9px;
  text-align: left;
  font-size: 12px;
}
.markdown-body :deep(th) { background: #f5f7fa; font-weight: 600; }
.markdown-body :deep(a) { color: #409eff; text-decoration: none; }

</style>
