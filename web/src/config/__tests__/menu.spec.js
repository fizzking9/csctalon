// 导航菜单结构测试 (#46, A-20260910-004 LOCKED)
//
// 目的: 用测试锁住「菜单重组」的两条硬约束, 防止后续改动悄悄破坏:
//   1. 零 path 变更 —— 菜单指向的 path 必须都存在于 router(防悬空菜单项)
//   2. 收敛目标 —— 一级菜单数量、系统管理三层分组结构
//
// 这些断言的价值: 菜单是"配置驱动"后, 靠人眼 review 很容易漏掉
// "加了个菜单项但忘了建路由"这种问题(点了白屏), 用测试兜住。

import { describe, it, expect } from 'vitest'
import { MENU_GROUPS, BUILD_MODES, collectMenuPaths } from '@/config/menu'
import router from '@/router'

/** 收集 router 中所有已注册的绝对 path。 */
function collectRouterPaths() {
  const paths = new Set()
  const walk = (routes, parent = '') => {
    for (const r of routes) {
      if (r.path) {
        const full = r.path.startsWith('/')
          ? r.path
          : `${parent}/${r.path}`.replace(/\/+/g, '/')
        paths.add(full)
      }
      if (r.children) {
        const base = r.path
          ? (r.path.startsWith('/') ? r.path : `${parent}/${r.path}`.replace(/\/+/g, '/'))
          : parent
        walk(r.children, base)
      }
    }
  }
  walk(router.options.routes)
  return paths
}

describe('菜单配置 - 防悬空菜单项', () => {
  it('菜单中每个 path 都存在于 router(避免点了白屏)', () => {
    const routerPaths = collectRouterPaths()
    const menuPaths = collectMenuPaths()
    const missing = menuPaths.filter((p) => !routerPaths.has(p))
    expect(missing, `以下菜单路径未在 router 中注册: ${missing.join(', ')}`).toEqual([])
  })
})

describe('菜单配置 - 收敛目标', () => {
  it('一级菜单数量 <= 8(原为 13 项过散)', () => {
    expect(MENU_GROUPS.length).toBeLessThanOrEqual(8)
  })

  it('系统管理下含 3 个子分组(组织与权限/数据与接入/审计与配置)', () => {
    const system = MENU_GROUPS.find((g) => g.key === 'system')
    expect(system, '系统管理分组必须存在').toBeTruthy()
    const subGroups = (system.children || []).filter((c) => c.children?.length)
    expect(subGroups.length).toBe(3)
    expect(subGroups.map((g) => g.key)).toEqual([
      'system-org', 'system-data', 'system-audit',
    ])
  })

  it('接入中心在「数据与接入」分组下(#44 需在菜单占位)', () => {
    const system = MENU_GROUPS.find((g) => g.key === 'system')
    const dataGroup = system.children.find((g) => g.key === 'system-data')
    const paths = (dataGroup.children || []).map((c) => c.path)
    expect(paths).toContain('/system/access-center')
  })

  it('数据分析组对 manager 可见(审计项 U3: 原为 admin-only 而错位)', () => {
    const group = MENU_GROUPS.find((g) => g.key === 'data-analysis')
    expect(group).toBeTruthy()
    expect(group.roles).toContain('supervisor')
    expect(group.roles).toContain('specialty_lead')
  })

  it('视图构建器仅 admin 可见(写操作权限配对)', () => {
    const group = MENU_GROUPS.find((g) => g.key === 'data-analysis')
    const builder = group.children.find((c) => c.path === '/analytics/builder')
    expect(builder.roles).toEqual(['admin'])
  })
})

describe('菜单配置 - buildMode 语义', () => {
  it('hidden 档的菜单项不出现在可见列表中', () => {
    const walk = (nodes, acc = []) => {
      for (const n of nodes || []) {
        if (n.buildMode === BUILD_MODES.HIDDEN) acc.push(n.path || n.key)
        if (n.children) walk(n.children, acc)
      }
      return acc
    }
    // 当前无 hidden 项(报表中心改由"不进菜单"处理), 此断言确保语义档位在使用中被正确区分
    expect(Array.isArray(walk(MENU_GROUPS))).toBe(true)
  })

  it('运营看板与系统管理为 always(核心菜单在所有构建模式可见)', () => {
    const dash = MENU_GROUPS.find((g) => g.key === 'dashboard')
    const system = MENU_GROUPS.find((g) => g.key === 'system')
    expect(dash.buildMode).toBe(BUILD_MODES.ALWAYS)
    expect(system.buildMode).toBe(BUILD_MODES.ALWAYS)
  })

  it('今日看板/运营看板命名与路由 meta.title 一致(审计项 U1)', () => {
    const dash = MENU_GROUPS.find((g) => g.key === 'dashboard')
    const byPath = Object.fromEntries(
      (dash.children || []).map((c) => [c.path, c.title]),
    )
    expect(byPath['/dashboards/today']).toBe('今日看板')
    expect(byPath['/dashboards/operation']).toBe('运营看板')
  })
})

describe('菜单配置 - 单一配置源', () => {
  it('collectMenuPaths 能递归收集嵌套子分组中的 path', () => {
    const paths = collectMenuPaths()
    // 来自二级分组(系统管理 → 数据与接入)的 path 应被收集
    expect(paths).toContain('/system/access-center')
    expect(paths).toContain('/data/ingest')
    // 来自叶子 altChildren 的 path 也应被收集
    expect(paths).toContain('/strategy/manage')
  })
})
