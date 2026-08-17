import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
})

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
        // 用 localLogout 而非 logout: 此时 token 已失效, 调 /auth/logout 会再 401 形成递归
        authStore.localLogout()
        router.push('/login')
        ElMessage.error('登录已过期,请重新登录')
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
