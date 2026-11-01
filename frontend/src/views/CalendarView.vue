<script setup lang="ts">
/**
 * 日历页：月网格上同时显示日历事件和待办。
 *
 * 三条容易写错的地方（docs/18 有完整说明）：
 *  · 翻月靠 datesSet 拿 activeStart/activeEnd，不要在 onMounted 死拉当月
 *  · 拖拽按 kind 分流到两个不同接口，**失败必须 revert()**，否则界面骗人
 *  · 颜色全部在 eventContent 插槽里上，不要去压 .fc-* 的 class
 */

import type { CalendarOptions, DatesSetArg, EventClickArg, EventDropArg, EventInput } from '@fullcalendar/core'
import zhCn from '@fullcalendar/core/locales/zh-cn'
import dayGridPlugin from '@fullcalendar/daygrid'
import interactionPlugin from '@fullcalendar/interaction'
import FullCalendar from '@fullcalendar/vue3'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import { useCalendarStore, type CalendarEvent, type Tone } from '@/stores/calendar'

const router = useRouter()
const store = useCalendarStore()

/** 触屏设备拖拽要长按，体验很差。这类设备上关掉拖拽，改成点开条目改日期。 */
const coarsePointer = window.matchMedia('(pointer: coarse)').matches

/** 色板。只给 CSS 变量名，色值统一由 styles.css 的 @theme 管。 */
const solidTone: Record<Tone, string> = {
  danger: 'var(--color-danger)',
  warn: 'var(--color-warn)',
  accent: 'var(--color-accent)',
  deep: 'var(--color-accent-deep)',
  success: 'var(--color-success)',
  muted: 'var(--color-ink-soft)',
}
const softTone: Record<Tone, string> = {
  danger: 'var(--color-danger-soft)',
  warn: 'var(--color-warn-soft)',
  accent: 'var(--color-accent-soft)',
  deep: 'var(--color-accent-soft)',
  success: 'var(--color-success-soft)',
  muted: 'var(--color-bg-alt)',
}

/** 本地日期 → YYYY-MM-DD。不用 toISOString()，那是 UTC，晚上会差一天。 */
function dayOf(value: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
}

/** 轻提示。项目里没有全局 toast，这里只解决本页的一句话反馈。 */
const notice = ref('')
let noticeTimer: number | undefined
function say(text: string) {
  notice.value = text
  window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => (notice.value = ''), 2600)
}
onBeforeUnmount(() => window.clearTimeout(noticeTimer))

// ── 数据加载 ─────────────────────────────────────────────
// datesSet 在每次日期范围变化时触发（含首次渲染），正好是我们要的加载时机。
function handleDatesSet(arg: DatesSetArg) {
  const from = dayOf(arg.view.activeStart)
  // activeEnd 是"结束日的下一天零点"（开区间），减一天才是网格最后一天
  const end = arg.view.activeEnd
  const to = dayOf(new Date(end.getFullYear(), end.getMonth(), end.getDate() - 1))
  if (from === store.range.from && to === store.range.to) return
  void store.load({ from, to })
}

// ── 拖拽改期 ─────────────────────────────────────────────
async function handleEventDrop(info: EventDropArg) {
  const { kind, rawId } = info.event.extendedProps as CalendarEvent['extendedProps']
  const start = info.event.start
  const oldStart = info.oldEvent.start
  if (!start || !oldStart) {
    info.revert()
    return
  }

  try {
    if (kind === 'todo') {
      await store.patchTodoDue(rawId, dayOf(start))
    } else {
      await store.patchEventDate(rawId, dayOf(oldStart), dayOf(start))
    }
    say(`已改到 ${dayOf(start)}`)
  } catch (err) {
    // 失败必须还原，否则用户以为改成功了，刷新一看又回去了
    info.revert()
    say(err instanceof Error ? err.message : '改期失败，已还原')
  }
}

// ── 点击条目 ─────────────────────────────────────────────
const sheetOpen = ref(false)
const sheetDeadline = ref('')
const sheetSaving = ref(false)
/** 点开的那条待办。事件类不走这张卡。 */
const sheetTarget = ref<{ id: number; title: string; due: string | null } | null>(null)

function handleEventClick(arg: EventClickArg) {
  const props = arg.event.extendedProps as CalendarEvent['extendedProps']

  if (props.kind === 'todo') {
    sheetTarget.value = {
      id: props.rawId,
      title: arg.event.title,
      due: arg.event.start ? dayOf(arg.event.start) : null,
    }
    sheetDeadline.value = arg.event.start ? dayOf(arg.event.start) : ''
    sheetOpen.value = true
    return
  }

  // 自己建的事件：给一个能删的面板。以前点上去只会跳档案或弹一句话，
  // 建错了没有任何办法撤掉。
  if (props.canDelete) {
    eventTarget.value = { id: props.rawId, title: arg.event.title }
    return
  }

  // 事件类：挂在人身上的直接进档案，节日这类没主体的就给一句提示
  if (props.personId) {
    void router.push(`/persons/${props.personId}`)
    return
  }
  say(`${arg.event.title} · ${props.hint}`)
}

