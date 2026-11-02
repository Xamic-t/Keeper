import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, onUnauthorized } from './client'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('api client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    onUnauthorized(() => undefined)
    window.history.pushState({}, '', '/')
  })

  it('解包 {data} envelope', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ data: { ok: true } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.get<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({ credentials: 'include', signal: expect.any(AbortSignal) }),
    )
  })

  it('把后端 error 包成 ApiError', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: { code: 'BAD', message: '坏了' } }, 400))
    vi.stubGlobal('fetch', fetchMock)

    const error = await api.get('/bad').catch((err: unknown) => err)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 400, code: 'BAD', message: '坏了' })
  })

  it('401 且不在登录页时触发统一跳登录回调', async () => {
    window.history.pushState({}, '', '/todos')
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: { code: 'UNAUTHENTICATED', message: '登录过期' } }, 401))
    vi.stubGlobal('fetch', fetchMock)
    const unauthorized = vi.fn()
    onUnauthorized(unauthorized)

    await expect(api.get('/todos')).rejects.toBeInstanceOf(ApiError)

    expect(unauthorized).toHaveBeenCalledTimes(1)
  })

  it('401 已在登录页时不触发跳转，避免互踢', async () => {
    window.history.pushState({}, '', '/login')
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: { code: 'UNAUTHENTICATED', message: '登录过期' } }, 401))
    vi.stubGlobal('fetch', fetchMock)
    const unauthorized = vi.fn()
    onUnauthorized(unauthorized)

    await expect(api.get('/auth/me')).rejects.toBeInstanceOf(ApiError)

    expect(unauthorized).not.toHaveBeenCalled()
  })

  it('AbortError 转成可读的 REQUEST_TIMEOUT', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockImplementation(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('The operation was aborted.', 'AbortError'))
          })
        }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const request = api.get('/slow')
    // 立刻挂一个 catch，避免 Promise 在 fake timers 推进时先变成 unhandled rejection
    const caught = request.catch((err: unknown) => err)
    await vi.advanceTimersByTimeAsync(30_000)

    const error = await caught
    expect(error).toMatchObject({
      name: 'ApiError',
      status: 408,
      code: 'REQUEST_TIMEOUT',
      message: '请求超时，请重试',
    })
    vi.useRealTimers()
  })
})
