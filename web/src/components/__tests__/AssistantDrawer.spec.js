// AI 助手抽屉 - 轮询与窗口回显逻辑测试 (工单 #66)
//
// 背景: 生产事故(2026-09-10)显示 `analyze` 有 36% 请求 6 秒内没跑完, 会返回
// status=running 让前端轮询; 而原实现 `for(;;){await ...}` 无错误处理、
// 答案只存内存 → 用户"毫无提示"且结果静默丢失。
// 本测试锁定修复后的三条不变式:
//   1. 轮询必须追到终态(不能只查一次就停)
//   2. 单次查询失败要重试, 而非直接放弃
//   3. 抽屉关闭/重开后, 未完成的任务要能续查(结果不丢)
//
// 说明: 组件模板依赖 Element Plus, 这里只挂载并驱动其 script 逻辑所需的
// 最小面; 通过 mock '@/api/aiAssistant' 与 '@/utils/sanitize' 隔离外部依赖。

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const aiConfig = vi.fn()
const aiAnalyze = vi.fn()
const aiTaskStatus = vi.fn()

vi.mock('@/api/aiAssistant', () => ({
  aiConfig: (...a) => aiConfig(...a),
  aiAnalyze: (...a) => aiAnalyze(...a),
  aiTaskStatus: (...a) => aiTaskStatus(...a),
}))

vi.mock('@/utils/sanitize', () => ({
  renderMarkdown: (s) => s || '',
}))

// 组件会 import element-plus 的 ElMessage(仅在拦截器/提示处用), 这里给最小替身
vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

const THREAD_KEY = 'ai-assistant-thread'
const PENDING_KEY = 'ai-assistant-pending-task'

/**
 * 复位 `useAssistant` 的全局单例 `state.visible`。
 *
 * ⚠️ 该 state 是**模块级 reactive 单例**（`composables/useAssistant.js`），
 *    在同一个测试文件的所有用例间共享。若不复位：
 *    上个用例把 `visible` 置 true → 本用例再置 true 属「值未变化」
 *    → 组件内的 `watch(visible)` **不触发** → 误判「未请求配置」（#96 排查实录）。
 *    这是测试隔离问题，不是产品缺陷 —— 但必须复位，否则测试结果不可信。
 */
async function resetAssistantVisible() {
  const { useAssistant } = await import('@/composables/useAssistant')
  useAssistant().closeAssistant()
}

/** 从组件实例上取内部状态(script setup 未 expose, 走 setupState) */
async function mountDrawer(existingPinia = null) {
  const { default: Comp } = await import('../AssistantDrawer.vue')
  const { mount } = await import('@vue/test-utils')
  // #96: 组件内用了 useAuthStore() → 需 active pinia。
  // 传入 existingPinia 可让测试在 mount **之前**预置登录态。
  const { createPinia, setActivePinia } = await import('pinia')
  const pinia = existingPinia || createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Comp, {
    global: {
      plugins: [pinia],
      stubs: {
        'el-drawer': { template: '<div><slot name="header" /><slot /></div>' },
        'el-icon': { template: '<i><slot /></i>' },
        'el-tooltip': { template: '<span><slot /></span>' },
        'el-popover': { template: '<span><slot name="reference" /><slot /></span>' },
        'el-input': { template: '<textarea />' },
        'el-select': { template: '<div><slot /></div>' },
        'el-option': { template: '<div />' },
        'el-date-picker': { template: '<div />' },
        'el-button': { template: '<button><slot /></button>' },
        'el-result': { template: '<div><slot name="sub-title" /></div>' },
        'el-tag': { template: '<span><slot /></span>' },
      },
    },
  })
  return wrapper
}

