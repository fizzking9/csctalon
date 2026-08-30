// 构建模式工具: 区分 debug(开发/内测) 与 release(生产发布) 的菜单可见性。
//
// 构建时由 VITE_BUILD_MODE 注入(见 package.json / Dockerfile / docker-compose.yml):
//   debug    → npm run build:debug   (VITE_BUILD_MODE=debug, 显示全部菜单)
//   release  → npm run build         (VITE_BUILD_MODE=release, 只显示核心菜单, 生产默认)
//
// ⚠️ 本机制只做【菜单/入口隐藏】, 不做页面访问拦截:
//    通过待办/工作台卡片等内部链接仍可访问隐藏页面(如 /todos/:id 详情),
//    这是业务需要(发布版"我的待办"点击"查看"要能进详情页)。
//
// 菜单可见性规则(三档):
//   'always'     → 所有构建模式都显示(核心菜单: 运营看板/工作台/我的待办/专项分析[3项]/系统管理)
//   'debug_only' → 仅 debug 构建显示(其余内测/管理闭环等功能)
export const BUILD_MODE = import.meta.env.VITE_BUILD_MODE || 'release'

export const isDebugMode = BUILD_MODE === 'debug'

/**
 * 菜单可见性判断(在模板 v-if 中使用)。
 * @param {string} visibility 'always' | 'debug_only'
 */
export function showByVisibility(visibility = 'always') {
  return visibility === 'always' || isDebugMode
}
