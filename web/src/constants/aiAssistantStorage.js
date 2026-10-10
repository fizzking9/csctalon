/**
 * AI 助手会话级存储 key (工单 #66)
 *
 * 为什么单独抽一个模块: 这两个 key 有**两处**使用方 ——
 *   ① 组件 `components/AssistantDrawer.vue`(读写会话与待查任务)
 *   ② `stores/auth.js` 的 `localLogout`(登出时清理, 防上一个账号的经营数据
 *      留在同一标签页被下一位使用者看到)
 * 若各自硬编码字符串, 任一处改名都会静默导致"登出不清"或"续查失效", 且不会有
 * 任何报错。故收敛为单一常量来源。
 */
export const AI_THREAD_KEY = 'ai-assistant-thread'
export const AI_PENDING_KEY = 'ai-assistant-pending-task'
