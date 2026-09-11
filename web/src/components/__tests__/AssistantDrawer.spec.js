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

/** 从组件实例上取内部状态(script setup 未 expose, 走 setupState) */
async function mountDrawer() {
  const { default: Comp } = await import('../AssistantDrawer.vue')
  const { mount } = await import('@vue/test-utils')
  const wrapper = mount(Comp, {
    global: {
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
  beforeEach(() => {
    vi.useFakeTimers()
    sessionStorage.clear()
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

    expect(aiTaskStatus).toHaveBeenCalledWith('ai_t9')
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
