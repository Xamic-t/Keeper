<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  if (!username.value || !password.value) return
  busy.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.push(redirect)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '登录失败，请稍后再试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex min-h-full items-center justify-center bg-bg px-5">
    <div class="w-full max-w-sm">
      <div class="mb-8 text-center">
        <div class="text-2xl font-semibold tracking-wide">Keeper</div>
        <p class="mt-1 text-sm text-ink-soft">投喂、记住、判断、提醒</p>
      </div>

      <form class="card space-y-4 p-6" @submit.prevent="submit">
        <div>
          <label class="mb-1 block text-sm text-ink-soft" for="username">账号</label>
          <input
            id="username"
            v-model="username"
            class="input"
            autocomplete="username"
            placeholder="用户名 / 手机号 / 邮箱"
          />
        </div>
        <div>
          <label class="mb-1 block text-sm text-ink-soft" for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            class="input"
            autocomplete="current-password"
            placeholder="••••••"
          />
        </div>

        <p v-if="error" class="rounded-lg bg-accent-soft px-3 py-2 text-sm text-accent-deep">
          {{ error }}
        </p>

        <button class="btn btn-primary w-full" type="submit" :disabled="busy">
          {{ busy ? '登录中…' : '登录' }}
        </button>
      </form>

      <p class="mt-4 text-center text-xs text-ink-soft">账号由管理员发放，忘记密码请联系管理员。</p>
    </div>
  </div>
</template>
