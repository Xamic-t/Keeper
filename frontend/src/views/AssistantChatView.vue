<script setup lang="ts">
/**
 * 助理对话（2026-11-03 接回 Harness）。
 *
 * 形态没变：还是主界面第 3 个 tab、原生聊天窗口。变的是**谁来编排**——
 * 文字消息走 `/api/conversation/turn/stream`，后端起 `dsh --profile headless`，
 * 把它的 NDJSON 事件转成 SSE 推过来：
 *
 *    thinking    模型在想什么（一句摘要）
 *    tool_call   调了哪个工具、参数是什么
 *    tool_result 工具返回了什么（摘要）
 *    final       最终回复
 *
 * 所以气泡下面能看到"它到底查了什么"，而不是只看到一句结论。
 * 图片仍走原来的 multipart 接口（抽取链路没变）。
 */

import { computed, nextTick, onMounted, ref } from 'vue'

import { ApiError, api, csrfToken } from '@/api/client'
import type { AssistantAction, AssistantMessage, AssistantTurn } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import { describeAction, failureText } from '@/lib/assistantDisplay'

interface Step {
  tool: string
  label: string
  ok?: boolean
  summary: string
}

interface Bubble {
  key: string
  role: 'user' | 'assistant'
  content: string
  /** 原生路径（降级/历史）产生的动作结果 */
  actions: AssistantAction[]
  /** Harness 路径产生的工具步骤 */
  steps: Step[]
  status: 'ok' | 'sending' | 'failed'
  streaming?: boolean
  thinking?: string
  notice?: string
  imageUrl?: string
  file?: File | null
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
  const rows = (item.actions ?? []) as (AssistantAction & Step)[]
  return {
    key: item.id ? `srv-${item.id}` : nextKey(),
    role: item.role,
    content: item.content,
    // 历史里两种形状混存：{type:'tool'} 是工具步骤，其余是原生动作
    actions: rows.filter((row) => row.type !== 'tool') as AssistantAction[],
    steps: rows
      .filter((row) => row.type === 'tool')
      .map((row) => ({ tool: row.tool, label: row.label, ok: row.ok, summary: row.summary })),
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

/** 图片走原来的 multipart 接口（抽取链路没变）。 */
async function deliverWithImage(bubble: Bubble) {
  const body = new FormData()
  if (bubble.content) body.append('text', bubble.content)
  if (bubble.file) body.append('image', bubble.file)

  const turn = await api.post<AssistantTurn>('/conversation/messages', body)
  bubble.status = 'ok'
  bubble.serverId = turn.user_message_id ?? bubble.serverId
  messages.value.push({
    key: turn.assistant_message_id ? `srv-${turn.assistant_message_id}` : nextKey(),
    role: 'assistant',
    content: turn.reply,
    actions: turn.actions ?? [],
    steps: [],
    status: 'ok',
  })
}

/**
 * 文字走 SSE。为什么不用 EventSource：它只能 GET，带不了 CSRF 头，也不能传 body。
 * 所以用 fetch + ReadableStream 自己解 `data: {...}\n\n`。
 */
async function deliverStream(bubble: Bubble) {
  const csrf = csrfToken()
  const response = await fetch('/api/conversation/turn/stream', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    body: JSON.stringify({ text: bubble.content }),
  })
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}))
    throw new ApiError(
      response.status,
      payload?.error ?? { code: 'HTTP_ERROR', message: '助理这条没接上' },
    )
  }

  const live: Bubble = {
    key: nextKey(),
    role: 'assistant',
    content: '',
    actions: [],
    steps: [],
    status: 'ok',
    streaming: true,
  }
  messages.value.push(live)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.trim()
      if (!line.startsWith('data:')) continue
      let event: Record<string, unknown>
      try {
        event = JSON.parse(line.slice(5).trim())
      } catch {
        continue
      }

      if (event.type === 'thinking') {
        live.thinking = String(event.text ?? '')
      } else if (event.type === 'tool_call') {
        live.steps.push({
          tool: String(event.tool ?? ''),
          label: String(event.label ?? ''),
          summary: '调用中…',
        })
        live.thinking = ''
      } else if (event.type === 'tool_result') {
        const pending = [...live.steps].reverse().find((step) => step.ok === undefined)
        if (pending) {
          pending.ok = Boolean(event.ok)
          pending.summary = String(event.summary ?? '')
        } else {
          live.steps.push({
            tool: String(event.tool ?? ''),
            label: String(event.label ?? ''),
            ok: Boolean(event.ok),
            summary: String(event.summary ?? ''),
          })
        }
      } else if (event.type === 'notice') {
        live.notice = String(event.text ?? '')
      } else if (event.type === 'final') {
        live.content = String(event.text ?? '')
        live.streaming = false
        bubble.status = 'ok'
      }
    }
    await scrollToBottom()
  }

  live.streaming = false
  if (!live.content) {
    live.content = '这次没拿到回复，点重试再问一次。'
    live.status = 'failed'
  }
}

