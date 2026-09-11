<!--
  ┌─────────────────────── 设计说明（Design Rationale） ───────────────────────┐
  │ 目的：客服主管 / 运营负责人 在晨会或临时追问时, 用一句话问出经营口径答案    │
  │       (如"昨天的退款怎么样"), 不必自己拼看板筛选条件; 频率=每日多次。      │
  │                                                                          │
  │ 逻辑：① 时间窗是这类问答的第一歧义源, 故把「窗口」做成显式可见元素:        │
  │         周期下拉(自动识别/当日/本周/本月/指定区间) + 每条回答顶部的窗口    │
  │         回显条 —— 先让用户确认"问的是哪个窗口", 再看数字。                │
  │       ② 生成需要数秒, 故用「进度可见 + 结果不丢」替代"转圈等":            │
  │         生成中显示已等待秒数; 关抽屉/刷新后从 sessionStorage 续查。       │
  │                                                                          │
  │ 口径：数据取自内部聚合 feed, 与「对外简报 /brief」同底层同缓存            │
  │       (ai_feed._build_feed = external._build_brief), 金额单位=元。       │
  │       口径卡随问句注入, 见 app/services/ai_glossary.py;                  │
  │       窗口判定规则见 app/services/ai_time_parser.py。                    │
  │                                                                          │
  │ 取舍：不做「多轮上下文记忆」—— 每次提问独立取数, 避免模型跨轮次引用旧窗口   │
  │       导致口径漂移(想要连续追问请把条件写进同一句)。                      │
  │       不做「流式逐字输出」—— 后端为非流式 chat/completions, 改为进度提示  │
  │       已能满足"别让我干等"的诉求, 引入 SSE 会使单 worker 连接更紧张。     │
  │       不做「历史会话列表/收藏」—— 一期定位是即时问答, 持久化只保留当前      │
  │       标签页(登出即清), 避免经营数据长期滞留浏览器。                      │
  │                                                                          │
  │ 工单：#66 · 设计依据：docs/standards/界面设计说明规范.md                  │
  └──────────────────────────────────────────────────────────────────────────┘
