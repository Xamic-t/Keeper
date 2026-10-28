<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { FactOut, LedgerOut, PersonDetail } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'

const route = useRoute()
const router = useRouter()

const personId = Number(route.params.id)
const detail = ref<PersonDetail | null>(null)
const facts = ref<FactOut[]>([])
const ledger = ref<LedgerOut[]>([])
const timeline = ref<{ day: string; kind: string; title: string; detail: string | null }[]>([])
const loading = ref(true)
const rewriting = ref(false)
const message = ref('')

async function load() {
  loading.value = true
  try {
    const [person, factList, ledgerList, timelineList] = await Promise.all([
      api.get<PersonDetail>(`/persons/${personId}`),
      api.get<FactOut[]>(`/persons/${personId}/facts`),
      api.get<LedgerOut[]>(`/persons/${personId}/ledger`),
      api.get<{ day: string; kind: string; title: string; detail: string | null }[]>(
        `/persons/${personId}/timeline`,
      ),
    ])
    detail.value = person
    facts.value = factList
    ledger.value = ledgerList
    timeline.value = timelineList
  } finally {
    loading.value = false
  }
}

async function rewriteSummary() {
  rewriting.value = true
  message.value = ''
  try {
    await api.post(`/persons/${personId}/summary/rewrite`)
    message.value = '综述已重新生成'
    await load()
  } catch (err) {
    message.value = err instanceof Error ? err.message : '生成失败（可能还没配模型 Key）'
  } finally {
    rewriting.value = false
  }
}

async function removePerson() {
  if (!confirm('删掉这个人？相关数据会全部清除，且不可恢复。')) return
  await api.delete(`/persons/${personId}`)
  router.push({ name: 'persons' })
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <div v-if="loading" class="text-sm text-ink-soft">加载中…</div>

    <template v-else-if="detail">
      <PageHeader :title="detail.nickname" :subtitle="detail.relationship.roles.join(' / ')" />

      <!-- 综述 -->
      <section class="card mb-4 p-4">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-medium text-ink-soft">人物综述</h2>
          <button class="text-sm text-accent hover:text-accent-deep" :disabled="rewriting" @click="rewriteSummary">
            {{ rewriting ? '生成中…' : detail.summary_stale ? '重新生成' : '重写' }}
          </button>
        </div>
        <p v-if="detail.summary" class="leading-relaxed">{{ detail.summary }}</p>
        <p v-else class="text-sm text-ink-soft">还没有综述。确认几条事实后就能生成。</p>
        <p v-if="message" class="mt-2 text-xs text-ink-soft">{{ message }}</p>
      </section>

      <!-- 关键信息 -->
      <section class="card mb-4 divide-y divide-border">
        <div class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">阶段</span>
          <span>{{ detail.relationship.stage }}（{{ detail.relationship.stage_source }}）</span>
        </div>
        <div class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">收 / 付</span>
          <span>{{ detail.relationship.total_in }} / {{ detail.relationship.total_out }}</span>
        </div>
        <div v-if="detail.relationship.taboo.length" class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">禁忌</span>
          <span class="text-accent-deep">{{ detail.relationship.taboo.join('、') }}</span>
        </div>
        <div class="flex items-center justify-between px-4 py-3 text-sm">
          <span class="text-ink-soft">已记住</span>
          <span>{{ detail.counts.facts }} 条事实 · {{ detail.counts.ledger }} 笔往来</span>
        </div>
      </section>

      <!-- 标签 -->
      <section v-if="detail.tags.length" class="mb-4 flex flex-wrap gap-1">
        <span v-for="tag in detail.tags" :key="tag" class="tag">{{ tag }}</span>
      </section>

      <!-- 事实 -->
      <section class="mb-6">
        <h2 class="mb-2 text-sm font-medium text-ink-soft">事实</h2>
        <div v-if="facts.length" class="card divide-y divide-border">
          <div v-for="fact in facts" :key="fact.id" class="px-4 py-3">
            <div class="text-sm">{{ fact.content }}</div>
            <div class="mt-1 flex items-center gap-2 text-xs text-ink-soft">
              <span class="tag">{{ fact.category }}</span>
              <span>权重 {{ fact.weight.toFixed(2) }}</span>
              <span v-if="fact.evidence" class="truncate">出处：{{ fact.evidence }}</span>
            </div>
          </div>
        </div>
        <EmptyState v-else title="还没有确认的事实" hint="投喂截图后，去「确认」里过一遍" />
      </section>

      <!-- 往来 -->
      <section class="mb-6">
        <h2 class="mb-2 text-sm font-medium text-ink-soft">往来</h2>
        <div v-if="ledger.length" class="card divide-y divide-border">
          <div v-for="item in ledger" :key="item.id" class="flex items-center justify-between px-4 py-3 text-sm">
            <span>{{ item.occurred_at }} · {{ item.kind }}</span>
            <span :class="item.direction === 'in' ? 'text-success' : 'text-ink'">
              {{ item.direction === 'in' ? '+' : '-' }}{{ item.amount }}
            </span>
          </div>
        </div>
        <EmptyState v-else title="还没有往来记录" />
      </section>

      <!-- 时间线 -->
      <section class="mb-8">
        <h2 class="mb-2 text-sm font-medium text-ink-soft">时间线</h2>
        <div v-if="timeline.length" class="card divide-y divide-border">
          <div v-for="(item, index) in timeline" :key="index" class="flex gap-3 px-4 py-3 text-sm">
            <span class="w-20 shrink-0 text-ink-soft">{{ item.day }}</span>
            <span class="min-w-0 flex-1">{{ item.title }}</span>
          </div>
        </div>
        <EmptyState v-else title="时间线还是空的" />
      </section>

      <div class="flex justify-end">
        <button class="btn btn-ghost text-accent-deep" @click="removePerson">删除这个人</button>
      </div>
    </template>

    <div v-else class="text-sm text-ink-soft">找不到这个人。</div>
  </div>
</template>
