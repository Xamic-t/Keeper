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

async function load() {
  loading.value = true
  try {
    todos.value = await api.get<TodoOut[]>('/todos', { status: 'open' })
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!title.value.trim()) return
  await api.post('/todos', { title: title.value.trim() })
  title.value = ''
  await load()
}

async function toggle(todo: TodoOut) {
  await api.patch(`/todos/${todo.id}`, { status: 'done' })
  await load()
}

async function remove(todo: TodoOut) {
  await api.delete(`/todos/${todo.id}`)
  await load()
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
