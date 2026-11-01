<script setup lang="ts">
/**
 * 对话式投喂。
 *
 * 只做两件事：投喂（说一句话 / 甩一张截图）和查询（问某个人的情况）。
 * 不做通用聊天机器人——每多一个意图，就多一个不准的地方（HANDOFF2 W13-B）。
 *
 * 两个交互上的必须项：
 *  · 投喂是**同步抽取**的，一张图 3–8 秒。所以发送后立刻上气泡 + "正在识别"，
 *    不能让界面看起来卡死了
 *  · 失败的消息要留在原地并给"重试"，而不是静默消失。用户刚打完的字不能丢
 */

import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { ChatReply } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()

interface Payload {
  text: string
  file: File | null
}

interface ChatMessage {
  key: number
  role: 'me' | 'bot'
  text: string
  status: 'ok' | 'sending' | 'failed'
  imageUrl?: string
  personId?: number | null
  pendingCount?: number
  payload?: Payload
}

const messages = ref<ChatMessage[]>([])
const input = ref('')
const pendingFile = ref<File | null>(null)
const pendingPreview = ref('')
const sending = ref(false)
const listEl = ref<HTMLElement | null>(null)

let seq = 0

const canSend = computed(() => !sending.value && (input.value.trim().length > 0 || !!pendingFile.value))

async function scrollToBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

function pickFile(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0] ?? null
  if (!file) return
  clearFile()
  pendingFile.value = file
  pendingPreview.value = URL.createObjectURL(file)
  target.value = ''
}

function clearFile() {
  if (pendingPreview.value) URL.revokeObjectURL(pendingPreview.value)
  pendingPreview.value = ''
  pendingFile.value = null
}

