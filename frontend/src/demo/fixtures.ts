/**
 * 离线演示 fixture。
 *
 * 这不是 mock 测试：它是 V1 的「断网也能演示」能力。
 * 只在前端拦截，不请求后端、不调模型、不碰真实数据库。
 */

import type {
  AdminUserOut,
  CalendarItem,
  ChatReply,
  ConfirmationGroup,
  FactOut,
  LedgerOut,
  PersonDetail,
  PersonListItem,
  RawInputOut,
  SettingsOut,
  TagOut,
  TodayView,
  TodoOut,
  UserOut,
} from '@/api/types'

export const DEMO_KEY = 'keeper.demo'

export function demoEnabled(): boolean {
  if (import.meta.env.VITE_DEMO_MODE === 'true') return true
  if (typeof window === 'undefined') return false
  if (new URLSearchParams(window.location.search).get('demo') === '1') {
    sessionStorage.setItem(DEMO_KEY, '1')
  }
  return sessionStorage.getItem(DEMO_KEY) === '1'
}

export function enableDemo(): void {
  sessionStorage.setItem(DEMO_KEY, '1')
}

export function disableDemo(): void {
  sessionStorage.removeItem(DEMO_KEY)
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function isoDay(offset = 0): string {
  const date = new Date()
  date.setDate(date.getDate() + offset)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function isoAt(offset = 0, hour = 10): string {
  return `${isoDay(offset)}T${pad(hour)}:00:00+08:00`
}

const USER: UserOut = {
  id: 9001,
  username: 'demo',
  display_name: '演示账号',
  role: 'member',
}

const PERSONS: PersonListItem[] = [
  {
    id: 1,
    nickname: '王姐',
    avatar_path: null,
    stage: 'core',
    stage_source: 'manual',
    tags: ['偏好 · 白茶', '行业 · 建材'],
    last_interaction_at: isoAt(-2, 21),
  },
  {
    id: 2,
    nickname: '张总',
    avatar_path: null,
    stage: 'familiar',
    stage_source: 'manual',
    tags: ['近况 · 女儿留学'],
    last_interaction_at: isoAt(-7, 15),
  },
  {
    id: 3,
    nickname: '小赵',
    avatar_path: null,
    stage: 'new',
    stage_source: 'auto',
    tags: [],
    last_interaction_at: isoAt(-15, 11),
  },
]

const TAGS: TagOut[] = [
  { id: 1, person_id: 1, key: 'preference', value: '白茶', confidence: 0.9, expires_at: null },
  { id: 2, person_id: 1, key: 'industry', value: '建材', confidence: 0.9, expires_at: null },
  { id: 3, person_id: 2, key: 'situation', value: '女儿留学', confidence: 0.8, expires_at: null },
]

const DETAIL: PersonDetail = {
  id: 1,
  nickname: '王姐',
  aliases: ['王姐', '王总'],
  platform_ids: {},
  avatar_path: null,
  contact: {},
  note: '做建材生意，喜欢白茶，别问体重。',
  created_at: isoAt(-120, 9),
  updated_at: isoAt(-2, 21),
  relationship: {
    roles: ['ge_jie', 'customer'],
    stage: 'core',
    stage_source: 'manual',
    birthday_type: 'solar',
    birthday_solar: '1988-03-12',
    birthday_lunar: null,
    gift_budget_min: '300.00',
    gift_budget_max: '800.00',
    taboo: ['体重', '年龄'],
    total_in: '12400.00',
    total_out: '3200.00',
    last_interaction_at: isoAt(-2, 21),
  },
  tags: ['偏好 · 白茶', '行业 · 建材'],
  counts: { facts: 3, ledger: 2, events: 2, todos: 1 },
  summary: '王姐做建材生意，是核心客户。喜欢白茶，送礼预算 300–800；不要聊体重和年龄。',
  summary_stale: false,
}

const FACTS: FactOut[] = [
  {
    id: 101,
    person_id: 1,
    content: '喜欢喝白茶，太甜的不喝',
    category: 'preference',
    weight: 1,
    confidence: 0.95,
    evidence: '最近在喝白茶，太甜的喝不了',
    status: 'confirmed',
    last_confirmed_at: isoAt(-30, 20),
    created_at: isoAt(-30, 20),
  },
  {
    id: 102,
    person_id: 1,
    content: '做建材生意，主要做工程渠道',
    category: 'structure',
    weight: 1,
    confidence: 0.9,
    evidence: '我们主要做工程',
    status: 'confirmed',
    last_confirmed_at: isoAt(-25, 19),
    created_at: isoAt(-25, 19),
  },
  {
    id: 103,
    person_id: 1,
    content: '不要聊体重和年龄',
    category: 'taboo',
    weight: 1,
    confidence: 0.9,
    evidence: '别问我体重',
    status: 'confirmed',
    last_confirmed_at: isoAt(-20, 18),
    created_at: isoAt(-20, 18),
  },
]

const LEDGER: LedgerOut[] = [
  {
    id: 201,
    person_id: 1,
    direction: 'out',
    kind: 'gift',
    amount: '520.00',
    currency: 'CNY',
    occurred_at: isoDay(-30),
    due_date: null,
    settled_at: null,
    note: '生日礼物',
    created_at: isoAt(-30, 12),
  },
  {
    id: 202,
    person_id: 1,
    direction: 'in',
    kind: 'order',
    amount: '1200.00',
    currency: 'CNY',
    occurred_at: isoDay(-12),
    due_date: isoDay(2),
    settled_at: null,
    note: '样品款',
    created_at: isoAt(-12, 12),
  },
]

const TODAY: TodayView = {
  date: isoDay(),
  tasks: [
    {
      type: 'reminder',
      title: '王姐生日还有 3 天',
      subtitle: '去年送过白茶礼盒；今年可以换成茶具',
      person_id: 1,
      source_id: 301,
      due: isoDay(3),
      priority: 1,
      unsent: false,
      unsent_reason: null,
    },
    {
      type: 'todo',
      title: '给小赵回私信',
      subtitle: '昨天问过报价，还没回',
      person_id: 3,
      source_id: 401,
      due: isoDay(),
      priority: 2,
    },
  ],
  tasks_more: 0,
  pending_count: 2,
  pending_preview: { fact_id: 501, nickname: '张总', content: '女儿在澳洲读研' },
  recent: [
    { kind: 'fact', person_id: 1, nickname: '王姐', content: '最近在喝白茶', at: isoAt(-2, 21) },
    { kind: 'ledger', person_id: 2, nickname: '张总', content: '收到样品款 1200', at: isoAt(-3, 16) },
  ],
  degraded: false,
}

const TODO_LIST: TodoOut[] = [
  {
    id: 401,
    person_id: 3,
    title: '给小赵回私信',
    detail: '昨天问过报价',
    source: 'manual',
    source_id: null,
    ai_reason: null,
    due_date: isoDay(),
    status: 'open',
    done_at: null,
    created_at: isoAt(-1, 9),
  },
  {
    id: 402,
    person_id: 1,
    title: '准备王姐生日礼物',
    detail: '预算 300–800',
    source: 'reminder',
    source_id: 301,
    ai_reason: '生日还有 3 天',
    due_date: isoDay(2),
    status: 'open',
    done_at: null,
    created_at: isoAt(-2, 9),
  },
]

const CONFIRMATIONS: ConfirmationGroup[] = [
  {
    person_id: 2,
    nickname: '张总',
    items: [
      {
        id: 501,
        content: '女儿在澳洲读研',
        category: 'situation',
        confidence: 0.86,
        evidence: '我女儿在澳洲读研',
        raw_input_id: 601,
        thumb_url: null,
        kind: 'fact',
        created_at: isoAt(-1, 22),
      },
      {
        id: 502,
        content: '下个月要换办公室',
        category: 'situation',
        confidence: 0.72,
        evidence: '下个月搬办公室',
        raw_input_id: 601,
        thumb_url: null,
        kind: 'fact',
        created_at: isoAt(-1, 22),
      },
    ],
  },
]

const CALENDAR: CalendarItem[] = [
  {
    day: isoDay(),
    title: '给小赵回私信',
    type: 'custom',
    person_id: 3,
    event_id: 701,
    lunar: false,
  },
  {
    day: isoDay(3),
    title: '王姐生日',
    type: 'birthday',
    person_id: 1,
    event_id: 702,
    lunar: false,
  },
  {
    day: isoDay(8),
    title: '中秋节',
    type: 'festival',
    person_id: null,
    event_id: null,
    lunar: true,
  },
]

const RAW_INPUTS: RawInputOut[] = [
  {
    id: 601,
    kind: 'screenshot',
    status: 'done',
    person_id: 2,
    original_filename: '张总-聊天截图.png',
    error_msg: null,
    created_at: isoAt(-1, 22),
    processed_at: isoAt(-1, 22),
  },
  {
    id: 602,
    kind: 'text',
    status: 'done',
    person_id: 1,
    original_filename: null,
    error_msg: null,
    created_at: isoAt(-2, 20),
    processed_at: isoAt(-2, 20),
  },
]

const SETTINGS: SettingsOut = {
  keep_original_images: true,
  channels: [
    { id: 1, kind: 'wecom', name: '企业微信', priority: 10, is_active: true, has_secret: true },
  ],
}

const ADMIN_USERS: AdminUserOut[] = [
  {
    id: 9001,
    username: 'demo',
    display_name: '演示账号',
    role: 'owner',
    is_active: true,
    last_login_at: isoAt(0, 9),
    created_at: isoAt(-100, 9),
  },
  {
    id: 9002,
    username: 'member',
    display_name: '协作成员',
    role: 'member',
    is_active: true,
    last_login_at: null,
    created_at: isoAt(-20, 9),
  },
]

const CHAT: ChatReply = {
  reply: '王姐喜欢白茶，太甜的不喝。最近一次互动是两天前。',
  intent: 'query',
  person_id: 1,
  nickname: '王姐',
  facts: [{ id: 101, content: '喜欢喝白茶', evidence: '最近在喝白茶' }],
  raw_input_id: null,
  pending_count: 0,
}

/** 返回 undefined 表示这个路径没写 fixture，让调用方走真实请求。 */
export function demoEnvelope(
  path: string,
  method: string,
  _query?: Record<string, unknown>,
  body?: unknown,
): { data: unknown; meta?: Record<string, unknown> } | undefined {
  const clean = path.split('?')[0]
  const upper = method.toUpperCase()

  if (clean === '/auth/me') return { data: USER }
  if (clean === '/auth/logout' || clean === '/auth/password') return { data: { ok: true } }
  if (clean === '/today') return { data: TODAY }
  if (clean === '/persons') return { data: PERSONS, meta: { total: PERSONS.length } }
  if (clean === '/persons/1') return { data: DETAIL }
  if (clean === '/persons/1/facts') return { data: FACTS }
  if (clean === '/persons/1/ledger') return { data: LEDGER }
  if (clean === '/persons/1/timeline')
    return {
      data: [
        { at: isoAt(-2, 21), precision: 'minute', kind: 'fact', title: '最近在喝白茶', detail: null, amount: null, direction: null },
        { at: isoAt(-3, 16), precision: 'minute', kind: 'ledger', title: '收到样品款', detail: null, amount: '1200.00', direction: 'in' },
      ],
    }
  if (clean === '/persons/1/summary/rewrite') return { data: { person_id: 1, summary: DETAIL.summary, stale: false } }
  if (clean === '/tags') return { data: TAGS }
  if (clean === '/confirmations') return { data: CONFIRMATIONS }
  if (clean === '/events') return { data: CALENDAR }
  if (clean === '/todos') return { data: TODO_LIST }
  if (clean === '/raw-inputs') return { data: RAW_INPUTS }
  if (clean === '/settings') return { data: SETTINGS }
  if (clean === '/admin/users') return { data: ADMIN_USERS }
  if (clean === '/chat' && upper === 'POST')
    return { data: CHAT }
  if (clean.startsWith('/persons/1') && upper !== 'GET') return { data: DETAIL }
  if (clean.startsWith('/persons') && upper !== 'GET') return { data: { ok: true } }
  if (clean.startsWith('/todos/') && upper !== 'GET') return { data: TODO_LIST[0] }
  if (clean.startsWith('/reminders/') || clean.startsWith('/confirmations/'))
    return { data: { ok: true } }
  if (clean.startsWith('/notification-channels')) return { data: SETTINGS.channels }
  if (upper !== 'GET') return { data: { ok: true } }
  if (body !== undefined) return { data: { ok: true } }
  return undefined
}
