<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { RawInputOut } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
const items = ref<RawInputOut[]>([])
const text = ref('')
const uploading = ref(false)
const message = ref('')

// 轮询：识别是后台跑的，界面不主动问就永远停在「识别中…」。
// 上限 20 次 × 1.5s ≈ 30 秒：够跑完一张图，也不会因为接口异常一直打。
const POLL_INTERVAL_MS = 1500
const POLL_MAX_ROUNDS = 20
let pollTimer: number | null = null

function stopPolling() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
}

function isBusy(item: RawInputOut) {
  return item.status === 'pending' || item.status === 'processing'
}

/** 有在处理的就定期重拉列表；全部落定就停。 */
function startPolling() {
  stopPolling()
  let rounds = 0

  const tick = async () => {
    rounds += 1
    try {
      await load()
    } catch {
      // 取列表失败（掉线/401 已由 client 处理）就停下，不要越攒越多请求
      stopPolling()
      return
    }
    if (!items.value.some(isBusy)) return
    if (rounds >= POLL_MAX_ROUNDS) {
      message.value = '还在识别，等一会儿刷新就能看到结果'
      return
    }
    pollTimer = window.setTimeout(tick, POLL_INTERVAL_MS)
  }

  pollTimer = window.setTimeout(tick, POLL_INTERVAL_MS)
}

async function load() {
  items.value = await api.get<RawInputOut[]>('/raw-inputs', { limit: 20 })
}

async function uploadImages(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (!files.length) return

  uploading.value = true
  message.value = ''
  try {
    const body = new FormData()
    files.forEach((file) => body.append('files', file))
    const result = await api.post<{ items: { raw_input_id: number | null }[]; queued: number }>(
      '/ingest/image',
      body,
    )
    message.value = `已接收 ${result.queued} 张，正在后台识别…`
    input.value = ''
    await load()
    startPolling()
  } catch (err) {
    message.value = err instanceof Error ? err.message : '上传失败'
  } finally {
    uploading.value = false
  }
}

async function sendText() {
  if (!text.value.trim()) return
  try {
    await api.post('/ingest/text', { text: text.value.trim() })
    text.value = ''
    message.value = '已提交，正在后台识别…'
    await load()
    startPolling()
  } catch (err) {
    message.value = err instanceof Error ? err.message : '提交失败'
  }
}

/** 有原始文件名就显示名字，老数据没存名字的退化成时间 */
function displayName(item: RawInputOut) {
  if (item.original_filename) return item.original_filename
  return item.kind === 'text' ? '一句话投喂' : `截图 ${formatTime(item.created_at)}`
}

function formatTime(iso: string) {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  const today = new Date()
  const sameDay =
    at.getFullYear() === today.getFullYear() &&
    at.getMonth() === today.getMonth() &&
    at.getDate() === today.getDate()
  const clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`
  return sameDay ? `今天 ${clock}` : `${at.getMonth() + 1}月${at.getDate()}日 ${clock}`
}

/** 只在没完成／出错时才提示状态，正常的不打扰 */
function statusHint(item: RawInputOut) {
  if (item.status === 'failed') return item.error_msg || '识别失败'
  if (item.status === 'processing') return '识别中…'
  if (item.status === 'pending') return '排队中…'
  return ''
}

function thumbUrl(item: RawInputOut) {
  return item.kind === 'screenshot' ? `/api/raw-inputs/${item.id}/file` : ''
}

onMounted(async () => {
  try {
    await load()
  } catch (err) {
    message.value = err instanceof Error ? err.message : '列表没取到'
    return
  }
  // 进页面时如果有上次没跑完的，接着盯
  if (items.value.some(isBusy)) startPolling()
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <PageHeader
      title="投喂"
      subtitle="把聊天截图丢进来，系统替你记住"
      action-label="切换到对话"
      @action="router.push({ name: 'chat' })"
    />

    <section class="card mb-4 p-4">
      <h2 class="mb-2 text-sm font-medium">批量上传截图</h2>
      <p class="mb-3 text-xs text-ink-soft">
        一次最多 10 张，单张不超过 8MB，支持 jpg / png / webp。同一张图不会重复处理。
      </p>
      <label class="btn btn-primary inline-flex">
        {{ uploading ? '上传中…' : '选择图片' }}
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          class="hidden"
          :disabled="uploading"
          @change="uploadImages"
        />
      </label>
    </section>

    <section class="card mb-4 p-4">
      <h2 class="mb-2 text-sm font-medium">或者直接说一句话</h2>
      <textarea
        v-model="text"
        class="input h-24 resize-none"
        placeholder="比如：今天王姐说她女儿模考进了前二十"
        maxlength="2000"
      ></textarea>
      <button class="btn btn-primary mt-3" @click="sendText">提交</button>
    </section>

    <p v-if="message" class="mb-4 text-sm text-ink-soft">{{ message }}</p>

    <p class="mb-3 text-sm text-ink-soft">
      识别结果会出现在「确认」里：系统先抽出事实和往来，你点一遍 ✓ 才进档案。
    </p>

    <ul v-if="items.length" class="mt-4 space-y-2">
      <li v-for="item in items" :key="item.id" class="card flex items-center gap-3 px-3 py-3">
        <!-- 截图直接给缩略图，点开看原图 -->
        <a
          v-if="thumbUrl(item)"
          :href="thumbUrl(item)"
          target="_blank"
          class="block h-16 w-12 shrink-0 overflow-hidden rounded-md bg-bg-alt"
        >
          <img
            :src="thumbUrl(item)"
            alt=""
            loading="lazy"
            class="h-full w-full object-cover"
          />
        </a>
        <div
          v-else
          class="flex h-16 w-12 shrink-0 items-center justify-center rounded-md bg-bg-alt text-lg text-ink-soft"
        >
          ✎
        </div>

        <div class="min-w-0 flex-1">
          <div class="truncate text-sm">{{ displayName(item) }}</div>
          <div class="mt-0.5 text-xs text-ink-soft">{{ formatTime(item.created_at) }}</div>
          <div v-if="statusHint(item)" class="mt-1 text-xs text-warn">{{ statusHint(item) }}</div>
        </div>

        <a
          v-if="thumbUrl(item)"
          :href="thumbUrl(item)"
          target="_blank"
          class="shrink-0 text-xs text-ink-soft hover:text-accent"
        >
          原图
        </a>
      </li>
    </ul>
  </div>
</template>
