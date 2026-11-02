import { describe, expect, it } from 'vitest'

import { describeAction, failureText } from '@/lib/assistantDisplay'
import type { AssistantAction } from '@/api/types'

function action(overrides: Partial<AssistantAction> = {}): AssistantAction {
  return {
    type: 'create_todo',
    ok: true,
    summary: '已创建待办：给王姐准备生日礼物',
    ref_id: 12,
    reason: null,
    ...overrides,
  }
}

describe('describeAction', () => {
  it('建待办成功 → 带待办图标和待办链接', () => {
    const display = describeAction(action())
    expect(display.icon).toBe('☑')
    expect(display.tone).toBe('ok')
    expect(display.link).toEqual({ label: '去看待办', to: '/todos' })
  })

  it('建事件成功 → 去日历（不是去待办）', () => {
    const display = describeAction(action({ type: 'create_event', summary: '已建事件' }))
    expect(display.link).toEqual({ label: '去看日历', to: '/calendar' })
  })

  it('记事实成功 → 去确认页（因为只落了 pending）', () => {
    const display = describeAction(action({ type: 'add_fact', summary: '记下了（待确认）' }))
    expect(display.link).toEqual({ label: '去确认', to: '/confirm' })
  })

  it('失败的动作不给链接，避免"点了跳过去什么都没有"', () => {
    const display = describeAction(action({ ok: false, reason: '没认出是哪个人' }))
    expect(display.tone).toBe('failed')
    expect(display.link).toBeNull()
    expect(display.icon).toBe('·')
  })

  it('未知类型成功时也给个默认图标，但不乱跳页面', () => {
    const display = describeAction(action({ type: 'unknown_kind' }))
    expect(display.icon).toBe('✓')
    expect(display.link).toBeNull()
  })
})

describe('failureText', () => {
  it('有 reason 用 reason', () => {
    expect(failureText(action({ ok: false, reason: '日期看不懂' }))).toBe('日期看不懂')
  })

  it('没有 reason 时给一句人话，不要留空', () => {
    expect(failureText(action({ ok: false, reason: null }))).toBe('没做成')
    expect(failureText(action({ ok: false, reason: '   ' }))).toBe('没做成')
  })
})
