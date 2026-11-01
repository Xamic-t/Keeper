<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { TodayView } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
const view = ref<TodayView | null>(null)
const loading = ref(true)
const error = ref('')

/** 本地记录本次勾掉的项。勾完先留着（划线），刷新后才真正从列表消失 */
const doneKeys = ref<Set<string>>(new Set())
const busyKey = ref<string | null>(null)

function showError(err: unknown, fallback: string) {
  error.value = err instanceof Error ? err.message : fallback
}

async function load() {
  loading.value = true
  try {
    view.value = await api.get<TodayView>('/today')
    doneKeys.value = new Set()
  } catch (err) {
    showError(err, '今天的数据没取到，刷新试试')
  } finally {
    loading.value = false
  }
}

function keyOf(task: { type: string; source_id: number | null }) {
  return `${task.type}-${task.source_id}`
}

const pendingTasks = computed(() =>
  (view.value?.tasks ?? []).filter((task) => !doneKeys.value.has(keyOf(task))),
)
const doneTasks = computed(() =>
  (view.value?.tasks ?? []).filter((task) => doneKeys.value.has(keyOf(task))),
)
const total = computed(() => view.value?.tasks.length ?? 0)
const doneCount = computed(() => doneTasks.value.length)

/**
 * 勾掉一条。
 * 待办可以反悔（改回 open）；提醒不行——后端只是把它标成 clicked，
 * 没有回滚接口，所以提醒勾了就是勾了，不再给取消入口。
 */
async function toggleDone(task: {
  type: string
  source_id: number | null
  title: string
}) {
  if (!task.source_id || busyKey.value) return
  const key = keyOf(task)

  if (doneKeys.value.has(key)) {
    if (task.type !== 'todo') return
    busyKey.value = key
    try {
      await api.patch(`/todos/${task.source_id}`, { status: 'open' })
      const next = new Set(doneKeys.value)
      next.delete(key)
      doneKeys.value = next
    } catch (err) {
      showError(err, '改回未完成失败')
    } finally {
      busyKey.value = null
    }
    return
  }

  busyKey.value = key
  error.value = ''
  try {
    if (task.type === 'todo') {
      await api.patch(`/todos/${task.source_id}`, { status: 'done' })
    } else {
      await api.post(`/reminders/${task.source_id}/done`)
    }
    doneKeys.value = new Set(doneKeys.value).add(key)
  } catch (err) {
    showError(err, '标记完成失败')
  } finally {
    busyKey.value = null
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

function dueText(due: string | null) {
  if (!due) return ''
  const at = new Date(`${due}T00:00:00`)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((at.getTime() - today.getTime()) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff === -1) return '昨天'
  if (diff < 0) return `逾期 ${-diff} 天`
  return `${at.getMonth() + 1}月${at.getDate()}日`
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
        <div class="min-w-0">
          <div class="font-medium">有 {{ view.pending_count }} 条等你确认</div>
          <div class="truncate text-sm text-ink-soft">{{ view.pending_preview?.content ?? 'AI 抽出来的东西' }}</div>
        </div>
        <span class="shrink-0 text-accent">→</span>
      </RouterLink>

      <!-- 待办清单 -->
      <section class="mb-8">
        <div class="mb-2 flex items-baseline justify-between">
          <h2 class="text-sm font-medium text-ink-soft">今天要做</h2>
          <span v-if="total" class="text-xs text-ink-soft">{{ doneCount }} / {{ total }}</span>
        </div>

        <div v-if="total" class="card divide-y divide-border">
          <!-- 未完成 -->
          <div
            v-for="task in pendingTasks"
            :key="keyOf(task)"
            class="flex items-start gap-3 px-4 py-3"
          >
            <button
              class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border-strong text-[11px] text-transparent transition hover:border-success hover:text-success disabled:opacity-40"
              :disabled="busyKey !== null"
              :aria-label="`完成：${task.title}`"
              @click="toggleDone(task)"
            >
              ✓
            </button>

            <div class="min-w-0 flex-1">
              <div class="text-sm leading-relaxed">{{ task.title }}</div>
              <div v-if="task.subtitle" class="mt-0.5 truncate text-xs text-ink-soft">
                {{ task.subtitle }}
              </div>
              <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
                <span
                  v-if="task.type === 'reminder'"
                  class="rounded bg-bg-alt px-1.5 py-0.5 text-[11px]"
                >
                  提醒
                </span>
                <!-- 没推出去要说清楚：用户没收到手机通知，但事还是得做 -->
                <span
                  v-if="task.unsent"
                  class="rounded bg-danger-soft px-1.5 py-0.5 text-[11px] text-danger"
                  :title="task.unsent_reason ?? '没有推送出去'"
                >
                  未推送
                </span>
                <RouterLink
                  v-if="task.person_id"
                  :to="{ name: 'person', params: { id: task.person_id } }"
                  class="hover:text-accent"
                >
                  {{ task.subtitle || '看档案' }}
                </RouterLink>
                <span v-if="dueText(task.due)" :class="task.due && dueText(task.due).startsWith('逾期') ? 'text-warn' : ''">
                  {{ dueText(task.due) }}
                </span>
              </div>
            </div>

            <button
              v-if="task.type === 'reminder'"
              class="shrink-0 self-center text-xs text-ink-soft hover:text-accent"
              @click="toTodo(task.source_id)"
            >
              转待办
            </button>
          </div>

          <!-- 已完成 -->
          <div
            v-for="task in doneTasks"
            :key="keyOf(task)"
            class="flex items-start gap-3 bg-bg-alt/40 px-4 py-3"
          >
            <button
              class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-success bg-success/10 text-[11px] text-success"
              :disabled="busyKey !== null"
              :aria-label="`取消完成：${task.title}`"
              @click="toggleDone(task)"
            >
              ✓
            </button>
            <div class="min-w-0 flex-1">
              <div class="text-sm leading-relaxed text-ink-soft line-through">{{ task.title }}</div>
              <div v-if="task.subtitle" class="mt-0.5 truncate text-xs text-ink-soft/70">
                {{ task.subtitle }}
              </div>
            </div>
          </div>
        </div>

        <div v-else>
          <EmptyState title="今天没有安排" hint="去投喂几张截图，系统会替你记着" />
        </div>

        <p v-if="view.tasks_more > 0" class="mt-2 px-1 text-xs text-ink-soft">
          还有 {{ view.tasks_more }} 条没收进来，去「待办」里看全量。
        </p>
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
