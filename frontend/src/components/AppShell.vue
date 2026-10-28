<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const navItems = [
  { name: 'today', label: '今日', icon: '◎' },
  { name: 'ingest', label: '投喂', icon: '↥' },
  { name: 'persons', label: '人', icon: '☺' },
  { name: 'confirm', label: '确认', icon: '✓' },
  { name: 'calendar', label: '日历', icon: '▦' },
  { name: 'todos', label: '待办', icon: '☑' },
]

const active = computed(() => String(route.name ?? 'today'))

async function signOut() {
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="min-h-full bg-bg text-ink">
    <!-- 桌面：左侧栏 -->
    <aside class="fixed inset-y-0 left-0 hidden w-56 flex-col border-r border-border bg-bg-alt/60 px-4 py-6 lg:flex">
      <div class="px-2">
        <div class="text-lg font-semibold tracking-wide">Keeper</div>
        <div class="text-xs text-ink-soft">私域关系维护</div>
      </div>

      <nav class="mt-8 flex flex-col gap-1">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="flex items-center gap-3 rounded-xl px-3 py-2 text-sm text-ink-soft transition hover:bg-surface"
          :class="active === item.name ? 'bg-surface font-medium text-ink shadow-sm' : ''"
        >
          <span class="w-4 text-center">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="mt-auto space-y-2 px-2 text-sm">
        <div class="text-ink-soft">{{ auth.user?.display_name || auth.user?.username }}</div>
        <RouterLink :to="{ name: 'settings' }" class="block text-ink-soft hover:text-ink">
          设置
        </RouterLink>
        <button class="text-ink-soft hover:text-ink" @click="signOut">退出</button>
      </div>
    </aside>

    <!-- 手机：顶栏 -->
    <header class="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-bg/90 px-4 py-3 backdrop-blur lg:hidden">
      <div class="text-base font-semibold">Keeper</div>
      <RouterLink :to="{ name: 'settings' }" class="text-sm text-ink-soft">设置</RouterLink>
    </header>

    <main class="px-4 pt-4 pb-24 lg:ml-56 lg:px-10 lg:pt-10 lg:pb-16">
      <slot />
    </main>

    <!-- 手机：底部 tab -->
    <nav class="fixed inset-x-0 bottom-0 z-10 flex border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
      <RouterLink
        v-for="item in navItems"
        :key="item.name"
        :to="{ name: item.name }"
        class="flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] text-ink-soft"
        :class="active === item.name ? 'text-accent' : ''"
      >
        <span class="text-base leading-none">{{ item.icon }}</span>
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>
  </div>
</template>
