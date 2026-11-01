/**
 * 日历数据。
 *
 * 日历页要同时显示两类东西，它们来自两个不同接口：
 *   · /api/events  节日、节气、生日（其中一部分是算出来的派生条目）
 *   · /api/todos   待办，只有带 due_date 的才能落在格子里
 *
 * 这里把两者合并成 FullCalendar 能吃的一份数组，并负责改期。
 *
 * 三条约定（违反会立刻出问题，见 docs/18）：
 *  1. 合并后的 id 必须带前缀 `todo-` / `event-`。
 *     两张表的自增 id 都是小整数，直接混用必然撞车，撞了后果是"拖 A 改了 B"。
 *  2. 改期成功后只改本地数组，**不重新 load**。
 *     否则后端排序一抖，屏幕就跳（HANDOFF2 §六）。
 *  3. 颜色不落库，前端按 type / 到期情况派生。todos 表没有 priority 列。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api/client'
import type { CalendarItem, TodoOut } from '@/api/types'

/** 一条条目是待办还是日历事件。拖拽分流靠它。 */
export type ItemKind = 'todo' | 'event'

/** 色档。具体颜色交给 CSS 变量，这里只给档位名，不写死色值。 */
export type Tone = 'danger' | 'warn' | 'accent' | 'deep' | 'success' | 'muted'

export interface DateRange {
  from: string
  to: string
}

/** 合并后喂给 FullCalendar 的单条形状。 */
export interface CalendarEvent {
  id: string
  title: string
  start: string
  allDay: boolean
  backgroundColor: string
  borderColor: string
  /** 只有一个判断：能改的才能拖。派生条目（节日、节气、关系表生日）拖了没意义。 */
  editable: boolean
  extendedProps: {
    kind: ItemKind
    /** 真身 id：todo 的 id 或 event 的 id */
    rawId: number
    personId: number | null
    tone: Tone
    /** 悬停提示用：农历 / 逾期这类补充说明 */
    hint: string
  }
}

/**
 * 事件类型 → 色档。
 * birthday 用 deep（比 accent 深一档），这样和"以后到期的待办"能分开。
 */
const eventTone: Record<string, Tone> = {
  birthday: 'deep',
  festival: 'success',
  solar_term: 'muted',
  payment_due: 'warn',
  custom: 'accent',
}

const eventLabel: Record<string, string> = {
  birthday: '生日',
  festival: '节日',
  solar_term: '节气',
  payment_due: '结款',
  custom: '自定义',
}