describe('AssistantDrawer #66 轮询健壮性与续查', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    sessionStorage.clear()
    await resetAssistantVisible()   // 单例隔离，见函数注释
    // jsdom 不实现 matchMedia(组件用它判断移动端尺寸) → 补最小桩
    if (!window.matchMedia) {
      window.matchMedia = () => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
      })
    }
    aiConfig.mockResolvedValue({ data: { enabled: true, periods: [] } })
    aiAnalyze.mockReset()
    aiTaskStatus.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    sessionStorage.clear()
  })

  it('轮询会追到终态: 连续 running 后拿到 success(不是只查一次)', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({
      data: { task_id: 'ai_t1', status: 'running', window_label: '2026-09-10(昨天)', window_source: 'nl' },
    })
    // 前两次 running, 第三次 success —— 若实现"只查一次"则永远拿不到结果
    aiTaskStatus
      .mockResolvedValueOnce({ data: { status: 'running' } })
      .mockResolvedValueOnce({ data: { status: 'running' } })
      .mockResolvedValueOnce({
        data: { status: 'success', result_md: '最终分析结果', window_label: '2026-09-10(昨天)', window_source: 'nl' },
      })

    const vm = wrapper.vm
    vm.question = '昨天的运营数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(4000)
    await p

    expect(aiTaskStatus.mock.calls.length).toBeGreaterThanOrEqual(3)
    const last = vm.messages[vm.messages.length - 1]
    expect(last.content).toBe('最终分析结果')
    expect(last.loading).toBe(false)
    // 窗口必须回显(用户要能核对按哪个窗口统计)
    expect(last.windowLabel).toContain('2026-09-10')
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull()
  })

  it('单次查询失败会重试, 不会直接放弃', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_t2', status: 'running' } })
    aiTaskStatus
      .mockRejectedValueOnce(new Error('网络抖动'))
      .mockResolvedValueOnce({ data: { status: 'success', result_md: '重试后成功' } })

    const vm = wrapper.vm
    vm.question = '近3天'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(6000)
    await p

    expect(aiTaskStatus.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(vm.messages[vm.messages.length - 1].content).toBe('重试后成功')
  })

  it('连续失败达上限时: 保留 task_id 供续查, 且给出明确提示(不静默)', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_t3', status: 'running' } })
    aiTaskStatus.mockRejectedValue(new Error('一直失败'))

    const vm = wrapper.vm
    vm.question = '上月数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(60000)
    await p

    const last = vm.messages[vm.messages.length - 1]
    expect(last.content).toContain('查询结果失败')
    expect(last.loading).toBe(false)
    // 关键: task_id 必须留着, 否则结果永久丢失
    expect(sessionStorage.getItem(PENDING_KEY)).toBe('ai_t3')
  })

  it('抽屉关闭后重开: 从未完成任务续查并拿到结果(不丢答案)', async () => {
    // 模拟"上次会话留下未完成任务"
    sessionStorage.setItem(THREAD_KEY, JSON.stringify([
      { role: 'user', content: '昨天的运营数据' },
      { role: 'assistant', content: '⏳ 模型正在生成分析…', loading: false },
    ]))
    sessionStorage.setItem(PENDING_KEY, 'ai_t9')
    aiTaskStatus.mockResolvedValue({ data: { status: 'success', result_md: '续查拿到的结果' } })

    const wrapper = await mountDrawer()
    const vm = wrapper.vm
    // 打开抽屉 → 触发 restoreThread → 续查
    vm.state.visible = true
    await vi.advanceTimersByTimeAsync(4000)

    // 第二参为 { silentError: true }(#89): 由助手面板给具体文案, 免双重提示
    expect(aiTaskStatus).toHaveBeenCalledWith('ai_t9', { silentError: true })
    const last = vm.messages[vm.messages.length - 1]
    expect(last.content).toBe('续查拿到的结果')
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull()
  })

  it('窗口来源会被翻译成可读文案', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({
      data: { status: 'success', result_md: 'ok', window_source: 'nl', window_label: '2026-09-09 ~ 2026-09-11(近3天, 共3天)' },
    })
    const vm = wrapper.vm
    vm.question = '近3天'
    await vm.submit()
    const last = vm.messages[vm.messages.length - 1]
    expect(last.windowLabel).toContain('自动识别')
    expect(last.windowLabel).toContain('近3天')
  })

  it('failed 且后端未给 error 时, 前端展示兜底文案(不显示"未知错误")', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { status: 'failed', error: null } })
    const vm = wrapper.vm
    vm.question = '今天退款'
    await vm.submit()
    const last = vm.messages[vm.messages.length - 1]
    expect(last.content).toContain('服务端日志')
    expect(last.content).not.toContain('未知错误')
  })
})

/**
 * #89: 404/410 是**终态**而非瞬时错误。
 *
 * 生产事故: 后端任务存内存, 重启即全丢 → `/ai/tasks/{id}` 404;
 * 原实现把 404 当瞬时错误重试, 重试耗尽后仍 `_savePending(taskId)` 保留 pending
 * → 下次开抽屉再 404 → **死循环**(实测同一 task_id 于 09-14 02:09 被重试 4 次,
 * 间隔 2.4s = POLL_INTERVAL_MS×2, 次日 09:38 仍 401 未清)。
 */