/** 真正发请求。重试也走这里，所以参数是快照，不读输入框当前值。 */
async function deliver(bubble: Bubble) {
  bubble.status = 'sending'
  sending.value = true
  await scrollToBottom()

  try {
    if (bubble.file) {
      await deliverWithImage(bubble)
    } else {
      await deliverStream(bubble)
    }
    bubble.status = 'ok'
  } catch (err) {
    bubble.status = 'failed'
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
      steps: [],
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
    steps: [],
    status: 'sending',
    imageUrl: pendingPreview.value || undefined,
    file: pendingFile.value,
  }
  pendingPreview.value = ''
  pendingFile.value = null
  input.value = ''

  messages.value.push(bubble)
  await deliver(bubble)
}

async function retry(bubble: Bubble) {
  if (sending.value) return
  const last = messages.value[messages.value.length - 1]
  if (last?.status === 'failed' && last.role === 'assistant') messages.value.pop()
  // 流式失败时可能残留一个空助手气泡，一并清掉，免得越重试越多
  const tail = messages.value[messages.value.length - 1]
  if (tail && tail.role === 'assistant' && !tail.content) messages.value.pop()
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
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void send()
  }
}

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

    <div
      ref="listEl"
      class="flex-1 space-y-3 overflow-y-auto rounded-2xl border border-border bg-bg-alt/40 p-3 lg:p-5"
    >
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

        <div v-else class="flex justify-start">
          <div class="max-w-[88%] space-y-2 lg:max-w-[75%]">
            <p v-if="bubble.notice" class="text-xs text-ink-soft">{{ bubble.notice }}</p>

            <div class="rounded-2xl rounded-bl-sm border border-border bg-surface px-3 py-2 text-sm">
              <p v-if="bubble.content" class="whitespace-pre-wrap break-words">{{ bubble.content }}</p>
              <p v-else-if="bubble.streaming" class="text-ink-soft">
                {{ bubble.thinking || '正在想…' }}
              </p>
            </div>

            <!-- Harness 的工具调用：它查了什么、拿到什么 -->
            <details
              v-if="bubble.steps.length"
              class="rounded-xl border border-border bg-bg-alt/60 px-2.5 py-1.5 text-xs"
              open
            >
              <summary class="cursor-pointer text-ink-soft">
                执行过程 · {{ bubble.steps.length }} 步
              </summary>
              <ul class="mt-1.5 space-y-1">
                <li v-for="(step, index) in bubble.steps" :key="index" class="flex gap-2">
                  <span :class="step.ok === false ? 'text-accent' : 'text-ink-soft'">
                    {{ step.ok === false ? '×' : step.ok ? '✓' : '·' }}
                  </span>
                  <span class="shrink-0">{{ step.label || step.tool }}</span>
                  <span class="truncate text-ink-soft">{{ step.summary }}</span>
                </li>
              </ul>
            </details>

            <!-- 原生路径（降级/历史）的动作条 -->
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
                <span v-else-if="!action.ok" class="text-ink-soft">{{ failureText(action) }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <div v-if="sending && !messages[messages.length - 1]?.streaming" class="flex justify-start">
        <div
          class="rounded-2xl rounded-bl-sm border border-border bg-surface px-3 py-2 text-sm text-ink-soft"
        >
          正在想…
        </div>
      </div>
    </div>

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
        <button class="btn btn-primary shrink-0" :disabled="!canSend" @click="send">发送</button>
      </div>
      <p class="hidden px-2 pt-1 text-[11px] text-ink-soft lg:block">
        Enter 发送 · Shift+Enter 换行 · 📎 发截图（会先识别内容，进确认队列）
      </p>
    </div>
  </div>
</template>
