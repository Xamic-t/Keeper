<script setup lang="ts">
/**
 * 账号管理（仅 owner）。
 *
 * 后端 /api/admin/users 早就有了，但一直没界面；V1 补最小闭环：
 * 看账号、建号、禁用/启用、重置密码。删除账号刻意不做（要保留审计与数据归属）。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { AdminUserOut } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const users = ref<AdminUserOut[]>([])
const loading = ref(false)
const error = ref('')
const message = ref('')

const form = ref({ username: '', displayName: '', password: '', role: 'member' })
const creating = ref(false)
const busyId = ref<number | null>(null)
const resetId = ref<number | null>(null)
const newPassword = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    users.value = await api.get<AdminUserOut[]>('/admin/users')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '账号列表没取到'
  } finally {
    loading.value = false
  }
}

async function createUser() {
  error.value = ''
  message.value = ''
  if (!form.value.username.trim() || form.value.password.length < 8) {
    error.value = '登录名必填，初始密码至少 8 位'
    return
  }
  creating.value = true
  try {
    await api.post('/admin/users', {
      username: form.value.username.trim(),
      display_name: form.value.displayName.trim() || null,
      password: form.value.password,
      role: form.value.role,
    })
    form.value = { username: '', displayName: '', password: '', role: 'member' }
    message.value = '账号已创建'
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '建号失败'
  } finally {
    creating.value = false
  }
}

async function toggleActive(user: AdminUserOut) {
  error.value = ''
  message.value = ''
  busyId.value = user.id
  try {
    const updated = await api.patch<AdminUserOut>(`/admin/users/${user.id}`, {
      is_active: !user.is_active,
    })
    users.value = users.value.map((item) => (item.id === user.id ? updated : item))
    message.value = updated.is_active ? '账号已启用' : '账号已禁用'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '操作失败'
  } finally {
    busyId.value = null
  }
}

function startReset(user: AdminUserOut) {
  resetId.value = user.id
  newPassword.value = ''
  error.value = ''
  message.value = ''
}

async function resetPassword() {
  if (resetId.value === null) return
  if (newPassword.value.length < 8) {
    error.value = '新密码至少 8 位'
    return
  }
  busyId.value = resetId.value
  error.value = ''
  try {
    await api.patch(`/admin/users/${resetId.value}`, { password: newPassword.value })
    message.value = '密码已重置，请通知对方'
    resetId.value = null
    newPassword.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : '重置失败'
  } finally {
    busyId.value = null
  }
}

function lastLogin(value: string | null) {
  if (!value) return '从未登录'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  if (auth.user?.role !== 'owner') {
    error.value = '只有 owner 能管理账号'
    void router.replace({ name: 'today' })
    return
  }
  void load()
})
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <PageHeader title="账号" subtitle="只有 owner 能进 · 建号与禁用" />

    <p v-if="error" class="mb-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
      {{ error }}
    </p>
    <p v-if="message" class="mb-4 rounded-lg bg-accent-soft px-3 py-2 text-sm text-accent-deep">
      {{ message }}
    </p>

    <form class="card mb-5 space-y-3 p-4" @submit.prevent="createUser">
      <div class="text-sm font-medium">新建账号</div>
      <div class="grid gap-2 sm:grid-cols-2">
        <input v-model="form.username" class="input" placeholder="登录名 / 手机号 / 邮箱" />
        <input v-model="form.displayName" class="input" placeholder="显示名（可空）" />
        <input v-model="form.password" class="input" type="password" placeholder="初始密码（≥8 位）" />
        <select v-model="form.role" class="input">
          <option value="member">member</option>
          <option value="owner">owner</option>
        </select>
      </div>
      <button class="btn btn-primary" type="submit" :disabled="creating">
        {{ creating ? '创建中…' : '创建账号' }}
      </button>
    </form>

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <div v-else class="space-y-2">
      <div v-for="user in users" :key="user.id" class="card px-4 py-3">
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-medium">{{ user.display_name || user.username }}</span>
          <span class="tag">{{ user.role }}</span>
          <span :class="user.is_active ? 'tag' : 'tag text-danger'">
            {{ user.is_active ? '启用' : '已禁用' }}
          </span>
          <span v-if="user.id === auth.user?.id" class="tag">我自己</span>
        </div>
        <div class="mt-1 text-xs text-ink-soft">
          {{ user.username }} · 最后登录 {{ lastLogin(user.last_login_at) }}
        </div>

        <div v-if="resetId === user.id" class="mt-3 flex flex-wrap gap-2">
          <input
            v-model="newPassword"
            class="input min-w-0 flex-1"
            type="password"
            placeholder="新密码（≥8 位）"
          />
          <button class="btn btn-primary" :disabled="busyId !== null" @click="resetPassword">
            确认重置
          </button>
          <button class="btn btn-ghost" @click="resetId = null">取消</button>
        </div>
        <div v-else class="mt-3 flex gap-2">
          <button
            class="btn btn-ghost"
            :disabled="busyId !== null || user.id === auth.user?.id"
            @click="toggleActive(user)"
          >
            {{ user.is_active ? '禁用' : '启用' }}
          </button>
          <button class="btn btn-ghost" :disabled="busyId !== null" @click="startReset(user)">
            重置密码
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
