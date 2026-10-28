/** 后端返回的数据结构。与 docs/10-接口响应规范.md 一一对应。 */

export interface UserOut {
  id: number
  username: string
  display_name: string | null
  role: string
}

export interface LoginData {
  user: UserOut
  csrf_token: string
}

export interface PageMeta {
  total: number
  page: number
  size: number
  total_pages: number
  has_next: boolean
}

export interface PersonListItem {
  id: number
  nickname: string
  avatar_path: string | null
  stage: string
  stage_source: string
  tags: string[]
  last_interaction_at: string | null
}

export interface RelationshipOut {
  roles: string[]
  stage: string
  stage_source: string
  birthday_type: string | null
  birthday_solar: string | null
  birthday_lunar: Record<string, unknown> | null
  gift_budget_min: string | null
  gift_budget_max: string | null
  taboo: string[]
  total_in: string
  total_out: string
  last_interaction_at: string | null
}

export interface PersonOut {
  id: number
  nickname: string
  aliases: string[]
  platform_ids: Record<string, unknown>
  avatar_path: string | null
  contact: Record<string, unknown>
  note: string | null
  created_at: string
  updated_at: string
  relationship: RelationshipOut
}

export interface PersonDetail extends PersonOut {
  tags: string[]
  counts: { facts: number; ledger: number; events: number; todos: number }
  summary: string | null
  summary_stale: boolean
}

export interface FactOut {
  id: number
  person_id: number
  content: string
  category: string
  weight: number
  confidence: number
  evidence: string | null
  status: string
  last_confirmed_at: string | null
  created_at: string
}

export interface LedgerOut {
  id: number
  person_id: number
  direction: string
  kind: string
  amount: string
  currency: string
  occurred_at: string
  due_date: string | null
  settled_at: string | null
  note: string | null
  created_at: string
}

export interface CalendarItem {
  day: string
  title: string
  type: string
  person_id: number | null
  event_id: number | null
  lunar: boolean
}

export interface TodoOut {
  id: number
  person_id: number | null
  title: string
  detail: string | null
  source: string
  source_id: number | null
  ai_reason: string | null
  due_date: string | null
  status: string
  done_at: string | null
  created_at: string
}

export interface TaskItem {
  type: 'reminder' | 'todo'
  title: string
  subtitle: string
  person_id: number | null
  source_id: number | null
  due: string | null
  priority: number
}

export interface TodayView {
  date: string
  tasks: TaskItem[]
  tasks_more: number
  pending_count: number
  pending_preview: { fact_id: number; nickname: string; content: string } | null
  recent: { kind: string; person_id: number; nickname: string; content: string; at: string }[]
  degraded: boolean
}

export interface ConfirmationItem {
  id: number
  content: string
  category: string
  confidence: number
  evidence: string | null
  raw_input_id: number | null
  thumb_url: string | null
  kind: string
  created_at: string
}

export interface ConfirmationGroup {
  person_id: number | null
  nickname: string
  items: ConfirmationItem[]
}

export interface ReminderOut {
  id: number
  event_id: number
  person_id: number | null
  remind_on: string
  level: string
  channel: string | null
  payload: {
    headline?: string
    suggestion?: string
    reasons?: string[]
    script?: string
    degraded?: boolean
  } | null
  status: string
  sent_at: string | null
  created_at: string
}

export interface SettingsOut {
  keep_original_images: boolean
  channels: {
    id: number
    kind: string
    name: string
    priority: number
    is_active: boolean
    has_secret: boolean
  }[]
}

export interface AdminUser {
  id: number
  username: string
  display_name: string | null
  role: string
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface RawInputOut {
  id: number
  kind: string
  status: string
  person_id: number | null
  error_msg: string | null
  created_at: string
  processed_at: string | null
}