// ── 新建 / 删除事件 ──────────────────────────────────────
const createOpen = ref(false)
const createForm = reactive({ title: '', occurs_on: '', type: 'custom' })
const eventTarget = ref<{ id: number; title: string } | null>(null)
const eventBusy = ref(false)

const EVENT_TYPES = [
  { value: 'custom', label: '自定义' },
  { value: 'payment_due', label: '结款' },
]

function openCreate() {
  createForm.title = ''
  createForm.occurs_on = dayOf(new Date())
  createForm.type = 'custom'
  createOpen.value = true
}

async function saveCreate() {
  if (!createForm.title.trim()) {
    say('先写个标题')
    return
  }
  if (!createForm.occurs_on) {
    say('先选个日期')
    return
  }
  eventBusy.value = true
  try {
    await store.createEvent({
      title: createForm.title.trim(),
      occurs_on: createForm.occurs_on,
      type: createForm.type,
    })
    createOpen.value = false
    say('已加上')
  } catch (err) {
    say(err instanceof Error ? err.message : '没加上')
  } finally {
    eventBusy.value = false
  }
}

async function deleteEvent() {
  const target = eventTarget.value
  if (!target) return
  if (!confirm(`删掉「${target.title}」？`)) return
  eventBusy.value = true
  try {
    await store.deleteEvent(target.id)
    eventTarget.value = null
    say('已删除')
  } catch (err) {
    say(err instanceof Error ? err.message : '没删掉')
  } finally {
    eventBusy.value = false
  }
}

function closeSheet() {
  sheetOpen.value = false
  sheetTarget.value = null
}

async function saveSheet(clear = false) {
  const target = sheetTarget.value
  if (!target) return
  const nextDay = clear ? null : sheetDeadline.value || null
  if (!clear && !nextDay) {
    say('先选个日期')
    return
  }
  sheetSaving.value = true
  try {
    await store.patchTodoDue(target.id, nextDay)
    say(nextDay ? `已改到 ${nextDay}` : '日期已清掉')
    closeSheet()
  } catch (err) {
    say(err instanceof Error ? err.message : '保存失败')
  } finally {
    sheetSaving.value = false
  }
}

// ── FullCalendar 配置 ────────────────────────────────────
const calendarOptions = reactive<CalendarOptions>({
  plugins: [dayGridPlugin, interactionPlugin],
  initialView: 'dayGridMonth',
  locale: zhCn,
  height: 'auto',
  // 左侧导航已有月份入口，这里不再重复做一套视图切换
  headerToolbar: { left: 'prev,next today', center: 'title', right: '' },
  editable: !coarsePointer,
  dayMaxEvents: 3,
  moreLinkText: (count) => `还有 ${count} 条`,
  // 手机上格子窄，去掉时间轴的留白
  fixedWeekCount: false,
  events: [],
  datesSet: handleDatesSet,
  eventDrop: handleEventDrop,
  eventClick: handleEventClick,
})

// store 里的合并结果变了就直接灌给日历；不重拉接口，只是重新渲染
watch(
  () => store.events,
  (next) => {
    calendarOptions.events = next as unknown as EventInput[]
  },
  { immediate: true },
)

const errorText = computed(() => store.error ?? '')
</script>

