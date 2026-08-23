import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import './assets/main.css'

const app = createApp(App)

// 注册所有 ElementPlus 图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// ---------- 构建后旧 chunk 引用失效的自愈 ----------
// 前端重新构建后, 缓存了旧 index.html 的浏览器会引用已被删除的旧 chunk,
// 导致点击菜单时路由懒加载 404 无响应。这里在 chunk 加载失败时自动刷新一次,
// 30s 内最多刷新一次, 避免刷新循环。
const CHUNK_RELOAD_KEY = 'chunk_error_reload_ts'
function tryAutoReload() {
  const last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0)
  if (Date.now() - last > 30 * 1000) {
    sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()))
    window.location.reload()
  }
}
// 路由懒加载 chunk 失败(vue-router 动态 import)
router.onError((error) => {
  const msg = (error && error.message) || ''
  if (/Failed to fetch dynamically imported module|Importing a module script failed/i.test(msg)) {
    tryAutoReload()
  }
})
// 入口预加载 chunk 失败(vite:preloadError)
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  tryAutoReload()
})

app.mount('#app')
