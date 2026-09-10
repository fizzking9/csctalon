// vitest 配置 - 前端单元测试
// 说明: 测试 utils 纯逻辑函数(金额格式化/XSS转义), 对应后端 pytest 基建。
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    // 与 vite.config.js 保持一致: 支持 @/xxx 别名
    // (菜单配置等模块用 @/ 导入, 缺此别名会导致测试无法解析)
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // jsdom 环境: 提供 window/DOM, 使 DOMPurify 正常工作(XSS 防护可测)
    environment: 'jsdom',
    include: ['src/**/__tests__/**/*.spec.js'],
    globals: true,
    // 补 localStorage/sessionStorage(setup 见文件内注释)
    setupFiles: ['./src/test-setup.js'],
    // 菜单可见性依赖 VITE_BUILD_MODE —— 必须在**模块加载前**由环境变量注入,
    // 因为 utils/env.js 的 BUILD_MODE 是模块级常量(运行时赋值无效)。
    // 测试统一用 debug 模式: 让 debug_only 档菜单也参与渲染断言(覆盖面更全)。
    env: {
      VITE_BUILD_MODE: 'debug',
    },
  },
})
