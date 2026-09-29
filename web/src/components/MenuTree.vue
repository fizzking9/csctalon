<template>
  <!--
    菜单渲染器 (#46, A-20260910-004 LOCKED)
    桌面侧边栏与移动端抽屉**共用本组件** —— 单一配置源 drivers 两端, 杜绝硬编码漂移。

    递归渲染 MENU_GROUPS:
      - 叶子项(path) → el-menu-item
      - 分组(children) → el-sub-menu(支持两层嵌套)
  -->
  <template v-for="node in visibleNodes" :key="node.key || node.path">
    <!-- 有子节点的分组 → el-sub-menu -->
    <el-sub-menu v-if="nodeChildren(node).length" :index="node.key || node.path">
      <template #title>
        <el-icon v-if="node.icon"><component :is="iconOf(node.icon)" /></el-icon>
        <span>{{ node.title }}</span>
      </template>
      <MenuTree :nodes="nodeChildren(node)" />
    </el-sub-menu>

    <!-- 叶子项 -->
    <el-menu-item v-else-if="node.path" :index="node.path">
      <el-icon v-if="node.icon"><component :is="iconOf(node.icon)" /></el-icon>
      <span>{{ node.title }}</span>
      <!-- 角标(如事务通知未读数; error 时红色显示严重故障数) -->
      <el-badge
        v-if="node.badge === 'eventUnread' && layoutState.eventUnread > 0"
        :value="layoutState.eventUnreadError > 0 ? layoutState.eventUnreadError : layoutState.eventUnread"
        :max="99"
        :type="layoutState.eventUnreadError > 0 ? 'danger' : 'warning'"
        class="menu-badge"
      >
        <span />
      </el-badge>
    </el-menu-item>
  </template>
</template>

<script setup>
import { computed } from 'vue'
import * as ElIcons from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { showByVisibility } from '@/utils/env'
import { BUILD_MODES } from '@/config/menu'
import { useLayoutState } from '@/composables/useLayoutState'

defineOptions({ name: 'MenuTree' })

const props = defineProps({
  nodes: { type: Array, required: true },
})

const authStore = useAuthStore()
// 未读数由 useLayoutState 共享(避免每个递归实例各自轮询)
// ⚠️ 不要解构 —— readonly(state) 解构后会丢失响应性
const layoutState = useLayoutState()

const iconOf = (name) => ElIcons[name] || ElIcons.Menu

/** 角色可见性: roles 为空数组/null 表示全员。 */
function roleVisible(roles) {
  if (!roles || !roles.length) return true
  return roles.includes(authStore.role)
}

/** 构建模式可见性: always 恒显 / debug_only 仅 debug / hidden 不显示。 */
function buildVisible(mode) {
  if (mode === BUILD_MODES.HIDDEN) return false
  if (mode === BUILD_MODES.DEBUG_ONLY) return showByVisibility('debug_only')
  return true
}

/**
 * 子节点解析。
 *
 * ⚠️ 必须同时合并三类来源, 漏掉任何一类都会导致菜单整块不渲染:
 *    1. node.children      —— 常规分组子项(最常见)
 *    2. node.altChildren   —— 「同一入口, 部分角色额外多一项」的附加项
 *                             (如 策略发布: 全员见文档查阅, admin/supervisor 额外见策略管理)
 *    3. 自身(当 node 是叶子却带 altChildren 时, 把自己降为第一个子项)
 *
 * 递归返回前统一做「角色 + 构建模式」过滤, 保证子层与顶层可见性规则一致。
 */
function nodeChildren(node) {
  const out = []
  const alt = (node.altChildren || []).filter((c) => roleVisible(c.roles))

  // 场景 3: 叶子项 + 附加项 → 自身降级为第一个子项
  if (node.path && alt.length) out.push({ ...node, altChildren: undefined })

  out.push(...(node.children || []))
  out.push(...alt)

  return out.filter((c) => roleVisible(c.roles) && buildVisible(c.buildMode))
}

const visibleNodes = computed(() =>
  (props.nodes || []).filter(
    (n) => roleVisible(n.roles) && buildVisible(n.buildMode),
  ),
)
</script>

<style scoped>
.menu-badge {
  margin-left: 6px;
  vertical-align: middle;
}
</style>
