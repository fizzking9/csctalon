/**
 * usePolling 统一轮询层单测(S-P1-2 / B2-8)
 *
 * 覆盖:
 *   1. start/stop: 定时器节奏 + 停止后不再触发
 *   2. 同 key 去重: 重复注册清掉旧任务, 只有新 fn 被调
 *   3. 防重入: async fn 未结束时重叠 tick 被跳过
 *   4. 可见性: hidden 暂停, visible 立即补跑并恢复节奏
 *   5. scope.stop()(=组件卸载)自动停止该 scope 内任务
 */

import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { POLL_INTERVALS, _resetForTests, usePolling } from '../usePolling'

describe('usePolling', () => {
  let scope

  beforeEach(() => {
    vi.useFakeTimers()
    _resetForTests()
    scope = effectScope()
  })

  afterEach(() => {
    scope.stop()
    _resetForTests()
    vi.useRealTimers()
  })

  // usePolling 依赖 effect 生命周期, 放进 scope.run 模拟 setup
  const withScope = (fn) => scope.run(() => fn(usePolling()))

  it('按间隔触发, stop 后停止', async () => {
    const fn = vi.fn()
    withScope(({ startPolling }) => {
      startPolling('k', fn, POLL_INTERVALS.LOGIN)
    })
    // async 版会刷新微任务(让 _tick 的 running 复位), ticks 在 2s/4s
    await vi.advanceTimersByTimeAsync(4000)
    expect(fn).toHaveBeenCalledTimes(2)

    withScope(({ stopPolling }) => stopPolling('k'))
    await vi.advanceTimersByTimeAsync(10000)
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('同 key 重复注册: 旧任务被清, 只调新 fn', async () => {
    const oldFn = vi.fn()
    const newFn = vi.fn()
    withScope(({ startPolling }) => {
      startPolling('k', oldFn, 2000)
      startPolling('k', newFn, 5000)
    })
    // t=2 处旧 fn 不应再被调
    await vi.advanceTimersByTimeAsync(2000)
    expect(oldFn).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(3000)
    expect(newFn).toHaveBeenCalledTimes(1)
  })

  it('async 执行未结束时重叠 tick 被跳过(防重入)', async () => {
    let release
    const inFlight = new Promise((r) => { release = r })
    const fn = vi.fn(() => inFlight)
    withScope(({ startPolling }) => {
      startPolling('k', fn, 2000)
    })
    // t=2/4/6 三个 tick: 首次进入 in-flight, 后两次重叠被同步跳过
    vi.advanceTimersByTime(6000)
    expect(fn).toHaveBeenCalledTimes(1)

    release()
    // 等 _tick 的 await 链 + finally 把 running 复位(两轮微任务足够)
    await Promise.resolve()
    await Promise.resolve()
    vi.advanceTimersByTime(2000) // t=8 释放后的下一个 tick 可再进入
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('页面 hidden 暂停; visible 立即补跑并恢复节奏', async () => {
    const fn = vi.fn()
    withScope(({ startPolling }) => {
      startPolling('k', fn, 2000)
    })

    // jsdom visibilityState 不可直接赋值, 用 defineProperty 模拟切后台
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(10000) // 暂停期不触发
    expect(fn).not.toHaveBeenCalled()

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(fn).toHaveBeenCalledTimes(1) // 回前台立即补跑一次
    await vi.advanceTimersByTimeAsync(2000) // 微任务刷新后节奏恢复, t+2s 再触发
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('scope.stop()(组件卸载)自动停止本 scope 注册的任务', async () => {
    const fn = vi.fn()
    const local = effectScope()
    local.run(() => {
      const { startPolling } = usePolling()
      startPolling('k', fn, 2000)
    })
    local.stop() // = 组件卸载 → onScopeDispose 自动停止
    await vi.advanceTimersByTimeAsync(10000)
    expect(fn).not.toHaveBeenCalled()
  })
})
