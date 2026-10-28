<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { SettingsOut } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const settings = ref<SettingsOut | null>(null)
const channelName = ref('')
const channelWebhook = ref('')
const message = ref('')

async function load() {
  settings.value = await api.get<SettingsOut>('/settings')
}

async function toggleKeepOriginal(value: boolean) {
  settings.value = await api.patch<SettingsOut>('/settings', { keep_original_images: value })
}

async function addChannel() {
  if (!channelName.value.trim() || !channelWebhook.value.trim()) return
  await api.post('/notification-channels', {
    kind: 'wecom',
    name: channelName.value.trim(),
    webhook: channelWebhook.value.trim(),
  })
  channelName.value = ''
  channelWebhook.value = ''
  message.value = '通道已添加'
  await load()
}

async function testChannel(id: number) {
  const result = await api.post<{ ok: boolean; channel: string }>(
    `/notification-channels/${id}/test`,
  )
  message.value = result.ok ? '推送成功' : '推送失败，检查 webhook 是否正确'
}

async function removeChannel(id: number) {
  await api.delete(`/notification-channels/${id}`)
  await load()
}

async function signOut() {
  await auth.logout()
  router.push({ name: 'login' })
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <PageHeader title="设置" />

    <section class="card mb-4 p-4">
      <label class="flex items-center justify-between">
        <span>
          <span class="block text-sm font-medium">保留聊天原图</span>
          <span class="block text-xs text-ink-soft">关掉后，抽取完就把图片删掉，只留文字</span>
        </span>
        <input
          type="checkbox"
          class="h-5 w-5 accent-[#d9694a]"
          :checked="settings?.keep_original_images ?? true"
          @change="toggleKeepOriginal(($event.target as HTMLInputElement).checked)"
        />
      </label>
    </section>

    <section class="card mb-4 p-4">
      <h2 class="mb-1 text-sm font-medium">推送通道</h2>
      <p class="mb-3 text-xs text-ink-soft">按顺序尝试，前一个失败自动降级到下一个。</p>

      <div v-if="settings?.channels.length" class="mb-3 space-y-2">
        <div
          v-for="channel in settings.channels"
          :key="channel.id"
          class="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm"
        >
          <span>{{ channel.name }} <span class="tag ml-1">{{ channel.kind }}</span></span>
          <span class="flex gap-3 text-xs">
            <button class="text-accent" @click="testChannel(channel.id)">测试</button>
            <button class="text-ink-soft hover:text-accent-deep" @click="removeChannel(channel.id)">
              删除
            </button>
          </span>
        </div>
      </div>

      <div class="space-y-2">
        <input v-model="channelName" class="input" placeholder="通道名称，比如「客户群」" />
        <input v-model="channelWebhook" class="input" placeholder="企微机器人 webhook 地址" />
        <button class="btn btn-ghost" @click="addChannel">添加通道</button>
      </div>
      <p v-if="message" class="mt-2 text-xs text-ink-soft">{{ message }}</p>
    </section>

    <section class="card mb-4 p-4">
      <h2 class="mb-2 text-sm font-medium">数据</h2>
      <p class="mb-3 text-xs text-ink-soft">导出会打包成 ZIP，里面是你全部人的档案。</p>
      <a class="btn btn-ghost" href="/api/export" download>导出全部数据</a>
    </section>

    <button class="btn btn-ghost w-full" @click="signOut">退出登录</button>
  </div>
</template>
