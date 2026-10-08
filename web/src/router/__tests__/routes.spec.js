// 路由完整性测试 (#260 B4-4, S-P1-4)
//
// 目的: 用测试锁住两条路由注册纪律, 防止回归:
//   1. 同一 path 只能注册一次 —— vue-router 同 path 多 record 时先注册者生效,
//      后注册的成为"死路由"(不可达但仍在包里, 且会 console 警告);
//      B4-4 前曾有 5 处 redirect + hidden 组件版双注册(均已清理)。
//   2. redirect 占位与组件路由互斥 —— 带 redirect 的 record 不应再有 component。

import { describe, it, expect } from 'vitest'
import router from '@/router'

/** 展开全部嵌套 route 为绝对 path 记录。 */
function flattenRoutes(routes, parent = '') {
  const out = []
  for (const r of routes) {
    const full = r.path
      ? (r.path.startsWith('/') ? r.path : `${parent}/${r.path}`).replace(/\/+/g, '/')
      : parent
    if (r.path) {
      // 布局路由(带 children)合法地 component+redirect 并存(redirect 仅兜底空匹配), 用 isLayout 标记
      out.push({
        path: full,
        hasComponent: !!r.component,
        hasRedirect: !!r.redirect,
        isLayout: !!r.children,
        name: r.name,
      })
    }
    if (r.children) out.push(...flattenRoutes(r.children, full))
  }
  return out
}

describe('router - 同 path 唯一注册(死路由防回归)', () => {
  it('不存在重复注册的 path(#260: 曾有 5 处 redirect+组件版双注册)', () => {
    const all = flattenRoutes(router.options.routes)
    const seen = new Map()
    const dup = []
    for (const r of all) {
      if (seen.has(r.path)) dup.push(`${seen.get(r.path).name || '-'} + ${r.name || '-'}`)
      seen.set(r.path, r)
    }
    expect(dup, `重复注册的 path: ${dup.join(', ')}`).toEqual([])
  })

  it('redirect 占位路由不得同时挂 component(布局路由的 children 兜底除外)', () => {
    const bad = flattenRoutes(router.options.routes).filter(
      (r) => r.hasRedirect && r.hasComponent && !r.isLayout
    )
    expect(bad.map((r) => r.path)).toEqual([])
  })

  it('组件路由必须有 name(布局路由与 pathMatch 兜底除外)', () => {
    const noName = flattenRoutes(router.options.routes).filter(
      (r) => r.hasComponent && !r.name && !r.isLayout && r.path !== '/:pathMatch(.*)*'
    )
    expect(noName.map((r) => r.path)).toEqual([])
  })
})
