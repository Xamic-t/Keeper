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
const channelKind = ref<'wecom' | 'pushplus'>('wecom')
const channelName = ref('')
const channelSecret = ref('')
const error = ref('')
const message = ref('')

/**
 * 统一收口：每个会调接口的动作都过一遍。
 * 以前这里没有 catch，接口一错页面就静默白屏（评审 P3-1），
 * 用户只会看到"什么都没变"，不知道该干嘛。
 */
async function guard<T>(action: () => Promise<T>, fallback: string): Promise<T | null> {
  error.value = ''
  try {
    return await action()
  } catch (err) {
    error.value = err instanceof Error ? err.message : fallback
    return null
  }
}

async function load() {
  const data = await guard(() => api.get<SettingsOut>('/settings'), '设置没取到，刷新试试')
  if (data) settings.value = data
}

async function toggleKeepOriginal(value: boolean) {
  const data = await guard(
    () => api.patch<SettingsOut>('/settings', { keep_original_images: value }),
    '开关没保存成功',
  )
  if (data) settings.value = data
}

async function addChannel() {
  const name = channelName.value.trim()
  const secret = channelSecret.value.trim()
  if (!name || !secret) {
    error.value = channelKind.value === 'wecom' ? '要填 webhook 地址' : '要填 PushPlus token'
    return
  }

  const created = await guard(
    () =>
      api.post('/notification-channels', {
        kind: channelKind.value,
        name,
        ...(channelKind.value === 'wecom' ? { webhook: secret } : { token: secret }),
      }),
    '通道没添加成功',
  )
  if (created === null) return

  channelName.value = ''
  channelSecret.value = ''
  message.value = '通道已添加，点「测试」验一下能不能收到'
  await load()
}

async function testChannel(id: number) {
  message.value = ''
  const result = await guard(
    () => api.post<{ ok: boolean; channel: string }>(`/notification-channels/${id}/test`),
    '测试没发出去',
  )
  if (result === null) return
  message.value = result.ok ? '推送成功，去手机上看看' : '推送失败，检查 webhook / token 对不对'
}

async function removeChannel(id: number) {
  const done = await guard(() => api.delete(`/notification-channels/${id}`), '删除失败')
  if (done !== null) await load()
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

    <p v-if="error" class="mb-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
      {{ error }}
    </p>

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
      <p class="mb-3 text-xs text-ink-soft">
        按优先级从上往下试，前一个失败自动降级到下一个。至少配一个，否则每日提醒只落库、不推送。
      </p>

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
      <p v-else class="mb-3 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
        还没有通道。现在提醒只会存进今日页，手机上收不到。
      </p>

      <div class="space-y-2">
        <div class="flex gap-2">
          <button
            class="btn flex-1"
            :class="channelKind === 'wecom' ? 'btn-primary' : 'btn-ghost'"
            @click="channelKind = 'wecom'"
          >
            企微机器人
          </button>
          <button
            class="btn flex-1"
            :class="channelKind === 'pushplus' ? 'btn-primary' : 'btn-ghost'"
            @click="channelKind = 'pushplus'"
          >
            PushPlus
          </button>
        </div>

        <input v-model="channelName" class="input" placeholder="通道名称，比如「客户群」" />
        <input
          v-model="channelSecret"
          class="input"
          :placeholder="
            channelKind === 'wecom' ? '企微群机器人 webhook 地址' : 'PushPlus token（pushplus.plus 登录后拿）'
          "
        />
        <button class="btn btn-ghost w-full" @click="addChannel">添加通道</button>
      </div>

      <p class="mt-2 text-xs text-ink-soft">
        企微：群设置 → 群机器人 → 添加 → 复制 webhook。PushPlus：微信登录 pushplus.plus → 一对一推送 → 复制 token。
      </p>
      <p v-if="message" class="mt-2 text-xs text-accent-deep">{{ message }}</p>
    </section>

    <section class="card mb-4 p-4">
      <h2 class="mb-2 text-sm font-medium">数据</h2>
      <p class="mb-3 text-xs text-ink-soft">导出会打包成 ZIP，里面是你全部人的档案。</p>
      <a class="btn btn-ghost" href="/api/export" download>导出全部数据</a>
    </section>

    <button class="btn btn-ghost w-full" @click="signOut">退出登录</button>
  </div>
</template>
