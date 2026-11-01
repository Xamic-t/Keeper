<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { TodoOut } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const todos = ref<TodoOut[]>([])
const loading = ref(true)
const title = ref('')
const creating = ref(false)
/** 出错要说话。以前这里没有 catch，接口一错就是静默白屏（评审 P3-1）。 */
const error = ref('')

function fail(err: unknown, fallback: string) {
  error.value = err instanceof Error ? err.message : fallback
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    todos.value = await api.get<TodoOut[]>('/todos', { status: 'open' })
  } catch (err) {
    fail(err, '待办没取到，刷新试试')
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!title.value.trim() || creating.value) return
  creating.value = true
  error.value = ''
  try {
    await api.post('/todos', { title: title.value.trim() })
    title.value = ''
    await load()
  } catch (err) {
    fail(err, '没加成功')
  } finally {
    creating.value = false
  }
}

async function toggle(todo: TodoOut) {
  error.value = ''
  try {
    await api.patch(`/todos/${todo.id}`, { status: 'done' })
    await load()
  } catch (err) {
    fail(err, '勾选没保存')
  }
}

async function remove(todo: TodoOut) {
  error.value = ''
  try {
    await api.delete(`/todos/${todo.id}`)
    await load()
  } catch (err) {
    fail(err, '没删掉')
  }
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <PageHeader title="待办" subtitle="想到什么记什么" />

    <form class="mb-4 flex gap-2" @submit.prevent="create">
      <input v-model="title" class="input" placeholder="要做什么？" maxlength="200" />
      <button class="btn btn-primary shrink-0" type="submit" :disabled="creating">添加</button>
    </form>

    <p v-if="error" class="mb-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
      {{ error }}
    </p>

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <div v-else-if="todos.length" class="card divide-y divide-border">
      <div v-for="todo in todos" :key="todo.id" class="flex items-center gap-3 px-4 py-3">
        <button
          class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border-strong text-[11px] text-transparent hover:border-success hover:text-success"
          @click="toggle(todo)"
        >
          ✓
        </button>
        <div class="min-w-0 flex-1">
          <div class="text-sm">{{ todo.title }}</div>
          <div v-if="todo.detail" class="truncate text-xs text-ink-soft">{{ todo.detail }}</div>
          <div v-if="todo.ai_reason" class="truncate text-xs text-ink-soft">依据：{{ todo.ai_reason }}</div>
        </div>
        <span v-if="todo.due_date" class="tag">{{ todo.due_date }}</span>
        <button class="text-xs text-ink-soft hover:text-accent-deep" @click="remove(todo)">删除</button>
      </div>
    </div>

    <EmptyState v-else title="没有待办" hint="今天的事都做完了" />
  </div>
</template>