<template>
  <div class="mx-auto max-w-4xl">
    <PageHeader
      title="日历"
      subtitle="生日、节日、节气、结款、待办，都在这一张网格上"
      action-label="新建"
      @action="openCreate()"
    />

    <div class="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
      <span v-if="store.loading">加载中…</span>
      <span v-if="errorText" class="text-danger">{{ errorText }}</span>
      <span v-if="store.overdueCount" class="tag bg-danger-soft text-danger">
        {{ store.overdueCount }} 条逾期
      </span>
      <span v-if="store.undatedCount" class="tag">{{ store.undatedCount }} 条待办没填日期</span>
      <span v-if="coarsePointer" class="tag">点小圆点看条目</span>
    </div>

    <div class="card overflow-hidden p-2 sm:p-3">
      <FullCalendar :options="calendarOptions">
        <!--
          格子里放什么，取决于屏幕：
          · 手机上一格只有 55px 宽，标题四个字就撑爆了（"更..."），
            所以退化成小圆点——苹果和谷歌的日历在手机上都这么干
          · 桌面放胶囊，和待办页的 .tag 视觉一致
        -->
        <template #eventContent="arg">
          <span
            v-if="coarsePointer"
            class="cal-dot"
            :style="{ background: solidTone[arg.event.extendedProps.tone as Tone] }"
            :title="arg.event.title"
          />
          <div
            v-else
            class="cal-pill"
            :class="arg.event.extendedProps.kind === 'todo' ? 'cal-pill-soft' : 'cal-pill-solid'"
            :style="
              arg.event.extendedProps.kind === 'todo'
                ? {
                    background: softTone[arg.event.extendedProps.tone as Tone],
                    color: solidTone[arg.event.extendedProps.tone as Tone],
                    borderColor: solidTone[arg.event.extendedProps.tone as Tone],
                  }
                : {
                    background: solidTone[arg.event.extendedProps.tone as Tone],
                    color: '#fff',
                    borderColor: solidTone[arg.event.extendedProps.tone as Tone],
                  }
            "
            :title="`${arg.event.title} · ${arg.event.extendedProps.hint}`"
          >
            {{ arg.event.title }}
          </div>
        </template>
      </FullCalendar>
    </div>

    <p class="mt-3 hidden text-xs text-ink-soft sm:block">
      实心是日历事件（生日深陶土、节日绿、节气灰、结款琥珀）；
      描边是待办（逾期红、今天琥珀、以后陶土）。
    </p>

    <!-- 点开待办：改日期 / 去待办页 -->
    <div
      v-if="sheetOpen && sheetTarget"
      class="fixed inset-0 z-30 flex items-end justify-center bg-ink/25 sm:items-center"
      @click.self="closeSheet"
    >
      <div class="card w-full max-w-sm rounded-b-none p-4 sm:rounded-b-[14px]">
        <div class="mb-1 text-sm font-medium">{{ sheetTarget.title }}</div>
        <div class="mb-3 text-xs text-ink-soft">改一下这条待办的日子</div>

        <input v-model="sheetDeadline" type="date" class="input" />

        <div class="mt-4 flex items-center gap-2">
          <button class="btn btn-primary flex-1" :disabled="sheetSaving" @click="saveSheet(false)">
            保存
          </button>
          <button class="btn btn-ghost" :disabled="sheetSaving" @click="saveSheet(true)">
            清掉日期
          </button>
        </div>

        <div class="mt-3 flex items-center justify-between text-xs">
          <RouterLink :to="{ name: 'todos' }" class="text-ink-soft hover:text-ink">
            去待办页看全部
          </RouterLink>
          <button class="text-ink-soft hover:text-ink" @click="closeSheet">关闭</button>
        </div>
      </div>
    </div>

    <!-- 新建事件 -->
    <div
      v-if="createOpen"
      class="fixed inset-0 z-30 flex items-end justify-center bg-ink/25 sm:items-center"
      @click.self="createOpen = false"
    >
      <div class="card w-full max-w-sm rounded-b-none p-4 sm:rounded-b-[14px]">
        <div class="mb-3 text-sm font-medium">新建一个日子</div>
        <div class="space-y-3">
          <input v-model="createForm.title" class="input" placeholder="比如：给王姐送茶" maxlength="100" />
          <input v-model="createForm.occurs_on" type="date" class="input" />
          <select v-model="createForm.type" class="input">
            <option v-for="item in EVENT_TYPES" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </div>
        <div class="mt-4 flex gap-2">
          <button class="btn btn-primary flex-1" :disabled="eventBusy" @click="saveCreate">
            保存
          </button>
          <button class="btn btn-ghost" :disabled="eventBusy" @click="createOpen = false">
            取消
          </button>
        </div>
      </div>
    </div>

    <!-- 点开自己建的事件：可以删 -->
    <div
      v-if="eventTarget"
      class="fixed inset-0 z-30 flex items-end justify-center bg-ink/25 sm:items-center"
      @click.self="eventTarget = null"
    >
      <div class="card w-full max-w-sm rounded-b-none p-4 sm:rounded-b-[14px]">
        <div class="mb-1 text-sm font-medium">{{ eventTarget.title }}</div>
        <div class="mb-4 text-xs text-ink-soft">自己建的事件可以删掉</div>
        <div class="flex gap-2">
          <button class="btn btn-ghost flex-1 text-danger" :disabled="eventBusy" @click="deleteEvent">
            删除
          </button>
          <button class="btn btn-primary flex-1" @click="eventTarget = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- 一句话反馈 -->
    <div
      v-if="notice"
      class="pointer-events-none fixed inset-x-0 bottom-24 z-40 flex justify-center px-4 lg:bottom-10"
    >
      <div class="rounded-full bg-ink/90 px-4 py-2 text-xs text-white shadow-lg">
        {{ notice }}
      </div>
    </div>
  </div>
</template>