/** 真正发请求。重试也走这里，所以参数用快照，不读输入框的当前值。 */
async function deliver(message: ChatMessage, payload: Payload) {
  message.status = 'sending'
  await scrollToBottom()

  try {
    const body = new FormData()
    if (payload.text) body.append('message', payload.text)
    if (payload.file) body.append('image', payload.file)

    const reply = await api.post<ChatReply>('/chat', body)
    message.status = 'ok'
    messages.value.push({
      key: ++seq,
      role: 'bot',
      text: reply.reply,
      status: 'ok',
      personId: reply.person_id,
      pendingCount: reply.pending_count,
    })
  } catch (err) {
    message.status = 'failed'
    messages.value.push({
      key: ++seq,
      role: 'bot',
      text: err instanceof Error ? `没发出去：${err.message}` : '没发出去，点重试再试一次。',
      status: 'failed',
    })
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

async function send() {
  if (!canSend.value) return
  const payload: Payload = { text: input.value.trim(), file: pendingFile.value }

  const message: ChatMessage = {
    key: ++seq,
    role: 'me',
    text: payload.text,
    status: 'sending',
    imageUrl: pendingPreview.value || undefined,
    payload,
  }
  // 预览地址的所有权交给这条消息，别再 revoke
  pendingPreview.value = ''

  messages.value.push(message)
  input.value = ''
  pendingFile.value = null
  sending.value = true

  await deliver(message, payload)
}

async function retry(message: ChatMessage) {
  if (!message.payload || sending.value) return
  sending.value = true
  // 上一条失败的回执一并撤掉，避免重试后屏幕上出现两份结论
  const index = messages.value.findIndex((item) => item.role === 'bot' && item.status === 'failed')
  if (index >= 0) messages.value.splice(index, 1)
  await deliver(message, message.payload)
}

function onEnter(event: KeyboardEvent) {
  // 输入法组词中的回车不算发送
  if (event.isComposing) return
  event.preventDefault()
  void send()
}

onMounted(() => {
  messages.value.push({
    key: ++seq,
    role: 'bot',
    text:
      '把聊天截图丢进来，或者说一句「王姐说她女儿模考进了前二十」。\n' +
      '也可以直接问：「王姐喜欢什么」。\n' +
      '我只做投喂和查询两件事，别的忙帮不上。',
    status: 'ok',
  })
})
</script>

<template>
  <div class="mx-auto flex h-[calc(100dvh-8rem)] max-w-2xl flex-col lg:h-[calc(100dvh-9rem)]">
    <PageHeader title="对话" subtitle="说一句，或者甩张截图；也能问某个人" />

    <div class="mb-3 flex items-center justify-between text-xs text-ink-soft">
      <RouterLink :to="{ name: 'ingest' }" class="hover:text-ink">← 切回批量投喂</RouterLink>
      <span>抽出的事实要你在「确认」里点过才进档案</span>
    </div>

    <!-- 消息区 -->
    <div ref="listEl" class="flex-1 space-y-3 overflow-y-auto pb-2">
      <div
        v-for="message in messages"
        :key="message.key"
        class="flex"
        :class="message.role === 'me' ? 'justify-end' : 'justify-start'"
      >
        <div class="max-w-[85%] space-y-1.5">
          <div
            class="whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed"
            :class="
              message.role === 'me'
                ? 'bg-accent text-white'
                : message.status === 'failed'
                  ? 'border border-danger bg-danger-soft text-ink'
                  : 'card text-ink'
            "
          >
            <div v-if="message.status === 'sending' && !message.text" class="text-sm opacity-80">
              正在识别…
            </div>
            <template v-else>{{ message.text }}</template>
            <div
              v-if="message.status === 'sending' && message.text"
              class="mt-1 text-[11px] opacity-70"
            >
              发送中…
            </div>
          </div>

          <!-- 图片缩略：自己发的那张 -->
          <img
            v-if="message.imageUrl"
            :src="message.imageUrl"
            alt=""
            class="ml-auto block max-h-40 rounded-xl border border-border object-cover"
          />

          <!-- 事实已经在气泡里列过了，这里只给一个能点进去的动作，不重复第二遍 -->
          <div
            v-if="message.personId || message.pendingCount"
            class="card flex items-center justify-between px-3 py-2 text-xs"
          >
            <RouterLink
              v-if="message.personId"
              :to="{ name: 'person', params: { id: message.personId } }"
              class="font-medium text-accent-deep hover:underline"
            >
              打开档案 →
            </RouterLink>
            <RouterLink :to="{ name: 'confirm' }" class="text-ink-soft hover:text-ink">
              {{ message.pendingCount ? `去确认这 ${message.pendingCount} 条` : '看看待确认' }}
            </RouterLink>
          </div>

          <button
            v-if="message.status === 'failed' && message.role === 'me'"
            class="block text-xs text-danger hover:underline"
            :disabled="sending"
            @click="retry(message)"
          >
            重试
          </button>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="mt-2 shrink-0">
      <div v-if="pendingPreview" class="mb-2 flex items-center gap-2">
        <img :src="pendingPreview" alt="" class="h-14 w-11 rounded-md object-cover" />
        <button class="text-xs text-ink-soft hover:text-ink" @click="clearFile">移除图片</button>
      </div>

      <div class="card flex items-end gap-2 p-2">
        <label
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-lg text-ink-soft hover:bg-bg-alt"
          :class="sending ? 'pointer-events-none opacity-50' : ''"
          title="发一张截图"
        >
          ＋
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            class="hidden"
            :disabled="sending"
            @change="pickFile"
          />
        </label>

        <textarea
          v-model="input"
          rows="1"
          maxlength="2000"
          class="max-h-32 min-h-9 flex-1 resize-none bg-transparent px-1 py-1.5 text-sm outline-none"
          placeholder="说点什么，或者问「王姐喜欢什么」"
          @keydown.enter.exact="onEnter"
        ></textarea>

        <button class="btn btn-primary h-9 shrink-0 px-3" :disabled="!canSend" @click="send">
          {{ sending ? '…' : '发送' }}
        </button>
      </div>
    </div>
  </div>
</template>
