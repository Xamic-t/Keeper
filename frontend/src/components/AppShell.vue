<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 助理是**一级菜单**（2026-11-03 用户明确要求）：桌面和手机都要有。
// 之前它只在桌面侧栏出现、手机上要从设置绕进去，等于没有。
const navItems = [
  { name: 'today', label: '今日', icon: '◎' },
  { name: 'ingest', label: '投喂', icon: '↥' },
  { name: 'assistant', label: '助理', icon: '✦' },
  { name: 'persons', label: '人', icon: '☺' },
  { name: 'confirm', label: '确认', icon: '✓' },
  { name: 'calendar', label: '日历', icon: '▦' },
  { name: 'todos', label: '待办', icon: '☑' },
]
// 管理后台**不混在业务菜单里**（2026-11-03 用户明确要求）：它是独立界面，
// 入口放在左下角账号区，和「设置」并列但单独一行、带分隔线。
const isOwner = computed(() => auth.user?.role === 'owner')

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

      <div class="mt-auto px-2 text-sm">
        <div class="mb-2 text-ink-soft">{{ auth.user?.display_name || auth.user?.username }}</div>
        <RouterLink :to="{ name: 'settings' }" class="block py-1 text-ink-soft hover:text-ink">
          设置
        </RouterLink>
        <button class="block py-1 text-ink-soft hover:text-ink" @click="signOut">退出</button>

        <!-- 管理员才有：单独一条，靠分隔线隔开，不和业务菜单混排 -->
        <RouterLink
          v-if="isOwner"
          :to="{ name: 'admin' }"
          class="mt-3 flex items-center gap-2 border-t border-border pt-3 text-ink-soft hover:text-ink"
        >
          <span class="w-4 text-center">⚙</span>
          <span>管理后台</span>
        </RouterLink>
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
