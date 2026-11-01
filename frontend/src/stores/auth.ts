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

  /** 清本地登录态。401 时由 client 的回调调用，logout 也走这里。 */
  function reset() {
    user.value = null
    // checked 保持 true：会话确实无效，没必要再拿 /auth/me 试一次
    checked.value = true
  }

  async function logout() {
    try {
      await api.post('/auth/logout')
    } finally {
      reset()
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

  return { user, checked, login, logout, ensureSession, reset }
})
