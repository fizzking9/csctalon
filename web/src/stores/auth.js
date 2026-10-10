import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import request from '@/api'
import { AI_THREAD_KEY, AI_PENDING_KEY } from '@/constants/aiAssistantStorage'
// ★ #260 档位名单唯一来源是 config/access.js(ROLE_LADDER), 此处只派生, 禁止手写角色数组
import { roleAllowed } from '@/config/access'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))

  const isLoggedIn = computed(() => !!token.value)
  const role = computed(() => user.value?.role || '')
  const isAdmin = computed(() => roleAllowed('admin', role.value))
  const isSupervisor = computed(() => role.value === 'supervisor')
  const isSpecialtyLead = computed(() => role.value === 'specialty_lead')
  const isSpecialist = computed(() => role.value === 'specialist')
  const isAgent = computed(() => role.value === 'agent')
  // 管理层(可看决策看板/管理闭环): 档位定义见 config/access.js ROLE_LADDER.manager
  const isManager = computed(() => roleAllowed('manager', role.value))
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
    // 清理会话级缓存: AI 助手把「提问 + 分析结果」明文存在 sessionStorage
    // (用于抽屉关闭/刷新后续查, 见 AssistantDrawer.vue #66)。登出不清会把
    // 上一个账号的经营数据留给同一标签页的下一位使用者 → 必须一并清掉。
    try {
      sessionStorage.removeItem(AI_THREAD_KEY)
      sessionStorage.removeItem(AI_PENDING_KEY)
    } catch (_) {
      // 无痕模式/禁用 storage: 忽略, 不影响登出
    }
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
