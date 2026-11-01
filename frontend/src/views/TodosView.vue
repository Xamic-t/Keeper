<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { PersonListItem, TodoOut } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const todos = ref<TodoOut[]>([])
const persons = ref<PersonListItem[]>([])
const loading = ref(true)
const creating = ref(false)
/** 出错要说话。以前这里没有 catch，接口一错就是静默白屏（评审 P3-1）。 */
const error = ref('')

// 新建表单：后端一直支持 due_date / person_id / detail，界面以前只给了标题
const title = ref('')
const dueDate = ref('')
const personId = ref<number | ''>('')
const detail = ref('')
const showMore = ref(false)

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

async function loadPersons() {
  try {
    const payload = await api.raw<{ data: PersonListItem[] }>('/persons?size=100')
    persons.value = payload.data
  } catch {
    // 人物列表拿不到不影响记待办，静默即可
    persons.value = []
  }
}

async function create() {
  if (!title.value.trim() || creating.value) return
  creating.value = true
  error.value = ''
  try {
    await api.post('/todos', {
      title: title.value.trim(),
      // 空字符串不能发给后端（due_date 要 null 或 YYYY-MM-DD）
      due_date: dueDate.value || null,
      person_id: personId.value === '' ? null : personId.value,
      detail: detail.value.trim() || null,
    })
    title.value = ''
    dueDate.value = ''
    personId.value = ''
    detail.value = ''
    showMore.value = false
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

function personName(id: number | null) {
  if (id === null) return ''
  return persons.value.find((item) => item.id === id)?.nickname ?? ''
}

/** 今天/明天/逾期，比裸日期好读 */
function dueLabel(due: string | null) {
  if (!due) return ''
  const today = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const todayText = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`
  if (due === todayText) return '今天'
  const tomorrow = new Date(today.getTime() + 86400000)
  const tomorrowText = `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(
    tomorrow.getDate(),
  )}`
  if (due === tomorrowText) return '明天'
  return due < todayText ? `逾期 · ${due}` : due
}

function dueClass(due: string | null) {
  if (!due) return 'tag'
  return due < new Date().toISOString().slice(0, 10) ? 'tag text-danger' : 'tag'
}

onMounted(async () => {
  await Promise.all([load(), loadPersons()])
})
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <PageHeader title="待办" subtitle="想到什么记什么" />

    <form class="mb-4 space-y-2" @submit.prevent="create">
      <div class="flex gap-2">
        <input v-model="title" class="input" placeholder="要做什么？" maxlength="200" />
        <button class="btn btn-primary shrink-0" type="submit" :disabled="creating">添加</button>
      </div>

      <button
        type="button"
        class="text-xs text-ink-soft hover:text-accent"
        @click="showMore = !showMore"
      >
        {{ showMore ? '收起' : '加截止日 / 挂人 / 备注' }}
      </button>

      <div v-if="showMore" class="card space-y-2 p-3">
        <label class="block text-xs text-ink-soft">
          截止日
          <input v-model="dueDate" type="date" class="input mt-1" />
        </label>
        <label class="block text-xs text-ink-soft">
          跟谁有关
          <select v-model="personId" class="input mt-1">
            <option value="">不指定</option>
            <option v-for="person in persons" :key="person.id" :value="person.id">
              {{ person.nickname }}
            </option>
          </select>
        </label>
        <label class="block text-xs text-ink-soft">
          备注
          <input v-model="detail" class="input mt-1" maxlength="2000" placeholder="可空" />
        </label>
      </div>
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
          <div v-if="todo.ai_reason" class="truncate text-xs text-ink-soft">
            依据：{{ todo.ai_reason }}
          </div>
          <div class="mt-1 flex flex-wrap items-center gap-2">
            <span v-if="todo.due_date" :class="dueClass(todo.due_date)">
              {{ dueLabel(todo.due_date) }}
            </span>
            <RouterLink
              v-if="todo.person_id"
              :to="{ name: 'person', params: { id: todo.person_id } }"
              class="tag hover:text-accent"
            >
              {{ personName(todo.person_id) || '看档案' }}
            </RouterLink>
          </div>
        </div>
        <button class="text-xs text-ink-soft hover:text-accent-deep" @click="remove(todo)">
          删除
        </button>
      </div>
    </div>

    <EmptyState v-else title="没有待办" hint="今天的事都做完了" />
  </div>
</template>
