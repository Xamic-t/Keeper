<script setup lang="ts">
/**
 * 助理对话（2026-11-03）。
 *
 * 为什么自己写而不嵌 Harness：用户明确要"一级菜单、不另开窗口、不用单独启动"。
 * 嵌 iframe 的代价是——要起 Node 侧车、要粘带 token 的地址、手机上根本进不去。
 * 详见 docs/32-助理整合计划.md 与 ARCHITECTURE 19.7.10。
 *
 * 四条交互上的必须项：
 *  · 发送后立刻上气泡 + "正在想"，模型要几秒，不能让界面看起来卡死
 *  · 失败的消息留在原地并可重试——用户刚打的字不能丢
 *  · 助理执行过动作要显式说"已创建待办 · 查看"，否则用户不知道它干了什么
 *  · 附图要在本地说得清（预览 + 可撤），传上去之后走投喂流水线抽取
 */

import { computed, nextTick, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import type { AssistantAction, AssistantMessage, AssistantTurn } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { describeAction, failureText } from '@/lib/assistantDisplay'

/** 界面用的消息：比后端多一个本地状态（发送中/失败）与重试用的快照。 */
interface Bubble {
  key: string
  role: 'user' | 'assistant'
  content: string
  actions: AssistantAction[]
  status: 'ok' | 'sending' | 'failed'
  imageUrl?: string
  file?: File | null
  /** 后端已落库的用户消息 id。失败重试时带回去，避免重复插一条同样的消息 */
  serverId?: number
}

const messages = ref<Bubble[]>([])
const input = ref('')
const pendingFile = ref<File | null>(null)
const pendingPreview = ref('')
const sending = ref(false)
const loading = ref(true)
const clearing = ref(false)
const errorText = ref('')
const listEl = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

let seq = 0
const nextKey = () => `m${++seq}`

const SUGGESTIONS = [
  '11 月 4 号王姐生日，帮我建个待办',
  '王姐喜欢什么？',
  '这周还有哪些日子要准备？',
]

const canSend = computed(
  () => !sending.value && (input.value.trim().length > 0 || pendingFile.value !== null),
)

async function scrollToBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

function toBubble(item: AssistantMessage): Bubble {
  return {
    key: item.id ? `srv-${item.id}` : nextKey(),
    role: item.role,
    content: item.content,
    actions: item.actions ?? [],
    status: 'ok',
  }
}

async function loadHistory() {
  loading.value = true
  errorText.value = ''
  try {
    const history = await api.get<AssistantMessage[]>('/conversation/messages', { limit: 50 })
    messages.value = history.map(toBubble)
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : '历史没加载出来'
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

function pickFile(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0] ?? null
  target.value = ''
  if (!file) return
  clearFile()
  pendingFile.value = file
  pendingPreview.value = URL.createObjectURL(file)
}

function clearFile() {
  if (pendingPreview.value) URL.revokeObjectURL(pendingPreview.value)
  pendingPreview.value = ''
  pendingFile.value = null
}

/** 真正发请求。重试也走这里，所以参数是快照，不读输入框当前值。 */
async function deliver(bubble: Bubble) {
  bubble.status = 'sending'
  sending.value = true
  await scrollToBottom()

  const body = new FormData()
  if (bubble.content) body.append('text', bubble.content)
  if (bubble.file) body.append('image', bubble.file)
  // 重试：上一次那句话已经落库了，让后端复用它，别再存一条
  if (bubble.serverId) body.append('reuse_message_id', String(bubble.serverId))

  try {
    const turn = await api.post<AssistantTurn>('/conversation/messages', body)
    bubble.status = 'ok'
    bubble.serverId = turn.user_message_id ?? bubble.serverId
    messages.value.push({
      key: turn.assistant_message_id ? `srv-${turn.assistant_message_id}` : nextKey(),
      role: 'assistant',
      content: turn.reply,
      actions: turn.actions ?? [],
      status: 'ok',
    })
  } catch (err) {
    bubble.status = 'failed'
    // 后端把已落库的用户消息 id 放在 error.detail 里，重试时复用它
    if (err instanceof ApiError) {
      const persisted = err.detail?.user_message_id
      if (typeof persisted === 'number') bubble.serverId = persisted
    }
    const detail = err instanceof Error ? err.message : '没发出去'
    messages.value.push({
      key: nextKey(),
      role: 'assistant',
      content: `刚才没接上（${detail}）。点上面的「重试」我再答一次——你的话已经存下了，不会丢。`,
      actions: [],
      status: 'failed',
    })
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

async function send() {
  if (!canSend.value) return

  const bubble: Bubble = {
    key: nextKey(),
    role: 'user',
    content: input.value.trim(),
    actions: [],
    status: 'sending',
    imageUrl: pendingPreview.value || undefined,
    file: pendingFile.value,
  }
  // 预览地址所有权交给这条消息，别再 revoke
  pendingPreview.value = ''
  pendingFile.value = null
  input.value = ''

  messages.value.push(bubble)
  await deliver(bubble)
}

async function retry(bubble: Bubble) {
  if (sending.value) return
  // 去掉上一条"失败提示"，避免重试几次堆一串
  const last = messages.value[messages.value.length - 1]
  if (last?.status === 'failed' && last.role === 'assistant') messages.value.pop()
  await deliver(bubble)
}

function useSuggestion(text: string) {
  input.value = text
}

async function clearHistory() {
  if (clearing.value) return
  clearing.value = true
  try {
    await api.delete('/conversation/messages')
    messages.value = []
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : '清空失败'
  } finally {
    clearing.value = false
  }
}

function onKeydown(event: KeyboardEvent) {
  // Enter 发送、Shift+Enter 换行（和微信/常见聊天一致）
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void send()
  }
}

/** 动作 → 图标/链接。纯逻辑放在 lib 里，Vitest 直接钉（见 assistantDisplay.test.ts）。 */
const actionView = (action: AssistantAction) => describeAction(action)

onMounted(loadHistory)
</script>

<template>
  <div class="flex h-[calc(100vh-8rem)] flex-col lg:h-[calc(100vh-10rem)]">
    <PageHeader title="助理" subtitle="说一句话就能记事、建待办、查人。图片也可以直接发。" />

    <div class="flex items-center justify-between pb-2 text-xs text-ink-soft">
      <span v-if="loading">正在读历史…</span>
      <span v-else-if="messages.length">{{ messages.length }} 条消息</span>
      <span v-else>还没有对话</span>
      <button
        v-if="messages.length"
        class="rounded-lg px-2 py-1 transition hover:bg-surface disabled:opacity-50"
        :disabled="clearing"
        @click="clearHistory"
      >
        {{ clearing ? '清空中…' : '清空对话' }}
      </button>
    </div>

    <p v-if="errorText" class="mb-2 rounded-xl bg-surface px-3 py-2 text-sm text-accent">
      {{ errorText }}
    </p>

    <!-- 消息区 -->
    <div
      ref="listEl"
      class="flex-1 space-y-3 overflow-y-auto rounded-2xl border border-border bg-bg-alt/40 p-3 lg:p-5"
    >
      <!-- 空状态：给三个能直接点的例子 -->
      <div v-if="!loading && !messages.length" class="flex h-full flex-col justify-center gap-3 px-2">
        <p class="text-sm text-ink-soft">你可以这么说：</p>
        <button
          v-for="item in SUGGESTIONS"
          :key="item"
          class="rounded-xl border border-border bg-surface px-3 py-2 text-left text-sm transition hover:border-accent"
          @click="useSuggestion(item)"
        >
          {{ item }}
        </button>
        <p class="pt-2 text-xs text-ink-soft">
          发了图会先识别内容、进确认队列；建待办/事件会立刻生效，气泡下面能点过去看。
        </p>
      </div>

      <template v-for="bubble in messages" :key="bubble.key">
        <!-- 用户 -->
        <div v-if="bubble.role === 'user'" class="flex justify-end">
          <div class="max-w-[85%] space-y-1 lg:max-w-[70%]">
            <div class="rounded-2xl rounded-br-sm bg-accent px-3 py-2 text-sm text-white">
              <p v-if="bubble.content" class="whitespace-pre-wrap break-words">{{ bubble.content }}</p>
              <img
                v-if="bubble.imageUrl"
                :src="bubble.imageUrl"
                alt="附图"
                class="mt-2 max-h-48 rounded-lg"
              />
            </div>
            <div class="flex justify-end gap-2 text-xs text-ink-soft">
              <span v-if="bubble.status === 'sending'">发送中…</span>
              <button
                v-else-if="bubble.status === 'failed'"
                class="text-accent hover:underline"
                @click="retry(bubble)"
              >
                重试
              </button>
            </div>
          </div>
        </div>

        <!-- 助理 -->
        <div v-else class="flex justify-start">
          <div class="max-w-[88%] space-y-2 lg:max-w-[75%]">
            <div
              class="rounded-2xl rounded-bl-sm border border-border bg-surface px-3 py-2 text-sm"
            >
              <p class="whitespace-pre-wrap break-words">{{ bubble.content }}</p>
            </div>

            <!-- 已执行的动作：可点过去看 -->
            <div v-if="bubble.actions.length" class="space-y-1">
              <div
                v-for="(action, index) in bubble.actions"
                :key="index"
                class="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-bg-alt/60 px-2.5 py-1.5 text-xs"
              >
                <span :class="action.ok ? 'text-accent' : 'text-ink-soft'">
                  {{ actionView(action).icon }} {{ action.summary }}
                </span>
                <RouterLink
                  v-if="actionView(action).link"
                  :to="actionView(action).link!.to"
                  class="text-accent hover:underline"
                >
                  {{ actionView(action).link!.label }}
                </RouterLink>
                <span v-else-if="!action.ok" class="text-ink-soft">
                  {{ failureText(action) }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- 打字中 -->
      <div v-if="sending" class="flex justify-start">
        <div
          class="rounded-2xl rounded-bl-sm border border-border bg-surface px-3 py-2 text-sm text-ink-soft"
        >
          正在想…
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="mt-3 rounded-2xl border border-border bg-surface p-2">
      <div v-if="pendingPreview" class="mb-2 flex items-center gap-2 px-1">
        <img :src="pendingPreview" alt="待发图片" class="h-14 w-14 rounded-lg object-cover" />
        <button class="text-xs text-ink-soft hover:text-ink" @click="clearFile">移除</button>
      </div>

      <div class="flex items-end gap-2">
        <button
          class="shrink-0 rounded-xl px-2 py-2 text-lg text-ink-soft transition hover:bg-bg-alt"
          title="发一张截图"
          @click="fileInput?.click()"
        >
          📎
        </button>
        <input
          ref="fileInput"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          class="hidden"
          @change="pickFile"
        />
        <textarea
          v-model="input"
          rows="1"
          placeholder="说点什么…"
          class="max-h-32 min-h-[2.5rem] flex-1 resize-none bg-transparent px-1 py-2 text-sm outline-none placeholder:text-ink-soft"
          @keydown="onKeydown"
        />
        <button class="btn btn-primary shrink-0" :disabled="!canSend" @click="send">
          发送
        </button>
      </div>
      <p class="hidden px-2 pt-1 text-[11px] text-ink-soft lg:block">
        Enter 发送 · Shift+Enter 换行 · 📎 发截图（会先识别内容，进确认队列）
      </p>
    </div>
  </div>
</template>
