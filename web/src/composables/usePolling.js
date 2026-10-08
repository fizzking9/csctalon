// -*- coding: utf-8 -*-
/**
 * 统一轮询层(S-P1-2 / B2-8)
 *
 * 设计说明(Design Rationale):
 *   目的: 收敛全站散落在各页面的自建 setInterval(评审时 9+ 处), 解决三个问题:
 *     ① 间隔单源 —— 轮询间隔常量只在本文件 POLL_INTERVALS 定义, 页面不再各写魔法数字;
 *     ② 可见性暂停 —— 页面切到后台(document.hidden)时暂停全部轮询,
 *        回到前台立即补跑一次再恢复节奏, 省请求且回来即新数据;
 *     ③ 去重 —— 同一 key 全局单例(重复注册先清旧), async 任务防重入
 *        (上一轮未结束则跳过本轮, 慢请求不叠加)。
 *   取舍:
 *     · 不做统一错误重试/熔断 —— 各任务的失败语义不同(进度轮询连续失败要停、
 *       全局状态静默忽略), 保留在调用方; 本层只管调度。
 *     · 不做跨标签页广播 —— 单机单标签使用场景, 无需求。
 *   生命周期: 组件卸载(setup 作用域释放)时自动停止该组件 start 的任务
 *             —— 用 onScopeDispose 而非 onBeforeUnmount, 纯 effectScope 同样生效;
 *             也可手动 stop(key)。
 */

import { onScopeDispose } from 'vue'

// 间隔常量(全站唯一来源): 新增档位先在此登记
export const POLL_INTERVALS = {
  LOGIN: 2000,        // 扫码/共享文档登录会话状态
  PROGRESS: 2000,     // 采集任务进度
  TASK_WATCH: 3000,   // BMS→SCRM 自动衔接观察 / OAuth 授权结果
  SYNC_STATUS: 15000, // 全局同步状态 + 通知未读角标
  TODAY_AUTO: 60000,  // 今日看板自动刷新(仅日视图·今天)
  PREFLIGHT: 30000,   // 预检(凭证/冷却/时间窗口)
}

// ──────────────────────── 全局任务注册表(跨组件去重) ────────────────────────

const tasks = new Map() // key -> { fn, intervalMs, timer, running }

let _paused = typeof document !== 'undefined' && document.visibilityState === 'hidden'

function _startTimer(t) {
  if (_paused || t.timer) return
  t.timer = setInterval(() => { void _tick(t) }, t.intervalMs)
}

function _stopTimer(t) {
  if (t.timer) {
    clearInterval(t.timer)
    t.timer = null
  }
}

async function _tick(t) {
  // 防重入: 上一次 async 执行未结束 → 跳过本轮
  if (t.running) return
  t.running = true
  try {
    await t.fn()
  } finally {
    t.running = false
  }
}

// 全局只绑一次 visibilitychange(模块级, 与组件生命周期无关)
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    _paused = document.visibilityState === 'hidden'
    if (_paused) {
      // 切后台: 暂停全部, 但保留注册项(回前台恢复)
      for (const t of tasks.values()) _stopTimer(t)
    } else {
      // 回前台: 每个任务立即补跑一次, 随后恢复间隔节奏
      for (const t of tasks.values()) {
        void _tick(t)
        _startTimer(t)
      }
    }
  })
}

// ──────────────────────── 组件级组合式 API ────────────────────────

export function usePolling() {
  // 本该组件注册的 key(卸载时只停自己的)
  const ownKeys = new Set()

  function startPolling(key, fn, intervalMs, { immediate = false } = {}) {
    // 同 key 去重: 无论属不属于本组件, 先停旧任务再注册(后注册语义覆盖)
    const old = tasks.get(key)
    if (old) _stopTimer(old)
    const t = { fn, intervalMs, timer: null, running: false }
    tasks.set(key, t)
    ownKeys.add(key)
    if (immediate) void _tick(t)
    _startTimer(t)
    // 返回该任务的停止函数(便于局部调用)
    return () => stopPolling(key)
  }

  function stopPolling(key) {
    const t = tasks.get(key)
    if (t) {
      _stopTimer(t)
      tasks.delete(key)
    }
    ownKeys.delete(key)
  }

  function stopAllPolling() {
    for (const k of [...ownKeys]) stopPolling(k)
  }

  // 组件卸载 / scope 释放: 自动停止本组件注册的任务
  onScopeDispose(stopAllPolling)

  return { startPolling, stopPolling, stopAllPolling }
}

// 测试辅助: 重置全局注册表(仅单测使用)
export function _resetForTests() {
  for (const t of tasks.values()) _stopTimer(t)
  tasks.clear()
  _paused = false
}
