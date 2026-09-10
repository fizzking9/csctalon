/**
 * 布局层共享状态 (#46)
 *
 * 背景: 菜单树是递归组件(MenuTree), 若每个递归实例各自轮询未读数会造成
 * N 倍请求。本模块用模块级 reactive 单例集中持有「布局层」状态,
 * 由 MainLayout 负责轮询写值, MenuTree 只读消费。
 */

import { reactive, readonly } from 'vue'

const state = reactive({
  /** 事务通知未读数(侧边栏/顶部角标) */
  eventUnread: 0,
  /** 布局是否处于移动端断点 */
  isMobile: false,
})

export function useLayoutState() {
  return readonly(state)
}

/** 仅供 MainLayout 等"持有者"写值用。 */
export function useLayoutStateWriter() {
  return {
    setEventUnread(v) {
      state.eventUnread = Number(v) || 0
    },
    setIsMobile(v) {
      state.isMobile = !!v
    },
  }
}
