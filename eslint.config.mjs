// ═══════════════════════════════════════════════════════════════════════════
//  根级 ESLint 配置 —— **桥接文件**，不是第二套规则
//
//  ★ 为什么需要它（2026-09-11 #67 排查结论）
//
//  现象：`web/src/views/dashboards/ComplaintAnalysis.vue` 中 `v-model:preset="..."`
//        被编辑器标为 [vue/no-v-model-argument] 'v-model' directives require no argument.
//
//  实测澄清（三条证据）：
//    1. `cd web && npx eslint src/...`          → 0 错误，退出码 0
//    2. `vite build` / `docker compose build --no-cache` → 构建成功
//    3. ESLint Node API（cwd=web）解析该规则值为 `[0]`（已关闭）
//
//  根因：**仓库根目录没有 ESLint 配置**。VS Code 的 ESLint 扩展默认以**工作区根**
//        为 cwd 启动 ESLint；在根目录找不到任何配置时，会退回到内置默认规则集，
//        于是把 Vue 3 的具名 v-model 按旧语义判为非法。而 `vue/no-v-model-argument`
//        只在 eslint-plugin-vue 的 `flat/vue2-*` 规则集中启用。
//
//  修复策略（**单一事实来源**）：
//    真实规则**只在 `web/eslint.config.js`**，本文件仅做转发 ——
//    避免出现两套配置互相漂移（历史教训：菜单/口径多处硬编码导致不一致）。
//
//  ⚠️ 约束：本文件**不得新增任何规则**。要改规则请改 `web/eslint.config.js`。
// ═══════════════════════════════════════════════════════════════════════════

import webConfig from './web/eslint.config.js'

export default [
  // 忽略构建产物与依赖（与 web/ 保持一致）
  { ignores: ['**/dist/**', '**/node_modules/**', '**/.venv/**', 'data/**', 'logs/**'] },

  // 直接复用 web 侧配置 —— 单一事实来源
  ...webConfig,
]
