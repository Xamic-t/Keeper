import { beforeEach, describe, expect, it } from 'vitest'

import { demoEnabled, demoEnvelope, disableDemo, enableDemo } from './fixtures'

describe('离线演示 fixture', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('enable/disable 控制演示模式', () => {
    expect(demoEnabled()).toBe(false)
    enableDemo()
    expect(demoEnabled()).toBe(true)
    disableDemo()
    expect(demoEnabled()).toBe(false)
  })

  it('今日页/人列表返回可用数据', () => {
    const today = demoEnvelope('/today', 'GET')?.data as { tasks: unknown[] }
    expect(today.tasks.length).toBeGreaterThan(0)

    const persons = demoEnvelope('/persons', 'GET')
    expect(persons?.meta?.total).toBe(3)
    expect((persons?.data as unknown[]).length).toBe(3)
  })

  it('未预录的 GET 返回 undefined，交给真实请求', () => {
    expect(demoEnvelope('/some-unknown-endpoint', 'GET')).toBeUndefined()
  })

  it('写操作返回成功，不触发真实请求', () => {
    expect(demoEnvelope('/todos/401', 'PATCH', undefined, { status: 'done' })).toEqual({
      data: expect.objectContaining({ id: 401 }),
    })
  })
})
