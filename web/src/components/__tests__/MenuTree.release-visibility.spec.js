// release 构建下的菜单可见性矩阵 (#221, 用户 2026-10-07 拍板的临时发布策略)
//
// 与 MenuTree.spec.js 的分工:
//   - MenuTree.spec.js 在 vitest 默认注入的 VITE_BUILD_MODE=debug 下断言(全量按角色);
//   - 本文件用 vi.mock 把 env 固定为 **release**(isDebugMode=false), 专门验证发布期规则:
//       · admin   → 可见全部菜单(含 debug_only 整组与子项)
//       · 非 admin → 可见集与历史逐字节一致(debug_only 一律隐藏, 不新增入口)
//
// 为什么必须 mock: utils/env.js 的 BUILD_MODE/isDebugMode 是模块级常量,
// vitest 在模块加载后无法用 stubEnv 改写(见 vitest.config.js 注释)。

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

// 固定为 release 语义: 仅 always 可见(debug_only 不再因 isDebugMode 而放行)。
vi.mock('@/utils/env', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    isDebugMode: false,
    showByVisibility: (visibility = 'always') => visibility === 'always',
  }
})

import MenuTree from '@/components/MenuTree.vue'
import { MENU_GROUPS } from '@/config/menu'
import { useAuthStore } from '@/stores/auth'

function mountMenu(role = 'admin') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.user = { role }
  return mount(
    {
      components: { MenuTree },
      template: '<el-menu><MenuTree :nodes="nodes" /></el-menu>',
      data: () => ({ nodes: MENU_GROUPS }),
    },
    { global: { plugins: [pinia, ElementPlus] } },
  )
}

const groupTitles = (w) => w.findAll('.el-sub-menu__title').map((t) => t.text())
const itemTexts = (w) => w.findAll('.el-menu-item').map((i) => i.text())

// menu.js 中 buildMode='debug_only' 的整组(发布期默认隐藏)
const DEBUG_GROUPS = ['运营工具', '管理闭环', '数据分析']
// always 整组
const ALWAYS_GROUPS = ['看板', 'AI 与分析', '系统管理']

describe('release 构建 · admin 可见全部菜单 (#221)', () => {
  it('admin 可见 debug_only 整组(运营工具/管理闭环/数据分析)', () => {
    const titles = groupTitles(mountMenu('admin'))
    for (const g of [...ALWAYS_GROUPS, ...DEBUG_GROUPS]) {
      expect(titles.some((t) => t.includes(g)), `admin 应见分组「${g}」`).toBe(true)
    }
  })

  it('admin 可见 debug_only 叶子项(运营工具-设备专项标签 / 专项分析-接待分析)', () => {
    const items = itemTexts(mountMenu('admin'))
    expect(items.some((t) => t.includes('设备专项标签'))).toBe(true)
    expect(items.some((t) => t.includes('接待分析'))).toBe(true)
    expect(items.some((t) => t.includes('设备流量审计'))).toBe(true) // 在「数据分析」组
  })
})

describe('release 构建 · 非 admin 维持历史可见集(逐字节不新增, #221)', () => {
  it('agent: 只见 always 且角色放行的「看板」, debug_only 整组全不可见', () => {
    const titles = groupTitles(mountMenu('agent'))
    expect(titles.some((t) => t.includes('看板'))).toBe(true)
    for (const g of DEBUG_GROUPS) {
      expect(titles.some((t) => t.includes(g)), `agent 不应见 debug_only 分组「${g}」`).toBe(false)
    }
    // 专项分析整组角色不含 agent(角色门控, 与本次改动无关, 一并锁死不回归)
    expect(titles.some((t) => t.includes('专项分析'))).toBe(false)
    expect(titles.some((t) => t.includes('AI 与分析'))).toBe(false)
    expect(titles.some((t) => t.includes('系统管理'))).toBe(false)
  })

  it('supervisor: always 的专项分析可见, 但 debug_only 的数据分析/运营工具/管理闭环仍隐藏', () => {
    const titles = groupTitles(mountMenu('supervisor'))
    expect(titles.some((t) => t.includes('专项分析'))).toBe(true)
    expect(titles.some((t) => t.includes('看板'))).toBe(true)
    for (const g of DEBUG_GROUPS) {
      expect(titles.some((t) => t.includes(g)), `release 下 supervisor 不应新增「${g}」`).toBe(false)
    }
    expect(titles.some((t) => t.includes('系统管理'))).toBe(false)
  })

  it('specialty_lead: 与 supervisor 同档, debug_only 整组不新增', () => {
    const titles = groupTitles(mountMenu('specialty_lead'))
    expect(titles.some((t) => t.includes('专项分析'))).toBe(true)
    for (const g of DEBUG_GROUPS) {
      expect(titles.some((t) => t.includes(g)), `release 下 specialty_lead 不应新增「${g}」`).toBe(false)
    }
  })
})
