import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // 附件静态资源(2026-08-20 审计 H-6/FEA-13 修复): dev 与 prod nginx 行为对齐
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // S-P1-3/B2-9: 第三方依赖拆成独立缓存块, 业务改动不再使 vendor 缓存失效;
        // 同时让 echarts 按需模块独立成块(不再挤进 BaseChart chunk)。
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          // echarts 及其底层渲染库 zrender
          if (id.includes('node_modules/echarts/') || id.includes('node_modules/zrender/')) {
            return 'echarts'
          }
          // element-plus 组件库 + 官方图标
          if (id.includes('node_modules/element-plus/') || id.includes('node_modules/@element-plus/')) {
            return 'element-plus'
          }
          // vue 核心生态(vue/@vue/runtime 等、vue-router、pinia)
          if (
            id.includes('node_modules/vue/')
            || id.includes('node_modules/@vue/')
            || id.includes('node_modules/vue-router/')
            || id.includes('node_modules/pinia/')
          ) {
            return 'vue-vendor'
          }
          // dayjs 全站广泛使用, 独立小块
          if (id.includes('node_modules/dayjs/')) return 'dayjs'
          // 其余第三方(axios/marked/dompurify 等)统一 vendor
          return 'vendor'
        },
      },
    },
  },
})
