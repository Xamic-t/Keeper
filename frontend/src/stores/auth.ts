/** 登录态。用户信息在 Pinia 里，cookie 由后端管理。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { UserOut } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserOut | null>(null)
  const checked = ref(false)

  async function login(username: string, password: string) {
    const data = await api.post<{ user: UserOut; csrf_token: string }>('/auth/login', {
      username,
      password,
    })
    user.value = data.user
    checked.value = true
    return data.user
  }

  async function logout() {
    try {
      await api.post('/auth/logout')
    } finally {
      user.value = null
    }
  }

  /** 启动时校验登录态：调 /auth/me，401 就是没登录。 */
  async function ensureSession() {
    if (checked.value) return user.value
    try {
      const me = await api.get<UserOut>('/auth/me')
      user.value = me
    } catch {
      user.value = null
    } finally {
      checked.value = true
    }
    return user.value
  }

  return { user, checked, login, logout, ensureSession }
})