-->
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
        <!-- 页面层设计说明(界面设计说明规范 §2.2 方案 A): 必含「取舍」段 + 口径出处 -->
        <el-popover placement="bottom-start" :width="420" trigger="click">
          <template #reference>
            <el-icon style="color:#909399;cursor:pointer"><QuestionFilled /></el-icon>
          </template>
          <div class="ai-rationale">
            <p><b>目的</b>：用一句话问出经营口径答案（如“昨天的退款怎么样”），
              不必自己拼看板的筛选条件。</p>
            <p><b>逻辑</b>：时间窗是这类问答的第一歧义源，所以每条回答顶部都会显示
              「本次按哪个窗口统计」，请先核对窗口再看数字。生成需要几秒，
              期间会显示已等待时间；中途关掉面板也不会丢结果，重新打开会接着查。</p>
            <p><b>口径</b>：数据与「对外简报」同源同缓存，金额单位为元。
              指标口径见 <code>docs/standards/数据时间字段使用规范.md</code>。</p>
            <p><b>取舍</b>：<br>
              · 不做多轮上下文记忆 —— 每次提问独立取数，避免跨轮次沿用旧窗口导致口径漂移；
              需要连续条件请写在同一句里。<br>
              · 不做流式逐字输出 —— 改用进度提示即可满足“别让我干等”，
              引入流式会加重单 worker 的并发压力。<br>
              · 不做历史会话收藏 —— 一期定位是即时问答，会话只留在当前标签页，登出即清，
              避免经营数据长期滞留浏览器。</p>
            <p class="dim-text">工单 #66 · 详见
              <code>docs/standards/界面设计说明规范.md</code></p>
          </div>
        </el-popover>
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
          输入问题(如“今日退款和涉诉怎么样”“昨天的运营数据”“这两天的对比”),
          也可问“近7天”“上周”“8月1日到8月5日”, 时间会自动识别。
        </div>
        <div
          v-for="(m, i) in messages"
          :key="i"
          class="ai-msg"
          :class="m.role"
        >
          <div class="ai-msg-role">{{ m.role === 'user' ? '我' : 'AI' }}</div>
          <div class="ai-msg-body" :class="{ 'ai-loading': m.loading }">
            <!-- 窗口回显: 让"按哪个窗口统计"始终可见, 识别错了能立刻发现 (#66) -->
            <div v-if="m.role === 'assistant' && m.windowLabel" class="ai-window-hint">
              <el-icon><Calendar /></el-icon>
              <span>{{ m.windowLabel }}</span>
            </div>
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
          placeholder="输入经营数据问题…(可直接说“昨天的数据”“近7天”“上周”)"
          maxlength="500"
          show-word-limit
          @keydown.enter.exact.prevent="submit"
        />
        <div class="ai-input-actions">
          <el-select v-model="periodMode" size="small" style="width: 120px">
            <el-option label="自动识别" value="auto" />
            <el-option label="当日" value="daily" />
            <el-option label="本周" value="weekly" />
            <el-option label="本月" value="monthly" />
            <el-option label="指定区间" value="range" />
          </el-select>
          <!-- 指定区间: 显式窗口优先于问句自动识别 (#66) -->
          <el-date-picker
            v-if="periodMode === 'range'"
            v-model="rangeDates"
            type="daterange"
            size="small"
            value-format="YYYY-MM-DD"
            start-placeholder="起"
            end-placeholder="止"
            :disabled-date="disableFuture"
            style="width: 230px"
          />
          <el-button type="primary" size="small" :loading="submitting" :disabled="!question.trim()" @click="submit">
            分析
          </el-button>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { MagicStick, QuestionFilled, Loading, Calendar } from '@element-plus/icons-vue'
import { useAssistant } from '@/composables/useAssistant'
import { AI_THREAD_KEY, AI_PENDING_KEY } from '@/constants/aiAssistantStorage'
import { aiConfig, aiAnalyze, aiTaskStatus } from '@/api/aiAssistant'
import { renderMarkdown } from '@/utils/sanitize'

const { state, closeAssistant } = useAssistant()

// 在途轮询的中止开关: 抽屉关闭/组件卸载时置 true, 停止后续请求
let _cancelled = false

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
onBeforeUnmount(() => {
  _mq?.removeEventListener('change', _onMediaChange)
  _cancelled = true
})

// 会话恢复/续查挂在「抽屉打开」而非「组件挂载」上 (#66):
// 组件在应用启动时就已挂载, 而用户可能很久之后才打开助手; 且 el-drawer 关闭时
// 只是销毁内部 DOM, 不会重新执行 setup —— 所以必须在每次打开时主动恢复。
// 关闭时置 _cancelled 停止在途轮询(省流量/省后端), 未完成任务 id 留在
// sessionStorage 里, 下次打开自动续查, 结果不会丢。
watch(visible, (v) => {
  if (v) {
    _cancelled = false
    restoreThread()
  } else {
    _cancelled = true
  }
})

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
// 持久化 (#66): el-drawer 带 destroy-on-close, 且用户可能刷新/切页 ——
// 原先答案只存内存, 轮询一断就永久丢失且无任何提示。
// 现在把「消息 + 未完成任务 id」落到 sessionStorage, 重开/刷新后自动续查。
// key 收敛在 @/constants/aiAssistantStorage: auth.js 登出时也要按同一份 key 清理,
// 两处硬编码字符串会静默漂移导致"登出不清"或"续查失效"。
const THREAD_KEY = AI_THREAD_KEY
const PENDING_KEY = AI_PENDING_KEY

const messages = ref([])
const question = ref('')
const periodMode = ref('auto')   // auto / daily / weekly / monthly / range
const rangeDates = ref(null)
const submitting = ref(false)
const threadEl = ref(null)

const POLL_INTERVAL_MS = 1200
const POLL_DEADLINE_MS = 180000
const MAX_POLL_ERRORS = 5

// 日期选择器: 禁选**明天及以后**(后端护栏也会 400, 这里提前拦截提升体验)。
// ⚠️ 不能写成 `d > Date.now() - 86400000`: 那会把「今天 00:00」也判成未来
//   (今天 00:00 的时间戳确实 > 现在-24h), 导致今天/昨天不可选。
//   正解: 与今天的 00:00 比较, 只禁严格晚于今天的日期。
function disableFuture(d) {
  const today0 = new Date()
  today0.setHours(0, 0, 0, 0)
  return d.getTime() > today0.getTime()
}

function _saveThread() {
  try {
    // loading 状态不落盘: 重开时由 pendingTaskId 决定是否恢复"生成中"
    sessionStorage.setItem(THREAD_KEY, JSON.stringify(messages.value.map(m => ({ ...m, loading: false }))))
  } catch { /* 无痕模式/配额满: 静默降级, 不阻塞对话 */ }
}
function _savePending(taskId) {
  try {
    if (taskId) sessionStorage.setItem(PENDING_KEY, taskId)
    else sessionStorage.removeItem(PENDING_KEY)
  } catch { /* 同上 */ }
}

function scrollBottom() {
  nextTick(() => {
    if (threadEl.value) threadEl.value.scrollTop = threadEl.value.scrollHeight
  })
}

function ask(q) {
  question.value = q
  submit()
}

/** 组装入参: 显式区间 > 指定周期 > 自动(交给后端按问句识别时间) */
function buildPayload(q) {
  if (periodMode.value === 'range') {
    const [s, e] = rangeDates.value || []
    if (s && e) return { question: q, period: 'daily', start: s, end: e }
    // 未选满区间 → 退回自动识别, 避免发一个半截参数被后端 400
    return { question: q, period: 'daily' }
  }
  if (periodMode.value === 'auto') return { question: q, period: 'daily' }
  return { question: q, period: periodMode.value }
}

/** 回显窗口来源, 让"按哪个窗口统计"始终可见 */
function applyWindow(msg, d) {
  if (!d) return
  if (d.window_label) {
    const src = { nl: '自动识别', explicit_range: '指定区间', explicit_date: '指定日期', period: '默认周期' }[d.window_source]
    msg.windowLabel = src ? `${src}: ${d.window_label}` : d.window_label
  } else if (d.start || d.end) {
    msg.windowLabel = `${d.start || ''} ~ ${d.end || ''}`
  }
}

async function submit() {
  const q = (question.value || '').trim()
  if (!q) return
  if (submitting.value) return
  submitting.value = true
  messages.value.push({ role: 'user', content: q })
  const aiMsg = { role: 'assistant', content: '', loading: true, windowLabel: '' }
  messages.value.push(aiMsg)
  question.value = ''
  _saveThread()
  scrollBottom()
  try {
    const res = await aiAnalyze(buildPayload(q))
    const d = res.data || {}
    applyWindow(aiMsg, d)
    // 后端 analyze 已同步等待: status=success 直接带 result_md(无需轮询); 否则轮询
    if (d.status === 'success') {
      finishMsg(aiMsg, d.result_md || '(无输出)')
    } else if (d.status === 'failed') {
      finishMsg(aiMsg, '⚠️ 分析失败: ' + (d.error || '未返回错误详情, 请查看服务端日志'))
    } else if (d.task_id) {
      // running 兜底: 显式告诉用户"仍在生成", 不要让他对着空白等
      aiMsg.content = '⏳ 数据已取到, 模型正在生成分析…'
      _savePending(d.task_id)
      await pollTask(d.task_id, aiMsg)
    } else {
      throw new Error('未返回 task_id')
    }
  } catch (e) {
    finishMsg(aiMsg, '⚠️ 提交失败: ' + (e?.message || e || '未知错误'))
  } finally {
    submitting.value = false
    _saveThread()
  }
}

function finishMsg(aiMsg, content) {
  aiMsg.loading = false
  aiMsg.content = content
  scrollBottom()
  _saveThread()
}

/**
 * 轮询至终态 (#66 核心修复)。
 *
 * 原实现 `for(;;){ await ... }` 无错误处理: 任一次请求异常(网络抖动/登录过期)
 * 都会直接抛出, 前端只显示"提交失败"、任务结果永久丢失 —— 生产 20 次轮询
 * 全部停在 running 且用户"毫无提示", 正是这条路径。
 * 现在: 单次失败重试(最多 MAX_POLL_ERRORS 次), 期间持续显示进度,
 * 且 task_id 已落 sessionStorage, 重开抽屉可续查。
 */
async function pollTask(taskId, aiMsg, opts = {}) {
  const deadline = Date.now() + (opts.deadlineMs || POLL_DEADLINE_MS)
  let consecutiveErrors = 0
  for (;;) {
    if (_cancelled) return
    let t = null
    try {
      const res = await aiTaskStatus(taskId)
      t = res.data || {}
      consecutiveErrors = 0
    } catch (e) {
      consecutiveErrors += 1
      if (consecutiveErrors >= MAX_POLL_ERRORS) {
        // 明确告知"任务可能已出结果", 并保留 task_id 供续查 —— 不静默吞掉
        _savePending(taskId)
        finishMsg(aiMsg, `⚠️ 连续 ${MAX_POLL_ERRORS} 次查询结果失败(网络或登录状态异常)。\n\n` +
          '分析可能仍在后台进行, 请稍后重新打开助手查看。')
        return
      }
      aiMsg.content = `⏳ 仍在生成中…(查询结果暂时失败, 正在重试 ${consecutiveErrors}/${MAX_POLL_ERRORS})`
      await sleep(POLL_INTERVAL_MS * 2)
      continue
    }

    applyWindow(aiMsg, t)
    if (t.status === 'success') {
      _savePending(null)
      finishMsg(aiMsg, t.result_md || '(无输出)')
      return
    }
    if (t.status === 'failed') {
      _savePending(null)
      finishMsg(aiMsg, '⚠️ 分析失败: ' + (t.error || '未返回错误详情, 请查看服务端日志'))
      return
    }
    if (Date.now() > deadline) {
      // 超时不丢结果: 保留 pending, 让用户重开时继续查
      _savePending(taskId)
      finishMsg(aiMsg, '⏱️ 本次分析耗时较长, 已转入后台继续生成。\n\n请稍后重新打开助手查看结果。')
      return
    }
    // running / queued: 显示进度, 让"没提示"不再发生
    const waited = Math.floor((Date.now() - (deadline - (opts.deadlineMs || POLL_DEADLINE_MS))) / 1000)
    aiMsg.content = `⏳ 模型正在生成分析… (已等待 ${waited}s)`
    await sleep(POLL_INTERVAL_MS)
  }
}

/** 重开抽屉/刷新后恢复会话, 若有未完成任务则续查 */
function restoreThread() {
  try {
    const raw = sessionStorage.getItem(THREAD_KEY)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr)) messages.value = arr
    }
  } catch { /* 解析失败: 忽略, 从空会话开始 */ }
  let pending = null
  try { pending = sessionStorage.getItem(PENDING_KEY) } catch { /* 同上 */ }
  if (!pending) { scrollBottom(); return }
  // 找到最后一条 assistant 消息承载"生成中"状态; 没有则补一条
  let target = null
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'assistant') { target = messages.value[i]; break }
  }
  if (!target) {
    target = { role: 'assistant', content: '', loading: true, windowLabel: '' }
    messages.value.push(target)
  }
  target.loading = true
  target.content = '⏳ 正在恢复上次的分析进度…'
  scrollBottom()
  pollTask(pending, target)
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
</script>

<style scoped>
.ai-header { display: flex; align-items: center; gap: 6px; }
.ai-title { font-size: 16px; font-weight: 600; }
/* 设计说明气泡内容(页面层 ⓘ) */
.ai-rationale { font-size: 12px; line-height: 1.7; color: #303133; }
.ai-rationale p { margin: 0 0 8px; }
.ai-rationale p:last-child { margin-bottom: 0; }
.ai-rationale code { background: #f5f7fa; padding: 1px 4px; border-radius: 3px; font-size: 11px; }
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
/* 窗口回显条: 让"按哪个窗口统计"始终可见 (#66) */
.ai-window-hint {
  display: flex; align-items: center; gap: 4px;
  font-size: 12px; color: #2563eb; background: #eff6ff;
  border-radius: 4px; padding: 2px 6px; margin-bottom: 6px;
  word-break: break-all;
}
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
