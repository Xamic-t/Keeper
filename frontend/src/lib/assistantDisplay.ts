/**
 * 助理气泡里"动作 → 展示"的纯逻辑。
 *
 * 抽出来单独放，是因为这段最容易被改出界面 bug：
 * 动作没成功却给了个跳转链接、或者建了事件却把人带去待办页。
 * 放在 .vue 里就只能在浏览器里点着看，抽出来能被 Vitest 直接钉住。
 */

import type { AssistantAction } from '@/api/types'

export interface ActionDisplay {
  /** 气泡左侧的图标 */
  icon: string
  /** 成功/失败的文案配色由调用方决定，这里只给语义 */
  tone: 'ok' | 'failed'
  /** 成功时点去哪儿；失败时为 null */
  link: { label: string; to: string } | null
}

/** 每类动作成功后该把用户带到哪儿。带错地方比不带更糟。 */
const LINKS: Record<string, { label: string; to: string }> = {
  create_todo: { label: '去看待办', to: '/todos' },
  create_event: { label: '去看日历', to: '/calendar' },
  add_fact: { label: '去确认', to: '/confirm' },
}

const ICONS: Record<string, string> = {
  create_todo: '☑',
  create_event: '▦',
  add_fact: '✓',
}

export function describeAction(action: AssistantAction): ActionDisplay {
  if (!action.ok) {
    return { icon: '·', tone: 'failed', link: null }
  }
  return {
    icon: ICONS[action.type] ?? '✓',
    tone: 'ok',
    link: LINKS[action.type] ?? null,
  }
}

/** 动作失败时给用户看的一句话。后端给了 reason 就用后端的。 */
export function failureText(action: AssistantAction): string {
  return action.reason?.trim() || '没做成'
}
