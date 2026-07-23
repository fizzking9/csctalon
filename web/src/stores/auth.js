import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import request from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))

  const isLoggedIn = computed(() => !!token.value)
  const role = computed(() => user.value?.role || '')
  const isAdmin = computed(() => role.value === 'admin')
  const displayName = computed(() => user.value?.display_name || '用户')

  async function login(username, password) {
    const res = await request.post('/auth/login-json', { username, password })
    token.value = res.data.access_token
    user.value = res.data.user
    localStorage.setItem('token', token.value)
    localStorage.setItem('user', JSON.stringify(user.value))
    return res.data
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  async function fetchMe() {
    const res = await request.get('/auth/me')
    user.value = res.data
    localStorage.setItem('user', JSON.stringify(user.value))
  }

  return { token, user, isLoggedIn, role, isAdmin, displayName, login, logout, fetchMe }
})
