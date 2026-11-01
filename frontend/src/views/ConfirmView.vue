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
const flash = ref('')

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

/**
 * 处理完一条后只在本地摘掉它，**不重新拉列表**。
 * 重新拉的话后端顺序一变，用户刚点过的那个人就会跳到别处，
 * 视线跟着跑，连着点几条会点错。
 */
function dropItem(itemId: number) {
  const next: ConfirmationGroup[] = []
  for (const group of groups.value) {
    const items = group.items.filter((item) => item.id !== itemId)
    if (items.length) next.push({ ...group, items })
  }
  groups.value = next
}

async function confirmOne(factId: number) {
  busy.value = factId
  error.value = ''
  try {
    await api.post(`/confirmations/${factId}/confirm`)
    dropItem(factId)
    flash.value = '已确认，进档案了'
    window.setTimeout(() => (flash.value = ''), 1600)
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
    dropItem(factId)
    flash.value = '已驳回'
    window.setTimeout(() => (flash.value = ''), 1600)
  } catch (err) {
    showError(err, '驳回失败')
  } finally {
    busy.value = null
  }
}

/** 后端单次上限 200，这里按 50 分片：一批出问题只影响 50 条，也更好看进度。 */
const BATCH_CHUNK = 50

async function confirmAll() {
  busy.value = -1
  error.value = ''
  try {
    const factIds = groups.value
      .flatMap((group) => group.items)
      .filter((item) => item.kind === 'fact')
      .map((item) => item.id)
    if (!factIds.length) return

    // 以前一次把全部 id 发出去，超过后端上限就整个失败，一条都确认不了
    let done = 0
    for (let start = 0; start < factIds.length; start += BATCH_CHUNK) {
      const slice = factIds.slice(start, start + BATCH_CHUNK)
      await api.post('/confirmations/batch', { fact_ids: slice })
      done += slice.length
    }

    // 只清掉 fact 卡片，归属待定的那些还得人工指定
    groups.value = groups.value
      .map((group) => ({ ...group, items: group.items.filter((item) => item.kind !== 'fact') }))
      .filter((group) => group.items.length > 0)
    flash.value = `已全部确认 ${done} 条`
    window.setTimeout(() => (flash.value = ''), 2000)
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
    dropItem(rawInputId)
    flash.value = '已指派，正在按这个人重新抽取'
    window.setTimeout(() => (flash.value = ''), 2000)

    // 指派会触发后台重新抽取，抽出来的新事实应当出现在这个列表里。
    // 以前只弹一句话，界面什么都不变，用户以为没生效。抽取是异步的，
    // 所以过几秒再拉一次（拉早了只能拿到旧结果）。
    window.setTimeout(() => void load(), 3000)
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
    <p v-if="flash" class="mb-3 rounded-md bg-success/10 px-3 py-2 text-sm text-success">{{ flash }}</p>

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
