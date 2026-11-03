<script setup lang="ts">
/**
 * 管理后台（仅 owner）。2026-11-03 重做。
 *
 * 之前的问题（用户原话）：「账号管理不该和设置混在一起，应该是一个管理员后台」。
 * 所以这次不是给旧页面加几个数字，而是**换形态**：
 *   · 独立页面 /admin，有自己的侧边栏，不再挂在业务菜单里（业务菜单里那个「账号」已删）
 *   · 同一个登录页进来，是 owner 就有入口；不是 owner 直接踢回业务首页
 *   · 四个页签：概览 / 账号 / 行为 / 系统
 *   · 「行为」是新增能力：每个账号用了多少（人数/事实/待办/往来/提醒/投喂/对话）
 *
 * 刻意的边界：后台只显示**数量**，不显示任何人的内容。
 * 想看内容得登录那个账号——管理员的权力止于"知道有没有在用"。
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { AdminOverview, AdminUserOut, AdminUserStats } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import {
  formatNumber,
  formatRelative,
  isEmptyAccount,
  roleLabel,
  statusLabel,
} from '@/lib/adminFormat'

const router = useRouter()
const auth = useAuthStore()

type Tab = 'overview' | 'accounts' | 'behavior' | 'system'
const tab = ref<Tab>('overview')

const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: 'overview', label: '概览', hint: '系统与总量' },
  { key: 'accounts', label: '账号', hint: '建号 / 停用 / 重置密码' },
  { key: 'behavior', label: '行为', hint: '每个账号用了多少' },
  { key: 'system', label: '系统', hint: '版本与环境' },
]

const overview = ref<AdminOverview | null>(null)
const users = ref<AdminUserOut[]>([])
const loading = ref(true)
const error = ref('')
const message = ref('')
const health = ref<string>('')

const form = ref({ username: '', displayName: '', password: '', role: 'member' })
const creating = ref(false)
const busyId = ref<number | null>(null)
const resetId = ref<number | null>(null)
const newPassword = ref('')

const behaviorRows = computed<AdminUserStats[]>(() => {
  const rows = overview.value?.users ?? []
  // 活跃的排前面，从没登录过的沉底（后台最该先看到"在用的人"）
  return [...rows].sort((a, b) => {
    if (!a.last_active_at && !b.last_active_at) return 0
    if (!a.last_active_at) return 1
    if (!b.last_active_at) return -1
    return a.last_active_at < b.last_active_at ? 1 : -1
  })
})

const emptyAccounts = computed(() =>
  behaviorRows.value.filter((row) =>
    isEmptyAccount({
      persons: row.persons,
      facts: row.facts,
      todos_open: row.todos_open,
      ledger: row.ledger,
      raw_inputs: row.raw_inputs,
      chat_messages: row.chat_messages,
    }),
  ),
)

async function guard<T>(action: () => Promise<T>, fallback: string): Promise<T | null> {
  error.value = ''
  try {
    return await action()
  } catch (err) {
    error.value = err instanceof Error ? err.message : fallback
    return null
  }
}

async function loadAll() {
  loading.value = true
  const data = await guard(() => api.get<AdminOverview>('/admin/overview'), '后台数据没取到')
  if (data) overview.value = data
  const list = await guard(() => api.get<AdminUserOut[]>('/admin/users'), '账号列表没取到')
  if (list) users.value = list
  const info = await guard(() => api.get<{ status: string }>('/health'), '健康检查没返回')
  health.value = info?.status ?? 'unknown'
  loading.value = false
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
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '建号失败'
  } finally {
    creating.value = false
  }
}

async function toggleActive(user: AdminUserOut) {
  busyId.value = user.id
  error.value = ''
  message.value = ''
  try {
    await api.patch(`/admin/users/${user.id}`, { is_active: !user.is_active })
    message.value = user.is_active ? `已停用 ${user.username}` : `已启用 ${user.username}`
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '改状态失败'
  } finally {
    busyId.value = null
  }
}

async function resetPassword(user: AdminUserOut) {
  if (newPassword.value.length < 8) {
    error.value = '新密码至少 8 位'
    return
  }
  busyId.value = user.id
  error.value = ''
  try {
    await api.patch(`/admin/users/${user.id}`, { password: newPassword.value })
    message.value = `${user.username} 的密码已重置`
    resetId.value = null
    newPassword.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : '重置失败'
  } finally {
    busyId.value = null
  }
}

function backToApp() {
  router.push({ name: 'today' })
}

async function signOut() {
  await auth.logout()
  router.push({ name: 'login' })
}

onMounted(loadAll)
</script>

<template>
  <div class="min-h-screen bg-bg text-ink">
    <div class="flex min-h-screen">
      <!-- 后台自己的侧边栏：和业务菜单彻底分开 -->
      <aside
        class="hidden w-56 shrink-0 flex-col border-r border-border bg-bg-alt/70 px-3 py-6 lg:flex"
      >
        <div class="px-2">
          <div class="text-xs tracking-[0.2em] text-ink-soft">KEEPER</div>
          <div class="mt-1 text-lg font-semibold">管理后台</div>
        </div>

        <nav class="mt-7 flex flex-col gap-1">
          <button
            v-for="item in TABS"
            :key="item.key"
            class="rounded-xl px-3 py-2 text-left text-sm transition hover:bg-surface"
            :class="tab === item.key ? 'bg-surface font-medium shadow-sm' : 'text-ink-soft'"
            @click="tab = item.key"
          >
            <div>{{ item.label }}</div>
            <div class="mt-0.5 text-[11px] text-ink-soft">{{ item.hint }}</div>
          </button>
        </nav>

        <div class="mt-auto space-y-2 px-2 pt-6 text-sm">
          <div class="text-xs text-ink-soft">{{ auth.user?.display_name || auth.user?.username }}</div>
          <button class="block text-ink-soft hover:text-ink" @click="backToApp">← 返回应用</button>
          <button class="block text-ink-soft hover:text-ink" @click="signOut">退出登录</button>
        </div>
      </aside>

      <main class="min-w-0 flex-1 px-4 py-5 lg:px-10 lg:py-8">
        <!-- 手机：顶部标题 + 横向页签 -->
        <header class="mb-4 flex items-center justify-between lg:hidden">
          <div class="text-base font-semibold">管理后台</div>
          <div class="flex gap-3 text-sm">
            <button class="text-ink-soft" @click="backToApp">返回应用</button>
            <button class="text-ink-soft" @click="signOut">退出</button>
          </div>
        </header>
        <div class="mb-4 flex gap-2 overflow-x-auto lg:hidden">
          <button
            v-for="item in TABS"
            :key="item.key"
            class="shrink-0 rounded-full border border-border px-3 py-1 text-xs"
            :class="tab === item.key ? 'bg-surface font-medium' : 'text-ink-soft'"
            @click="tab = item.key"
          >
            {{ item.label }}
          </button>
        </div>

        <p v-if="error" class="mb-3 rounded-xl bg-surface px-3 py-2 text-sm text-accent">{{ error }}</p>
        <p v-if="message" class="mb-3 rounded-xl bg-surface px-3 py-2 text-sm">{{ message }}</p>
        <p v-if="loading" class="text-sm text-ink-soft">正在读数据…</p>

        <template v-else-if="overview">
          <!-- 概览 -->
          <section v-show="tab === 'overview'" class="space-y-5">
            <h1 class="text-xl font-semibold tracking-tight">概览</h1>
            <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <div class="rounded-2xl border border-border bg-surface p-4">
                <div class="text-xs text-ink-soft">账号</div>
                <div class="mt-1 text-2xl font-semibold">{{ formatNumber(overview.accounts.total) }}</div>
                <div class="mt-1 text-xs text-ink-soft">
                  管理员 {{ overview.accounts.owners }} · 停用 {{ overview.accounts.disabled }}
                </div>
              </div>
              <div class="rounded-2xl border border-border bg-surface p-4">
                <div class="text-xs text-ink-soft">联系人</div>
                <div class="mt-1 text-2xl font-semibold">{{ formatNumber(overview.totals.persons) }}</div>
                <div class="mt-1 text-xs text-ink-soft">全站合计</div>
              </div>
              <div class="rounded-2xl border border-border bg-surface p-4">
                <div class="text-xs text-ink-soft">已确认事实</div>
                <div class="mt-1 text-2xl font-semibold">{{ formatNumber(overview.totals.facts) }}</div>
                <div class="mt-1 text-xs text-ink-soft">
                  待确认 {{ formatNumber(overview.totals.pending_confirmations) }}
                </div>
              </div>
              <div class="rounded-2xl border border-border bg-surface p-4">
                <div class="text-xs text-ink-soft">未完成待办</div>
                <div class="mt-1 text-2xl font-semibold">{{ formatNumber(overview.totals.todos_open) }}</div>
                <div class="mt-1 text-xs text-ink-soft">
                  投喂 {{ formatNumber(overview.totals.raw_inputs) }}
                </div>
              </div>
            </div>

            <div class="rounded-2xl border border-border bg-surface p-4">
              <div class="text-sm font-medium">谁在用</div>
              <p v-if="emptyAccounts.length" class="mt-1 text-xs text-ink-soft">
                有 {{ emptyAccounts.length }} 个账号建了但一条数据都没有：
                {{ emptyAccounts.map((item) => item.username).join('、') }}
              </p>
              <ul class="mt-3 space-y-2">
                <li
                  v-for="row in behaviorRows.slice(0, 5)"
                  :key="row.id ?? row.username"
                  class="flex items-center justify-between text-sm"
                >
                  <span>
                    {{ row.username }}
                    <span class="ml-1 text-xs text-ink-soft">{{ roleLabel(row.role) }}</span>
                  </span>
                  <span class="text-xs text-ink-soft">{{ formatRelative(row.last_active_at) }}</span>
                </li>
              </ul>
            </div>
          </section>

          <!-- 账号 -->
          <section v-show="tab === 'accounts'" class="space-y-5">
            <h1 class="text-xl font-semibold tracking-tight">账号</h1>

            <div class="rounded-2xl border border-border bg-surface p-4">
              <div class="text-sm font-medium">新建账号</div>
              <p class="mt-1 text-xs text-ink-soft">
                系统是邀请制，没有注册入口。初始密码必须由你指定，仓库里不留默认口令。
              </p>
              <div class="mt-3 grid gap-2 lg:grid-cols-4">
                <input v-model="form.username" class="input" placeholder="登录名" />
                <input v-model="form.displayName" class="input" placeholder="显示名（可空）" />
                <input v-model="form.password" class="input" type="password" placeholder="初始密码 ≥8 位" />
                <select v-model="form.role" class="input">
                  <option value="member">成员</option>
                  <option value="owner">管理员</option>
                </select>
              </div>
              <button class="btn btn-primary mt-3" :disabled="creating" @click="createUser">
                {{ creating ? '创建中…' : '创建' }}
              </button>
            </div>

            <div class="overflow-x-auto rounded-2xl border border-border">
              <table class="w-full min-w-[640px] text-sm">
                <thead class="bg-surface text-xs text-ink-soft">
                  <tr>
                    <th class="px-3 py-2 text-left">账号</th>
                    <th class="px-3 py-2 text-left">角色</th>
                    <th class="px-3 py-2 text-left">状态</th>
                    <th class="px-3 py-2 text-left">最后登录</th>
                    <th class="px-3 py-2 text-left">创建</th>
                    <th class="px-3 py-2 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="user in users" :key="user.id" class="border-t border-border">
                    <td class="px-3 py-2">
                      <div>{{ user.display_name || user.username }}</div>
                      <div class="text-xs text-ink-soft">{{ user.username }}</div>
                    </td>
                    <td class="px-3 py-2 text-ink-soft">{{ roleLabel(user.role) }}</td>
                    <td class="px-3 py-2">
                      <span :class="user.is_active ? '' : 'text-ink-soft'">{{ statusLabel(user.is_active) }}</span>
                    </td>
                    <td class="px-3 py-2 text-ink-soft">{{ formatRelative(user.last_login_at) }}</td>
                    <td class="px-3 py-2 text-xs text-ink-soft">{{ formatRelative(user.created_at) }}</td>
                    <td class="px-3 py-2 text-right">
                      <template v-if="resetId === user.id">
                        <input v-model="newPassword" class="input mr-2 inline-block w-36" type="password" placeholder="新密码 ≥8 位" />
                        <button class="text-accent" :disabled="busyId === user.id" @click="resetPassword(user)">保存</button>
                        <button class="ml-2 text-ink-soft" @click="resetId = null">取消</button>
                      </template>
                      <template v-else>
                        <button class="text-ink-soft hover:text-ink" @click="resetId = user.id">重置密码</button>
                        <button
                          class="ml-3"
                          :class="user.is_active ? 'text-accent' : 'text-ink-soft hover:text-ink'"
                          :disabled="busyId === user.id"
                          @click="toggleActive(user)"
                        >
                          {{ user.is_active ? '停用' : '启用' }}
                        </button>
                      </template>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- 行为 -->
          <section v-show="tab === 'behavior'" class="space-y-4">
            <h1 class="text-xl font-semibold tracking-tight">用户行为</h1>
            <p class="text-xs text-ink-soft">
              只有数量，没有内容——想看某人记了什么，用他的账号登录。后台的边界到此为止。
            </p>
            <div class="overflow-x-auto rounded-2xl border border-border">
              <table class="w-full min-w-[860px] text-sm">
                <thead class="bg-surface text-xs text-ink-soft">
                  <tr>
                    <th class="px-3 py-2 text-left">账号</th>
                    <th class="px-3 py-2 text-right">联系人</th>
                    <th class="px-3 py-2 text-right">事实</th>
                    <th class="px-3 py-2 text-right">待确认</th>
                    <th class="px-3 py-2 text-right">待办</th>
                    <th class="px-3 py-2 text-right">往来</th>
                    <th class="px-3 py-2 text-right">提醒已发</th>
                    <th class="px-3 py-2 text-right">投喂</th>
                    <th class="px-3 py-2 text-right">对话</th>
                    <th class="px-3 py-2 text-left">最近活跃</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in behaviorRows" :key="row.id ?? row.username" class="border-t border-border">
                    <td class="px-3 py-2">
                      {{ row.username }}
                      <span v-if="!row.is_active" class="ml-1 text-xs text-ink-soft">（停用）</span>
                    </td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.persons) }}</td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.facts) }}</td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.pending_confirmations) }}</td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.todos_open) }}</td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.ledger) }}</td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.reminders_sent) }}</td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.raw_inputs) }}</td>
                    <td class="px-3 py-2 text-right">{{ formatNumber(row.chat_messages) }}</td>
                    <td class="px-3 py-2 text-xs text-ink-soft">{{ formatRelative(row.last_active_at) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- 系统 -->
          <section v-show="tab === 'system'" class="space-y-4">
            <h1 class="text-xl font-semibold tracking-tight">系统</h1>
            <div class="grid gap-3 lg:grid-cols-2">
              <div class="rounded-2xl border border-border bg-surface p-4 text-sm">
                <div class="mb-2 font-medium">运行环境</div>
                <dl class="space-y-1 text-ink-soft">
                  <div class="flex justify-between"><dt>环境</dt><dd>{{ overview.system.app_env }}</dd></div>
                  <div class="flex justify-between"><dt>版本</dt><dd>{{ overview.system.app_version }}</dd></div>
                  <div class="flex justify-between"><dt>业务日期</dt><dd>{{ overview.system.today }}</dd></div>
                  <div class="flex justify-between"><dt>数据库</dt><dd>{{ health }}</dd></div>
                </dl>
              </div>
              <div class="rounded-2xl border border-border bg-surface p-4 text-sm">
                <div class="mb-2 font-medium">数据库迁移</div>
                <p class="text-ink-soft">
                  当前版本 <span class="text-ink">{{ overview.system.migration || '未知' }}</span>
                </p>
                <p class="mt-2 text-xs text-ink-soft">
                  部署或升级后先看这里：和代码里的迁移不一致，说明升级没跑完。
                </p>
              </div>
            </div>
          </section>
        </template>
      </main>
    </div>
  </div>
</template>
