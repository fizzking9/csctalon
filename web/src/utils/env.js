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
// ============================================================================
// ★ 2026-09-22: 明确区分**两个正交概念**（原先混用，见 docs/arch/A-20260922-001）
// ============================================================================
//   ① **环境模式**  APP_MODE  → dev / test / prod
//      决定: 连哪个后端库、日志级别、是否显示调试信息。**与菜单无关**。
//      对应前端的 VITE_APP_MODE（本文件下方 APP_MODE）。
//
//   ② **构建档位**  BUILD_MODE → debug / release
//      决定: 菜单显示哪些（核心菜单 vs 全部）。**与环境无关** ——
//      生产也可能出 debug 档（内测版全量菜单），本地也可能 release 档。
//
//   为什么必须分开：改造前只有 BUILD_MODE 一个概念，被当成"环境"使用 ——
//   于是"内测全量菜单"与"连生产库"这两个完全不同的诉求被耦合在一起，
//   无法表达"生产环境 + 全量菜单"（内测场景）或"本地环境 + 核心菜单"。
// ============================================================================

export const BUILD_MODE = import.meta.env.VITE_BUILD_MODE || 'release'

export const isDebugMode = BUILD_MODE === 'debug'

/**
 * 环境模式（dev / test / prod）—— 与构建档位正交。
 *
 * 由 VITE_APP_MODE 注入（docker 构建时来自 .env 的 APP_MODE）。
 * 缺省 'production'（安全侧：默认按生产处理，不泄露调试信息）。
 *
 * ⚠️ 与 BUILD_MODE 的区别：本值表示"这是一个什么环境的构建"，
 *    不表示"显示哪些菜单" —— 后者用 BUILD_MODE/showByVisibility。
 */
export const APP_MODE = import.meta.env.VITE_APP_MODE || 'production'

/** 是否非生产环境（用于显示调试入口、环境角标等）。 */
export const isNonProdEnv = APP_MODE !== 'production'

/**
 * 菜单可见性判断(在模板 v-if 中使用)。
 * @param {string} visibility 'always' | 'debug_only'
 */
export function showByVisibility(visibility = 'always') {
  return visibility === 'always' || isDebugMode
}
