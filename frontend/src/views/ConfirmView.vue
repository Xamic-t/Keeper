<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { ConfirmationGroup } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const groups = ref<ConfirmationGroup[]>([])
const loading = ref(true)
const busy = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    groups.value = await api.get<ConfirmationGroup[]>('/confirmations')
  } finally {
    loading.value = false
  }
}

async function confirmOne(factId: number) {
  busy.value = factId
  try {
    await api.post(`/confirmations/${factId}/confirm`)
    await load()
  } finally {
    busy.value = null
  }
}

async function rejectOne(factId: number) {
  busy.value = factId
  try {
    await api.post(`/confirmations/${factId}/reject`)
    await load()
  } finally {
    busy.value = null
  }
}

async function confirmAll(factIds: number[]) {
  busy.value = -1
  try {
    await api.post('/confirmations/batch', { fact_ids: factIds })
    await load()
  } finally {
    busy.value = null
  }
}

const total = computed(() => groups.value.reduce((sum, group) => sum + group.items.length, 0))

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <PageHeader
      title="确认"
      subtitle="AI 抽出来的东西，点一遍才进档案"
      :action-label="total > 0 ? '全部确认' : ''"
      @action="confirmAll(groups.flatMap((group) => group.items.map((item) => item.id)))"
    />

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
              <span>置信度 {{ (item.confidence * 100).toFixed(0) }}%</span>
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
            <div class="flex gap-2">
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