describe('AssistantDrawer #89 任务过期为终态', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    sessionStorage.clear()
    await resetAssistantVisible()   // 单例隔离，见函数注释
    if (!window.matchMedia) {
      window.matchMedia = () => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
      })
    }
    aiConfig.mockResolvedValue({ data: { enabled: true, periods: [] } })
    aiAnalyze.mockReset()
    aiTaskStatus.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    sessionStorage.clear()
  })

  /** 构造带 HTTP 状态的拒绝(axios 错误形状) */
  function httpError(status) {
    const e = new Error(`Request failed with status code ${status}`)
    e.response = { status, data: { detail: '任务不存在或已过期' } }
    return e
  }

  it('404 → 只请求 1 次, 不重试(终态)', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_gone', status: 'running' } })
    aiTaskStatus.mockRejectedValue(httpError(404))

    const vm = wrapper.vm
    vm.question = '今天的运营数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(20000)
    await p

    // 关键: 不重试 —— 若重试则次数会达到 MAX_POLL_ERRORS(5)
    expect(aiTaskStatus.mock.calls.length).toBe(1)
  })

  it('404 → 清除 pending(否则下次开抽屉再 404 形成死循环)', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_gone', status: 'running' } })
    aiTaskStatus.mockRejectedValue(httpError(404))

    const vm = wrapper.vm
    vm.question = '今天的运营数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(20000)
    await p

    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull()
  })

  it('404 → 文案明确说明已过期并引导重新提问', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_gone', status: 'running' } })
    aiTaskStatus.mockRejectedValue(httpError(404))

    const vm = wrapper.vm
    vm.question = '今天的运营数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(20000)
    await p

    const last = vm.messages[vm.messages.length - 1]
    expect(last.content).toContain('已过期')
    expect(last.content).toContain('重新提问')
    expect(last.loading).toBe(false)
    // 不应再出现"连续 N 次失败"的瞬时错误文案
    expect(last.content).not.toContain('连续')
  })

  it('410 → 同样按终态处理', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_gone', status: 'running' } })
    aiTaskStatus.mockRejectedValue(httpError(410))

    const vm = wrapper.vm
    vm.question = '今天的运营数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(20000)
    await p

    expect(aiTaskStatus.mock.calls.length).toBe(1)
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull()
  })

  it('回归: 网络错误(无 response)仍保持重试到上限并保留 pending 供续查', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_net', status: 'running' } })
    // 纯网络错误: 无 response 字段 → 走瞬时错误分支
    aiTaskStatus.mockRejectedValue(new Error('Network Error'))

    const vm = wrapper.vm
    vm.question = '今天的运营数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(120000)
    await p

    expect(aiTaskStatus.mock.calls.length).toBe(5)   // MAX_POLL_ERRORS
    // 瞬时错误必须保留 task_id, 结果还有可能取回
    expect(sessionStorage.getItem(PENDING_KEY)).toBe('ai_net')
    expect(vm.messages[vm.messages.length - 1].content).toContain('连续')
  })

  it('回归: 404 之后再遇到 5xx 仍能正常续查(状态机不被污染)', async () => {
    const wrapper = await mountDrawer()
    aiAnalyze.mockResolvedValue({ data: { task_id: 'ai_x', status: 'running' } })
    aiTaskStatus
      .mockRejectedValueOnce(httpError(500))
      .mockResolvedValueOnce({ data: { status: 'success', result_md: '仍然拿到了结果' } })

    const vm = wrapper.vm
    vm.question = '今天的运营数据'
    const p = vm.submit()
    await vi.advanceTimersByTimeAsync(10000)
    await p

    expect(vm.messages[vm.messages.length - 1].content).toBe('仍然拿到了结果')
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull()
  })
})

/**
 * #96: `/ai/config` 惰性加载 —— 未登录/未打开抽屉时不请求。
 *
 * 生产实测 `/ai/config` 被调 **271 次**（是 `analyze` 的 3.3 倍），
 * 其中 **16 次 401**：因为组件随主布局**全局挂载**，而原实现在 onMounted
 * 就请求配置 → 每个页面加载（含未登录页）都发一次。
 */
describe('AssistantDrawer #96 配置惰性加载', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    sessionStorage.clear()
    localStorage.clear()
    // ⚠️ 测试隔离：`useAssistant` 的 `state` 是**模块级 reactive 单例**
    //    （`composables/useAssistant.js`），会跨测试文件/用例共享。
    //    若不复位, 上个用例置 `visible=true` 后, 本用例再置 true
    //    属「值未变化」→ `watch` 不触发 → 误判「未请求配置」。
    await resetAssistantVisible()
    if (!window.matchMedia) {
      window.matchMedia = () => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
      })
    }
    aiConfig.mockReset()
    aiConfig.mockResolvedValue({
      data: { enabled: true, periods: [{ quick_questions: ['今天运营数据'] }] },
    })
    aiAnalyze.mockReset()
    aiTaskStatus.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    sessionStorage.clear()
    localStorage.clear()
  })

  it('组件 mount(未打开抽屉)时不请求 /ai/config', async () => {
    await mountDrawer()
    await vi.advanceTimersByTimeAsync(2000)
    expect(aiConfig).not.toHaveBeenCalled()
  })

  it('未登录时打开抽屉也不请求 /ai/config(避免 401 噪音)', async () => {
    const wrapper = await mountDrawer()
    // 测试环境无 token → isLoggedIn=false
    wrapper.vm.state.visible = true
    await vi.advanceTimersByTimeAsync(2000)
    expect(aiConfig).not.toHaveBeenCalled()
  })

  it('已登录且打开抽屉时才请求 /ai/config(有 token 的场景)', async () => {
    // ⚠️ 必须在 mount **之前**建立 pinia 并置 token —— mount 时的 setup 会取
    //    store（组件内 `const authStore = useAuthStore()`），时机错了就拿不到。
    const { createPinia, setActivePinia } = await import('pinia')
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore(pinia).token = 'fake-token-for-test'

    const wrapper = await mountDrawer(pinia)
    wrapper.vm.state.visible = true
    await vi.advanceTimersByTimeAsync(2000)
    expect(aiConfig).toHaveBeenCalled()
  })
})
