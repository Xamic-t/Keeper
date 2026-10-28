<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { PersonListItem } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
const items = ref<PersonListItem[]>([])
const total = ref(0)
const keyword = ref('')
const loading = ref(false)
const showCreate = ref(false)
const nickname = ref('')
const creating = ref(false)
const error = ref('')

const stageLabels: Record<string, string> = {
  new: '新识',
  familiar: '熟悉',
  core: '核心',
  cold: '冷淡',
}

async function load() {
  loading.value = true
  try {
    const payload = await api.raw<{ data: PersonListItem[]; meta: { total: number } }>(
      `/persons?size=100${keyword.value ? `&keyword=${encodeURIComponent(keyword.value)}` : ''}`,
    )
    items.value = payload.data
    total.value = payload.meta.total
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!nickname.value.trim()) return
  creating.value = true
  error.value = ''
  try {
    await api.post('/persons', { nickname: nickname.value.trim() })
    nickname.value = ''
    showCreate.value = false
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '创建失败'
  } finally {
    creating.value = false
  }
}

function search() {
  void load()
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <PageHeader title="人" :subtitle="`共 ${total} 位`" action-label="新建" @action="showCreate = true" />

    <div class="mb-4 flex gap-2">
      <input
        v-model="keyword"
        class="input"
        placeholder="搜昵称或别名"
        @keyup.enter="search"
      />
      <button class="btn btn-ghost shrink-0" @click="search">搜索</button>
    </div>

    <form v-if="showCreate" class="card mb-4 space-y-3 p-4" @submit.prevent="create">
      <input v-model="nickname" class="input" placeholder="称呼，比如「王姐」" maxlength="50" />
      <p v-if="error" class="text-sm text-accent-deep">{{ error }}</p>
      <div class="flex gap-2">
        <button class="btn btn-primary" type="submit" :disabled="creating">创建</button>
        <button class="btn btn-ghost" type="button" @click="showCreate = false">取消</button>
      </div>
    </form>

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <div v-else-if="items.length" class="space-y-2">
      <RouterLink
        v-for="person in items"
        :key="person.id"
        :to="{ name: 'person', params: { id: person.id } }"
        class="card flex items-center gap-3 px-4 py-3"
      >
        <div
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm text-accent-deep"
        >
          {{ person.nickname.slice(0, 1) }}
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <span class="font-medium">{{ person.nickname }}</span>
            <span class="tag">{{ stageLabels[person.stage] ?? person.stage }}</span>
            <span v-if="person.stage_source === 'manual'" class="tag">手动</span>
          </div>
          <div v-if="person.tags.length" class="mt-1 flex flex-wrap gap-1">
            <span v-for="tag in person.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
        </div>
        <span class="text-ink-soft">→</span>
      </RouterLink>
    </div>

    <EmptyState v-else title="还没有人" hint="先建一个人，或者直接投喂截图让系统认出来" />

    <div v-if="items.length" class="mt-6 text-center">
      <button class="btn btn-ghost" @click="router.push({ name: 'ingest' })">投喂截图</button>
    </div>
  </div>
</template>
