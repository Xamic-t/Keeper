<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { PersonListItem, TagOut } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
const items = ref<PersonListItem[]>([])
const tags = ref<TagOut[]>([])
const total = ref(0)
const keyword = ref('')
const tagFilter = ref('')
const loading = ref(false)
const listError = ref('')
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
const tagKeyLabels: Record<string, string> = {
  industry: '行业',
  family: '家庭',
  preference: '偏好',
  situation: '近况',
  value: '价值',
  stage: '阶段',
}

function tagLabel(tag: TagOut) {
  return `${tagKeyLabels[tag.key] ?? tag.key} · ${tag.value}`
}

const tagOptions = computed(() => {
  const seen = new Map<string, string>()
  for (const tag of tags.value) {
    const label = tagLabel(tag)
    if (!seen.has(label)) seen.set(label, `${tag.key}:${tag.value}`)
  }
  return [...seen.entries()].map(([label, value]) => ({ label, value }))
})

/** 标签筛选在前端做：20 人规模不值得为它加后端联表查询；先拉全量 tags，再按 person_id 过滤。 */
const visibleItems = computed(() => {
  if (!tagFilter.value) return items.value
  const [key, ...rest] = tagFilter.value.split(':')
  const value = rest.join(':')
  const personIds = new Set(
    tags.value.filter((tag) => tag.key === key && tag.value === value).map((tag) => tag.person_id),
  )
  return items.value.filter((person) => personIds.has(person.id))
})

async function load() {
  loading.value = true
  listError.value = ''
  try {
    const payload = await api.raw<{ data: PersonListItem[]; meta: { total: number } }>(
      `/persons?size=100${keyword.value ? `&keyword=${encodeURIComponent(keyword.value)}` : ''}`,
    )
    items.value = payload.data
    total.value = payload.meta.total
  } catch (err) {
    items.value = []
    total.value = 0
    listError.value = err instanceof Error ? err.message : '人没取到，刷新试试'
  } finally {
    loading.value = false
  }
}

async function loadTags() {
  try {
    tags.value = await api.get<TagOut[]>('/tags')
  } catch {
    // 标签是增强筛选，拿不到不阻塞人列表
    tags.value = []
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
    await Promise.all([load(), loadTags()])
  } catch (err) {
    error.value = err instanceof Error ? err.message : '创建失败'
  } finally {
    creating.value = false
  }
}

function search() {
  void load()
}

onMounted(() => {
  void load()
  void loadTags()
})
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <PageHeader title="人" :subtitle="`共 ${visibleItems.length} 位`" action-label="新建" @action="showCreate = true" />

    <div class="mb-4 flex gap-2">
      <input
        v-model="keyword"
        class="input"
        placeholder="搜昵称或别名"
        @keyup.enter="search"
      />
      <button class="btn btn-ghost shrink-0" @click="search">搜索</button>
    </div>

    <div v-if="tagOptions.length" class="mb-4">
      <label class="block text-xs text-ink-soft">
        按标签筛，比如「谁喜欢白茶」
        <select v-model="tagFilter" class="input mt-1">
          <option value="">全部人</option>
          <option v-for="option in tagOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </label>
    </div>

    <p v-if="listError" class="mb-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
      {{ listError }}
    </p>

    <form v-if="showCreate" class="card mb-4 space-y-3 p-4" @submit.prevent="create">
      <input v-model="nickname" class="input" placeholder="称呼，比如「王姐」" maxlength="50" />
      <p v-if="error" class="text-sm text-accent-deep">{{ error }}</p>
      <div class="flex gap-2">
        <button class="btn btn-primary" type="submit" :disabled="creating">创建</button>
        <button class="btn btn-ghost" type="button" @click="showCreate = false">取消</button>
      </div>
    </form>

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <div v-else-if="visibleItems.length" class="space-y-2">
      <RouterLink
        v-for="person in visibleItems"
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

    <EmptyState
      v-else
      :title="tagFilter ? '没有这个标签的人' : '还没有人'"
      :hint="tagFilter ? '换个标签看看' : '先建一个人，或者直接投喂截图让系统认出来'"
    />

    <div v-if="visibleItems.length" class="mt-6 text-center">
      <button class="btn btn-ghost" @click="router.push({ name: 'ingest' })">投喂截图</button>
    </div>
  </div>
</template>
