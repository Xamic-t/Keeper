<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { RawInputOut } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'

const items = ref<RawInputOut[]>([])
const text = ref('')
const uploading = ref(false)
const message = ref('')

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
  } catch (err) {
    message.value = err instanceof Error ? err.message : '上传失败'
  } finally {
    uploading.value = false
  }
}

async function sendText() {
  if (!text.value.trim()) return
  await api.post('/ingest/text', { text: text.value.trim() })
  text.value = ''
  message.value = '已提交，正在后台识别…'
  await load()
}

function statusText(status: string) {
  if (status === 'done') return '已识别'
  if (status === 'processing') return '识别中'
  if (status === 'failed') return '失败'
  return '排队中'
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <PageHeader title="投喂" subtitle="把聊天截图丢进来，系统替你记住" />

    <section class="card mb-4 p-4">
      <h2 class="mb-2 text-sm font-medium">上传截图</h2>
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

    <p class="mb-3 text-sm text-ink-soft">识别结果会出现在「确认」里：系统先抽出事实和往来，你点一遍 ✓ 才进档案。</p>

    <ul v-if="items.length" class="mt-4 space-y-2">
      <li
        v-for="item in items"
        :key="item.id"
        class="card flex items-center justify-between px-4 py-3 text-sm"
      >
        <span>{{ item.kind === 'text' ? '一句话投喂' : '截图' }}</span>
        <span class="tag">{{ statusText(item.status) }}</span>
      </li>
    </ul>
  </div>
</template>
