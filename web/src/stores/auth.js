import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import request from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))

  const isLoggedIn = computed(() => !!token.value)
  const role = computed(() => user.value?.role || '')
  const isAdmin = computed(() => role.value === 'admin')
  const isSupervisor = computed(() => role.value === 'supervisor')
  const isSpecialtyLead = computed(() => role.value === 'specialty_lead')
  const isSpecialist = computed(() => role.value === 'specialist')
  const isAgent = computed(() => role.value === 'agent')
  // 管理层:admin + supervisor + specialty_lead(可看决策看板/管理闭环, 见规划设计 v0.5 §二)
  const isManager = computed(() => ['admin', 'supervisor', 'specialty_lead'].includes(role.value))
  const displayName = computed(() => user.value?.display_name || '用户')

  async function login(username, password) {
    const res = await request.post('/auth/login-json', { username, password })
    token.value = res.data.access_token
    user.value = res.data.user
    localStorage.setItem('token', token.value)
    localStorage.setItem('user', JSON.stringify(user.value))
    return res.data
  }

  // 仅清本地凭证(不调后端)。供 401 拦截器使用,避免递归调用 /auth/logout 又触发 401。
  function localLogout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  // 主动登出: 先调后端吊销 token(token_version+1 使所有会话失效), 再清本地。
  // 后端调用失败(网络/已 401)也继续清本地, 保证前端一定能登出。
  async function logout() {
    if (token.value) {
      try {
        await request.post('/auth/logout', {}, { silentError: true })
      } catch (_) {
        // 忽略: 后端可能已 401 或网络异常, 本地仍要清干净
      }
    }
    localLogout()
  }

  async function fetchMe() {
    const res = await request.get('/auth/me')
    user.value = res.data
    localStorage.setItem('user', JSON.stringify(user.value))
  }

  return {
    token, user, isLoggedIn, role, isAdmin, isSupervisor, isSpecialtyLead, isSpecialist, isAgent, isManager, displayName,
    login, logout, localLogout, fetchMe,
  }
})