/** 本地今天，格式 YYYY-MM-DD。不能用 toISOString()，那是 UTC，会差一天。 */
function todayString(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export const useCalendarStore = defineStore('calendar', () => {
  const items = ref<CalendarItem[]>([])
  const todos = ref<TodoOut[]>([])
  const range = ref<DateRange>({ from: '', to: '' })
  const loading = ref(false)
  const error = ref<string | null>(null)

  /**
   * 请求序号。用户快速翻月时，先发的请求可能后到，
   * 用序号把过期响应丢掉，否则会看到上个月的格子配这个月的标题。
   */
  let requestSeq = 0

  /** 拉一个区间的数据。两个接口并发，都不带缓存。 */
  async function load(next: DateRange): Promise<void> {
    const seq = ++requestSeq
    loading.value = true
    error.value = null
    try {
      const [nextItems, nextTodos] = await Promise.all([
        api.get<CalendarItem[]>('/events', { from: next.from, to: next.to }),
        // 待办接口没有区间查询，前端拿全部 open 后自己按日期落格（20 人规模，量很小）
        api.get<TodoOut[]>('/todos', { status: 'open' }),
      ])
      if (seq !== requestSeq) return // 已经有更新的请求了，这份作废
      items.value = nextItems
      todos.value = nextTodos
      range.value = next
    } catch (err) {
      if (seq !== requestSeq) return
      error.value = err instanceof Error ? err.message : '加载失败'
    } finally {
      if (seq === requestSeq) loading.value = false
    }
  }

  /** 改期：待办。成功后只替换本地那一条，不重拉。传 null 表示清掉日期。 */
  async function patchTodoDue(todoId: number, day: string | null): Promise<void> {
    const updated = await api.patch<TodoOut>(`/todos/${todoId}`, { due_date: day })
    const index = todos.value.findIndex((todo) => todo.id === todoId)
    if (index >= 0) todos.value[index] = updated
  }

  /** 改期：自定义事件。借 events 的 occurs_on 字段。 */
  async function patchEventDate(eventId: number, oldDay: string, newDay: string): Promise<void> {
    await api.patch(`/events/${eventId}`, { occurs_on: newDay })
    // 同样只改本地，不重拉：重拉会让整张网格重绘、屏幕跳一下。
    // 只挪"被拖的那一条"，rrule 事件在服务端会整条规则平移，下次翻月自然对齐。
    items.value = items.value.map((item) =>
      item.event_id === eventId && item.day === oldDay ? { ...item, day: newDay } : item,
    )
  }

  /** 待办按到期情况分三档。今天之后统一 accent，不区分远近。 */
  function todoTone(todo: TodoOut): Tone {
    if (!todo.due_date) return 'muted'
    const today = todayString()
    if (todo.due_date < today) return 'danger'
    if (todo.due_date === today) return 'warn'
    return 'accent'
  }

  /**
   * 合并成 FullCalendar 事件数组。
   * 待办没有 due_date 的不进网格（没日子没法落格），页脚单独提示条数。
   */
  const events = computed<CalendarEvent[]>(() => {
    const today = todayString()

    const fromEvents: CalendarEvent[] = items.value.map((item, index) => {
      const tone = eventTone[item.type] ?? 'accent'
      const label = eventLabel[item.type] ?? item.type
      // 农历生日的公历日期是每年算出来的，改 occurs_on 不生效（刷新就弹回去），
      // 所以这类条目即使有 event_id 也不能拖 —— 拖了等于骗用户。
      const computedEveryYear = item.type === 'birthday' && item.lunar
      const writable = item.event_id !== null && !computedEveryYear
      return {
        // 派生条目没有 event_id，用 type + day + index 拼一个稳定 id，避免 key 冲突
        id: writable ? `event-${item.event_id}` : `derived-${item.type}-${item.day}-${index}`,
        title: item.title,
        start: item.day,
        allDay: true,
        backgroundColor: 'transparent',
        borderColor: 'transparent',
        editable: writable,
        extendedProps: {
          kind: 'event',
          rawId: item.event_id ?? 0,
          personId: item.person_id,
          tone,
          hint: `${label}${item.lunar ? ' · 农历' : ''}`,
        },
      }
    })

    const fromTodos: CalendarEvent[] = todos.value
      .filter((todo) => todo.due_date !== null)
      .map((todo) => {
        const tone = todoTone(todo)
        const due = todo.due_date as string
        const hint = due < today ? '已逾期' : due === today ? '今天到期' : '待办'
        return {
          id: `todo-${todo.id}`,
          title: todo.title,
          start: due,
          allDay: true,
          backgroundColor: 'transparent',
          borderColor: 'transparent',
          // 待办一律可拖：改期是它唯一的编辑动作，不涉及别的字段
          editable: true,
          extendedProps: { kind: 'todo', rawId: todo.id, personId: todo.person_id, tone, hint },
        }
      })

    return [...fromEvents, ...fromTodos]
  })

  /** 没有截止日的待办数。它们不在网格上，得让用户知道有这么多条"悬着"。 */
  const undatedCount = computed(() => todos.value.filter((todo) => !todo.due_date).length)

  /** 逾期未完成条数，页头用。 */
  const overdueCount = computed(() => {
    const today = todayString()
    return todos.value.filter((todo) => todo.due_date !== null && todo.due_date < today).length
  })

  return {
    items,
    todos,
    range,
    loading,
    error,
    events,
    undatedCount,
    overdueCount,
    load,
    patchTodoDue,
    patchEventDate,
  }
})
