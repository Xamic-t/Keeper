import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useCalendarStore } from './calendar'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('calendar store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('合并 events/todos，并给派生条目生成稳定 id 且不可删', async () => {
    const fetchMock = vi.fn().mockImplementation((url: RequestInfo | URL) => {
      const text = String(url)
      if (text.startsWith('/api/events')) {
        return Promise.resolve(
          jsonResponse({
            data: [
              {
                day: '2026-11-02',
                title: '评审会',
                type: 'custom',
                person_id: null,
                event_id: 9,
                lunar: false,
              },
              {
                day: '2026-11-02',
                title: '农历生日',
                type: 'birthday',
                person_id: 12,
                event_id: 8,
                lunar: true,
              },
            ],
          }),
        )
      }
      return Promise.resolve(jsonResponse({ data: [] }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const store = useCalendarStore()
    await store.load({ from: '2026-11-01', to: '2026-11-30' })

    const custom = store.events.find((item) => item.title === '评审会')
    const lunar = store.events.find((item) => item.title === '农历生日')

    expect(custom).toMatchObject({ id: 'event-9', editable: true })
    expect(custom?.extendedProps.canDelete).toBe(true)
    // 农历生日的公历日期是每年算出来的，不能拖也不能删；拖了等于骗用户
    expect(lunar?.id).toMatch(/^derived-birthday-/)
    expect(lunar?.editable).toBe(false)
    expect(lunar?.extendedProps.canDelete).toBe(false)
  })

  it('patchTodoDue 成功后只改本地那一条', async () => {
    const original = {
      id: 5,
      person_id: null,
      title: '写报告',
      detail: null,
      source: 'manual',
      source_id: null,
      ai_reason: null,
      due_date: '2026-11-01',
      status: 'open',
      done_at: null,
      created_at: '2026-11-01T08:00:00+08:00',
    }
    const fetchMock = vi.fn().mockImplementation((url: RequestInfo | URL) => {
      const text = String(url)
      if (text.startsWith('/api/todos') && text.includes('/5')) {
        return Promise.resolve(jsonResponse({ data: { ...original, due_date: '2026-11-03' } }))
      }
      if (text.startsWith('/api/events')) return Promise.resolve(jsonResponse({ data: [] }))
      return Promise.resolve(jsonResponse({ data: [original] }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const store = useCalendarStore()
    await store.load({ from: '2026-11-01', to: '2026-11-30' })
    await store.patchTodoDue(5, '2026-11-03')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/todos/5',
      expect.objectContaining({ method: 'PATCH' }),
    )
    expect(store.todos).toHaveLength(1)
    expect(store.todos[0].due_date).toBe('2026-11-03')
  })
})
