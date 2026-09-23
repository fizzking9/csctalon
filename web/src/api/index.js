import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
})

// 反向跨子域 SSO(netmgr 面板 → 本系统)启用开关。
// 应用启动时由 fetchSsoConfig() 预取; 默认 false —— 未确认两侧(面板 /sso/csctalon
// 签发端 + 本侧密钥)就绪前绝不外跳(2026-09-23 事故: 面板无该端点, JWT 过期用户被
// 自动甩到 https://panel.kimirock.top/sso/csctalon 的 Express 404)。
let netmgrSsoEnabled = false

export async function fetchSsoConfig() {
  try {
    // 用裸 axios, 不走 request 拦截器(此时可能正是未登录态, 避免递归触发 401 分流)
    const res = await axios.get('/api/v1/auth/sso-config')
    netmgrSsoEnabled = !!res.data?.data?.netmgr_sso_enabled
  } catch {
    netmgrSsoEnabled = false
  }
  return netmgrSsoEnabled
}

// 请求拦截:附加 token
request.interceptors.request.use((config) => {
  const authStore = useAuthStore()
  if (authStore.token) {
    config.headers.Authorization = `Bearer ${authStore.token}`
  }
  return config
})

// 响应拦截:统一处理错误
request.interceptors.response.use(
  (response) => {
    const data = response.data
    if (data && data.code !== undefined && data.code !== 0) {
      if (!response.config?.silentError) {
        ElMessage.error(data.message || '请求失败')
      }
      return Promise.reject(new Error(data.message || '请求失败'))
    }
    return data
  },
  (error) => {
    const silent = !!error.config?.silentError
    // 1. 主动取消 (AbortController) — 静默, 不弹任何提示
    if (error.code === 'ERR_CANCELED' || error.name === 'CanceledError') {
      return Promise.reject(error)
    }
    if (error.response) {
      const status = error.response.status
      // detail 可能是字符串、数组(FastAPI 422验证错误)或对象,统一转为字符串
      let detail = error.response.data?.detail || error.response.data?.message || '请求错误'
      if (Array.isArray(detail)) {
        // FastAPI 422 验证错误: [{type, loc, msg, ...}], 提取可读信息
        detail = detail.map((e) => e?.msg || String(e)).join('; ') || '请求参数错误'
      } else if (typeof detail === 'object') {
        detail = detail?.msg || JSON.stringify(detail)
      }
      if (status === 401) {
        const authStore = useAuthStore()
        const currentPath = window.location.pathname + window.location.search + window.location.hash
        if (currentPath.startsWith('/login')) {
          // 在 /login 页不跳任何续期链路(避免死循环), 仅清本地态
          authStore.localLogout()
          if (!silent) ElMessage.error('登录已过期,请重新登录')
        } else if (netmgrSsoEnabled) {
          // 跨子域 SSO 已启用(面板侧签发端就绪): JWT 过期自动跳 /api/v1/auth/sso,
          // 面板有会话 → 签新票 302 回来验票发新 JWT → 无感续期
          window.location.href = `/api/v1/auth/sso?next=${encodeURIComponent(currentPath)}`
        } else {
          // SSO 未启用: 清本地态回登录页, 登录后跳回原路径
          authStore.localLogout()
          window.location.href = `/login?expired=1&redirect=${encodeURIComponent(currentPath)}`
        }
      } else if (!silent) {
        ElMessage.error(String(detail))
      }
    } else if (!silent) {
      // 区分超时 / 网络错误, 给用户更准确的提示
      if (error.code === 'ECONNABORTED') {
        ElMessage.error('请求超时,后端处理时间过长,请稍后重试或缩小查询范围')
      } else {
        ElMessage.error('网络异常,请检查后端服务是否启动')
      }
    }
    return Promise.reject(error)
  }
)

export default request
