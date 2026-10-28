<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { ConfirmationGroup, PersonListItem } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const groups = ref<ConfirmationGroup[]>([])
const persons = ref<PersonListItem[]>([])
const loading = ref(true)
const busy = ref<number | null>(null)
const error = ref('')

function showError(err: unknown, fallback: string) {
  error.value = err instanceof Error ? err.message : fallback
}

async function load() {
  loading.value = true
  try {
    groups.value = await api.get<ConfirmationGroup[]>('/confirmations')
  } catch (err) {
    showError(err, '确认列表没取到，刷新试试')
  } finally {
    loading.value = false
  }
}

async function loadPersons() {
  const payload = await api.raw<{ data: PersonListItem[] }>('/persons?size=100')
  persons.value = payload.data
}

async function confirmOne(factId: number) {
  busy.value = factId
  error.value = ''
  try {
    await api.post(`/confirmations/${factId}/confirm`)
    await load()
  } catch (err) {
    showError(err, '确认失败')
  } finally {
    busy.value = null
  }
}

async function rejectOne(factId: number) {
  busy.value = factId
  error.value = ''
  try {
    await api.post(`/confirmations/${factId}/reject`)
    await load()
  } catch (err) {
    showError(err, '驳回失败')
  } finally {
    busy.value = null
  }
}

async function confirmAll() {
  busy.value = -1
  error.value = ''
  try {
    const factIds = groups.value
      .flatMap((group) => group.items)
      .filter((item) => item.kind === 'fact')
      .map((item) => item.id)
    if (!factIds.length) return
    await api.post('/confirmations/batch', { fact_ids: factIds })
    await load()
  } catch (err) {
    showError(err, '批量确认失败')
  } finally {
    busy.value = null
  }
}

// 归属待定：每张卡片记一个选中的 person_id，确认后走 assign 接口
const pickedPerson = ref<Record<number, number>>({})

async function assignOne(rawInputId: number) {
  const personId = pickedPerson.value[rawInputId]
  if (!personId) {
    error.value = '先选一个人'
    return
  }
  busy.value = rawInputId
  error.value = ''
  try {
    await api.post(`/raw-inputs/${rawInputId}/assign`, { person_id: personId })
    delete pickedPerson.value[rawInputId]
    await load()
  } catch (err) {
    showError(err, '指派失败')
  } finally {
    busy.value = null
  }
}

const factTotal = computed(() =>
  groups.value.reduce(
    (sum, group) => sum + group.items.filter((item) => item.kind === 'fact').length,
    0,
  ),
)
const hasUnassigned = computed(() =>
  groups.value.some((group) => group.items.some((item) => item.kind === 'unassigned')),
)

onMounted(async () => {
  await load()
  if (hasUnassigned.value) await loadPersons()
})
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <PageHeader
      title="确认"
      subtitle="AI 抽出来的东西，点一遍才进档案"
      :action-label="factTotal > 0 ? '全部确认' : ''"
      @action="confirmAll()"
    />

    <p v-if="error" class="mb-3 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn">{{ error }}</p>

    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <template v-else-if="groups.length">
      <section v-for="group in groups" :key="group.person_id ?? 'unknown'" class="mb-6">
        <h2 class="mb-2 text-sm font-medium text-ink-soft">
          {{ group.nickname }}
          <span class="text-xs">（{{ group.items.length }}）</span>
        </h2>

        <div class="space-y-2">
          <div v-for="item in group.items" :key="item.id" class="card p-4">
            <div class="mb-1 text-sm">{{ item.content }}</div>
            <div class="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
              <span class="tag">{{ item.category }}</span>
              <span v-if="item.kind === 'fact'">置信度 {{ (item.confidence * 100).toFixed(0) }}%</span>
              <a
                v-if="item.thumb_url"
                :href="item.thumb_url"
                target="_blank"
                class="text-accent hover:underline"
              >
                看原图
              </a>
            </div>
            <blockquote
              v-if="item.evidence"
              class="mb-3 border-l-2 border-border-strong pl-3 text-xs text-ink-soft"
            >
              {{ item.evidence }}
            </blockquote>

            <!-- 归属待定：选人 → 指派，走 /raw-inputs/{id}/assign -->
            <div v-if="item.kind === 'unassigned'" class="flex gap-2">
              <select
                v-model="pickedPerson[item.id]"
                class="input min-w-0 flex-1"
                :disabled="busy !== null"
              >
                <option :value="undefined" disabled>选一个人…</option>
                <option v-for="person in persons" :key="person.id" :value="person.id">
                  {{ person.nickname }}
                </option>
              </select>
              <button
                class="btn btn-primary shrink-0"
                :disabled="busy !== null || !pickedPerson[item.id]"
                @click="assignOne(item.id)"
              >
                指派
              </button>
            </div>

            <!-- 已归属：确认 / 驳回 -->
            <div v-else class="flex gap-2">
              <button
                class="btn btn-primary"
                :disabled="busy !== null"
                @click="confirmOne(item.id)"
              >
                确认
              </button>
              <button
                class="btn btn-ghost"
                :disabled="busy !== null"
                @click="rejectOne(item.id)"
              >
                驳回
              </button>
            </div>
          </div>
        </div>
      </section>
    </template>

    <EmptyState v-else title="没有待确认的东西" hint="投喂截图后，AI 抽到的事实会出现在这里" />
  </div>
</template>
