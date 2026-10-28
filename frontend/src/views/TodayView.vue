<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { TodayView } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
const view = ref<TodayView | null>(null)
const loading = ref(true)
const error = ref('')

function showError(err: unknown, fallback: string) {
  error.value = err instanceof Error ? err.message : fallback
}

async function load() {
  loading.value = true
  try {
    view.value = await api.get<TodayView>('/today')
  } catch (err) {
    showError(err, '今天的数据没取到，刷新试试')
  } finally {
    loading.value = false
  }
}

async function completeTodo(sourceId: number | null) {
  if (!sourceId) return
  error.value = ''
  try {
    await api.patch(`/todos/${sourceId}`, { status: 'done' })
    await load()
  } catch (err) {
    showError(err, '完成失败')
  }
}

async function toTodo(sourceId: number | null) {
  if (!sourceId) return
  error.value = ''
  try {
    await api.post(`/reminders/${sourceId}/to-todo`)
    await load()
  } catch (err) {
    showError(err, '转待办失败')
  }
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <PageHeader title="今天" :subtitle="view?.date ?? ''" />

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <p v-else-if="error" class="mb-3 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn">{{ error }}</p>

    <template v-else-if="view">
      <!-- 待确认 -->
      <RouterLink
        v-if="view.pending_count > 0"
        :to="{ name: 'confirm' }"
        class="card mb-4 flex items-center justify-between border-accent/40 bg-accent-soft/60 px-4 py-3"
      >
        <div>
          <div class="font-medium">有 {{ view.pending_count }} 条等你确认</div>
          <div class="text-sm text-ink-soft">{{ view.pending_preview?.content ?? 'AI 抽出来的东西' }}</div>
        </div>
        <span class="text-accent">→</span>
      </RouterLink>

      <!-- 任务 -->
      <section class="mb-6">
        <h2 class="mb-2 text-sm font-medium text-ink-soft">该做的事</h2>
        <div v-if="view.tasks.length" class="space-y-2">
          <div
            v-for="(task, index) in view.tasks"
            :key="`${task.type}-${task.source_id}-${index}`"
            class="card flex items-start gap-3 px-4 py-3"
          >
            <span
              class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px]"
              :class="task.type === 'reminder' ? 'border-accent text-accent' : 'border-border-strong text-ink-soft'"
            >
              {{ task.type === 'reminder' ? '!' : '·' }}
            </span>
            <div class="min-w-0 flex-1">
              <div class="font-medium">{{ task.title }}</div>
              <div v-if="task.subtitle" class="truncate text-sm text-ink-soft">{{ task.subtitle }}</div>
            </div>
            <button
              v-if="task.type === 'reminder'"
              class="shrink-0 text-sm text-ink-soft hover:text-accent"
              @click="toTodo(task.source_id)"
            >
              转待办
            </button>
            <button
              v-if="task.type === 'todo'"
              class="shrink-0 text-sm text-ink-soft hover:text-success"
              @click="completeTodo(task.source_id)"
            >
              完成
            </button>
          </div>
          <div v-if="view.tasks_more > 0" class="px-1 text-sm text-ink-soft">
            还有 {{ view.tasks_more }} 条，收在待办里
          </div>
        </div>
        <EmptyState v-else title="今天没有安排" hint="去投喂几张截图，系统会替你记着" />
      </section>

      <!-- 最近动态 -->
      <section>
        <h2 class="mb-2 text-sm font-medium text-ink-soft">最近记下的</h2>
        <div v-if="view.recent.length" class="card divide-y divide-border">
          <RouterLink
            v-for="(item, index) in view.recent"
            :key="index"
            :to="{ name: 'person', params: { id: item.person_id } }"
            class="block px-4 py-3 hover:bg-bg-alt/50"
          >
            <div class="text-sm text-ink-soft">{{ item.nickname }}</div>
            <div class="text-sm">{{ item.content }}</div>
          </RouterLink>
        </div>
        <EmptyState v-else title="还没有记录" hint="投喂后这里会显示你记住的事" />
      </section>

      <p v-if="view.degraded" class="mt-4 text-xs text-warn">
        部分数据没能取全，稍后刷新看看。
      </p>
    </template>

    <div v-else class="text-sm text-ink-soft">今天的数据没取到。</div>

    <div class="mt-8 text-center">
      <button class="btn btn-ghost" @click="router.push({ name: 'ingest' })">去投喂</button>
    </div>
  </div>
</template>
