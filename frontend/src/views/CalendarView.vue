<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { CalendarItem } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const today = new Date()
const month = ref(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`)
const items = ref<CalendarItem[]>([])
const loading = ref(false)

const typeLabels: Record<string, string> = {
  birthday: '生日',
  festival: '节日',
  solar_term: '节气',
  payment_due: '结款',
  custom: '自定义',
}

function range(monthValue: string) {
  const [year, mon] = monthValue.split('-').map(Number)
  const first = new Date(year, mon - 1, 1)
  const last = new Date(year, mon, 0)
  const pad = (n: number) => String(n).padStart(2, '0')
  return {
    from: `${year}-${pad(mon)}-01`,
    to: `${year}-${pad(mon)}-${pad(last.getDate())}`,
  }
}

async function load() {
  loading.value = true
  try {
    items.value = await api.get<CalendarItem[]>('/events', range(month.value))
  } finally {
    loading.value = false
  }
}

const grouped = () => {
  const map = new Map<string, CalendarItem[]>()
  items.value.forEach((item) => {
    const list = map.get(item.day) ?? []
    list.push(item)
    map.set(item.day, list)
  })
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]))
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <PageHeader title="日历" subtitle="生日、节日、节气、结款都在这儿" />

    <div class="mb-4 flex items-center gap-2">
      <input v-model="month" type="month" class="input" @change="load" />
      <button class="btn btn-ghost shrink-0" @click="load">刷新</button>
    </div>

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <div v-else-if="grouped().length" class="space-y-4">
      <section v-for="[day, list] in grouped()" :key="day">
        <div class="mb-1 text-sm font-medium text-ink-soft">{{ day }}</div>
        <div class="card divide-y divide-border">
          <div
            v-for="(item, index) in list"
            :key="index"
            class="flex items-center justify-between px-4 py-2.5 text-sm"
          >
            <span>{{ item.title }}</span>
            <span class="tag" :class="item.type === 'birthday' ? 'bg-accent-soft text-accent-deep' : ''">
              {{ typeLabels[item.type] ?? item.type }}{{ item.lunar ? '（农历）' : '' }}
            </span>
          </div>
        </div>
      </section>
    </div>

    <EmptyState v-else title="这个月没有安排" />
  </div>
</template>
