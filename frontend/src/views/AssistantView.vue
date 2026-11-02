<script setup lang="ts">
/**
 * 助理页：把 Harness（dsh）的 Web UI 嵌进来。
 *
 * 为什么不自己写一套对话界面：对话、工具调用、上下文管理都在 Harness 里，
 * 主站再实现一遍只会得到两个行为不一致的助手。
 *
 * 三个现实（都实测过）：
 *  · 3080 可能没起 —— 用户没跑 ./run.sh
 *  · 就算起了，直接开 http://127.0.0.1:3080 也是 **401**：
 *    dsh 用 `?token=...` 里的启动令牌换 cookie，令牌每次启动都不一样
 *  · 还有可能被 X-Frame-Options 拦住，iframe 里一片白
 *
 * 所以：要求用户把 dsh 打印出来的那条带 token 的地址粘一次，存在当前标签页 sessionStorage；
 * 没粘过就明确说"去启动、去复制"，而不是给一块 401 白板。
 * 不用 localStorage：token 是凭据，关闭标签页就清掉，能少一个长期泄露面。
 */

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import PageHeader from '@/components/PageHeader.vue'

const STORAGE_KEY = 'keeper.harness.url'
const defaultUrl = import.meta.env.VITE_HARNESS_URL ?? 'http://127.0.0.1:3080'

const savedUrl = ref(sessionStorage.getItem(STORAGE_KEY) ?? '')
const inputUrl = ref(savedUrl.value || defaultUrl)
const loaded = ref(false)
const unreachable = ref(false)
const saved = ref(Boolean(savedUrl.value))
let timer: number | undefined

/** 有 token 才能拿到会话，否则 dsh 直接 401 */
const hasToken = computed(() => /[?&]token=/.test(savedUrl.value))
/** 取 origin 用来探测服务在不在（带 token 的地址不能直接探，会换掉 cookie） */
const origin = computed(() => {
  try {
    return new URL(savedUrl.value || defaultUrl).origin
  } catch {
    return defaultUrl
  }
})

/**
 * iframe 的 load 事件在**连接被拒时也会触发**（拿到的是浏览器错误页），
 * 所以不能拿它判断"起来了"。用 no-cors fetch 探一下：
 * 服务在 → 拿到 opaque 响应；没起 → 直接抛。
 */
async function isUp(): Promise<boolean> {
  try {
    await fetch(origin.value, { mode: 'no-cors', cache: 'no-store' })
    return true
  } catch {
    return false
  }
}

async function check() {
  unreachable.value = saved.value && hasToken.value ? !(await isUp()) : false
}

function onLoad() {
  loaded.value = true
  window.clearTimeout(timer)
}

function saveUrl() {
  const value = inputUrl.value.trim()
  if (!value) return
  sessionStorage.setItem(STORAGE_KEY, value)
  savedUrl.value = value
  saved.value = true
  loaded.value = false
  void check()
}

function forget() {
  sessionStorage.removeItem(STORAGE_KEY)
  savedUrl.value = ''
  saved.value = false
  inputUrl.value = defaultUrl
  loaded.value = false
  unreachable.value = false
}

async function retry() {
  loaded.value = false
  await check()
  const frame = document.getElementById('harness-frame') as HTMLIFrameElement | null
  if (frame && !unreachable.value) {
    const separator = savedUrl.value.includes('?') ? '&' : '?'
    frame.src = `${savedUrl.value}${separator}t=${Date.now()}`
  }
}

function openExternal() {
  window.open(savedUrl.value || defaultUrl, '_blank', 'noreferrer')
}

onMounted(async () => {
  await check()
  timer = window.setTimeout(() => {
    if (saved.value && hasToken.value && !loaded.value) unreachable.value = true
  }, 5000)
})

onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="mx-auto max-w-4xl">
    <PageHeader
      title="助理"
      subtitle="能查你的档案、告诉你今天该联系谁"
      action-label="新窗口打开"
      @action="openExternal"
    />

    <!-- 还没粘地址：说清楚要做什么，不要给一块 401 白板 -->
    <div v-if="!saved || !hasToken" class="card space-y-3 p-4">
      <p class="text-sm">助理是独立进程，需要单独启动。启动后它会打印一条带 token 的地址。</p>
      <ol class="list-decimal space-y-2 pl-5 text-sm text-ink-soft">
        <li>
          在项目根目录跑
          <code class="rounded bg-bg-alt px-1 text-xs">cd harness &amp;&amp; ./run.sh</code>
        </li>
        <li>复制它打印的 <code class="rounded bg-bg-alt px-1 text-xs">http://127.0.0.1:3080/?token=...</code></li>
        <li>粘到下面，只在这个标签页里记住（关闭标签页即清除）</li>
      </ol>

      <input
        v-model="inputUrl"
        class="input"
        placeholder="http://127.0.0.1:3080/?token=..."
        aria-label="助理地址"
      />
      <p v-if="saved && !hasToken" class="text-xs text-warn">
        这个地址里没有 <code>token</code>，dsh 会拒绝访问（401）。要带 token 的那条。
      </p>
      <div class="flex gap-2">
        <button class="btn btn-primary" @click="saveUrl">保存并打开</button>
        <a class="btn btn-ghost" :href="defaultUrl" target="_blank" rel="noreferrer">
          先在新窗口试试
        </a>
      </div>
    </div>

    <template v-else>
      <div v-if="unreachable" class="card space-y-3 p-4">
        <p class="text-sm">连不上助理。它可能没在跑，或者已经重启（token 会变）。</p>
        <pre class="overflow-x-auto rounded-lg bg-bg-alt p-3 text-xs">cd harness && ./run.sh</pre>
        <div class="flex gap-2">
          <button class="btn btn-primary" @click="retry">重试</button>
          <button class="btn btn-ghost" @click="forget">换一个地址</button>
        </div>
      </div>

      <div v-show="!unreachable" class="card overflow-hidden p-0">
        <div v-if="!loaded" class="p-4 text-sm text-ink-soft">正在连接助理…</div>
        <iframe
          id="harness-frame"
          :src="savedUrl"
          class="h-[70vh] w-full border-0"
          title="Keeper 助理"
          @load="onLoad"
        ></iframe>
      </div>

      <div class="mt-3 flex items-center justify-between text-xs text-ink-soft">
        <span>助理是只读的：能查档案、待办、往来，但不能替你改任何东西。</span>
        <button class="hover:text-ink" @click="forget">换地址</button>
      </div>
    </template>
  </div>
</template>
