// api 层冒烟测试（#282 / O-12 / S-P2-4）
//
// 覆盖策略:
//   ① import.meta.glob 自动纳入 web/src/api/ 下全部模块 —— 新增 api 模块
//      无需登记即被测（防"忘了补测试"漂移）。
//   ② 每个模块至少断言 1 个 mock 请求: 依次尝试各导出函数(无参调用),
//      直到某一个触发 request.get/post/put/delete; 全部不触发则失败。
//      （request 已被整体 mock 为立即 resolve, 无参调用不会真的发请求,
//      个别函数需要先访问入参属性而提前 throw → catch 后试下一个导出。）
//   ③ index.js(axios 客户端本体, 已被 mock)不参与。
//
// 设计说明（Design Rationale）:
// - 目的: 替前端开发回答「api 层模块是否都能导入且真实发出预期 HTTP 调用」——
//   防 api 模块语法错误/导入断裂在构建期外静默存在。
// - 取舍: 不逐函数断言 URL/参数(那是契约测试, 交给后端接口测试); 不做 msw
//   网络层 mock(mock @/api 客户端更轻, 60 模块零网络依赖)。

import { describe, expect, it, vi, beforeEach } from 'vitest'

// ── mock @/api 客户端(axios 实例) ──
const mockGet = vi.fn(() => Promise.resolve({ code: 0, data: null }))
const mockPost = vi.fn(() => Promise.resolve({ code: 0, data: null }))
const mockPut = vi.fn(() => Promise.resolve({ code: 0, data: null }))
const mockDelete = vi.fn(() => Promise.resolve({ code: 0, data: null }))
const MOCK_METHODS = { get: mockGet, post: mockPost, put: mockPut, delete: mockDelete }

vi.mock('@/api', () => ({
  default: MOCK_METHODS,
  // index.js 的具名导出(App 启动用), mock 也需提供防导入断裂
  fetchSsoConfig: vi.fn(() => Promise.resolve(false)),
}))

// 自动纳入 api/ 下全部 .js/.ts 模块(排除 index.js 客户端本体)
const modules = import.meta.glob('../*.{js,ts}')

const entries = Object.entries(modules).filter(([path]) => !path.endsWith('/index.js'))

describe('api 层冒烟: 每模块可导入 + 至少 1 个 mock 请求', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('模块清单非空(glob 生效)', () => {
    expect(entries.length).toBeGreaterThan(30)
  })

  for (const [path, loader] of entries) {
    const name = path.replace(/^\.\.\//, '')
    it(`${name}: 可导入且至少一个导出函数发起请求`, async () => {
      const mod = await loader()
      // 两种导出风格: 具名函数(多数模块) / 导出对象聚合方法(如 reconcile.js)
      const fns = Object.values(mod).flatMap((v) => {
        if (typeof v === 'function') return [v]
        if (v && typeof v === 'object') return Object.values(v).filter((m) => typeof m === 'function')
        return []
      })
      expect(fns.length, `${name} 应至少导出 1 个函数`).toBeGreaterThan(0)

      let called = false
      for (const fn of fns) {
        try {
          // 不 await: 只需断言"发起了调用", mock 同步记录即可
          fn()
        } catch {
          // 无参调用可能因入参解构/属性访问提前 throw, 试下一个导出
        }
        called = Object.values(MOCK_METHODS).some((m) => m.mock.calls.length > 0)
        if (called) break
      }
      expect(called, `${name} 全部 ${fns.length} 个导出函数无参调用均未触发 request.*`).toBe(true)
    })
  }
})
