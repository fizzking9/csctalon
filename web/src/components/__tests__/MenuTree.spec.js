// MenuTree 渲染测试 (#46, A-20260910-004 LOCKED)
//
// ⚠️ 本文件的存在理由(血泪教训):
//   menu.spec.js 只断言了**配置数据**(MENU_GROUPS 结构), 全部通过 ——
//   但线上菜单**整块不渲染**(<ul class="el-menu"> 内 0 个子节点)。
//   根因: MenuTree.nodeChildren() 漏合并 node.children, 只处理了 altChildren,
//   导致每个分组都被判为"无子节点", 又因不是叶子项(path)而被 v-if 全部过滤掉。
//
//   结论: **数据层测试无法替代渲染层测试**。本文件用真实挂载断言 DOM,
//   锁住"配置 → 可见菜单项"这条链路。

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import MenuTree from '@/components/MenuTree.vue'
import { MENU_GROUPS } from '@/config/menu'
import { useAuthStore } from '@/stores/auth'

/**
 * 挂载 MenuTree 并返回容器。
 *
 * 说明: Element Plus 的 el-menu-item / el-sub-menu 依赖 el-menu 的 provide,
 * 因此这里统一包一层 el-menu 容器, 与 MainLayout 的真实用法一致。
 */
function mountMenu(role = 'admin') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.user = { role }

  return mount(
    {
      components: { MenuTree },
      template: '<el-menu><MenuTree :nodes="nodes" /></el-menu>',
      data() {
        return { nodes: MENU_GROUPS }
      },
    },
    {
      global: {
        plugins: [pinia, ElementPlus],
        mocks: {
          // showByVisibility / router 等由组件内部 import, 不依赖 inject
        },
      },
    },
  )
}

describe('MenuTree 渲染 - 防"菜单整块不渲染"回归', () => {
  it('至少渲染出菜单项(曾经的 bug: 0 个子节点)', () => {
    const wrapper = mountMenu('admin')
    const items = wrapper.findAll('.el-menu-item')
    const subs = wrapper.findAll('.el-sub-menu')
    expect(
      items.length + subs.length,
      `菜单渲染为空! 检查 MenuTree.nodeChildren 是否正确合并 node.children`,
    ).toBeGreaterThan(0)
  })

  it('顶级分组渲染为 el-sub-menu', () => {
    const wrapper = mountMenu('admin')
    const titles = wrapper.findAll('.el-sub-menu__title').map((t) => t.text())
    // ★ #80: 顶层分组名由「运营看板」改为「看板」(page 改「今日值班看板」，
    //   若沿用旧名会出现「看板看板」的重复语义)
    expect(titles.some((t) => t.includes('看板'))).toBe(true)
    expect(titles.some((t) => t.includes('系统管理'))).toBe(true)
  })

  it('系统管理下渲染出 3 个二级子分组(三层结构真正落地)', () => {
    const wrapper = mountMenu('admin')
    const allTitles = wrapper.findAll('.el-sub-menu__title').map((t) => t.text())
    expect(allTitles.some((t) => t.includes('组织与权限'))).toBe(true)
    expect(allTitles.some((t) => t.includes('数据与接入'))).toBe(true)
    expect(allTitles.some((t) => t.includes('审计与配置'))).toBe(true)
  })

  it('系统管理下叶子项可渲染(如 用户管理 / 数据采集 / 接入中心)', () => {
    const wrapper = mountMenu('admin')
    const itemTexts = wrapper.findAll('.el-menu-item').map((i) => i.text())
    expect(itemTexts.some((t) => t.includes('用户管理'))).toBe(true)
    expect(itemTexts.some((t) => t.includes('数据采集'))).toBe(true)
    expect(itemTexts.some((t) => t.includes('接入中心'))).toBe(true)
  })

  it('role=agent 时看不到 系统管理(角色过滤在渲染层生效)', () => {
    const wrapper = mountMenu('agent')
    const titles = wrapper.findAll('.el-sub-menu__title').map((t) => t.text())
    expect(titles.some((t) => t.includes('系统管理'))).toBe(false)
  })

  it('role=supervisor 时能看到 数据分析(manager 放宽生效)', () => {
    const wrapper = mountMenu('supervisor')
    const titles = wrapper.findAll('.el-sub-menu__title').map((t) => t.text())
    expect(titles.some((t) => t.includes('数据分析'))).toBe(true)
  })
})
