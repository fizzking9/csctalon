// vitest 配置 - 前端单元测试
// 说明: 测试 utils 纯逻辑函数(金额格式化/XSS转义), 对应后端 pytest 基建。
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    // jsdom 环境: 提供 window/DOM, 使 DOMPurify 正常工作(XSS 防护可测)
    environment: 'jsdom',
    include: ['src/**/__tests__/**/*.spec.js'],
    globals: true,
  },
})
