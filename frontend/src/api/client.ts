/**
 * 后端 API 客户端。
 *
 * 约定：
 *  · 后端统一返回 {data, meta?} 或 {error:{code,message}}，这里只关心 data
 *  · 写操作要带 X-CSRF-Token（从 cookie 里读，后端登录时种的可读 cookie）
 *  · 401 一律跳登录页
 *  · 只读演示模式（?demo=1 / VITE_DEMO_MODE）在前端拦截，不请求网络
 */

import { demoEnvelope, demoEnabled } from '@/demo/fixtures'

export interface ApiErrorShape {
  code: string
  message: string
  detail?: Record<string, unknown>
  trace_id?: string
}

export class ApiError extends Error {
  code: string
  status: number
  detail?: Record<string, unknown>

  constructor(status: number, error: ApiErrorShape) {
    super(error.message)
    this.name = 'ApiError'
    this.code = error.code
    this.status = status
    this.detail = error.detail
  }
}

export interface Envelope<T> {
  data: T
  meta?: Record<string, unknown>
}

/** 默认请求超时：同步抽取/导出可能慢，但后端挂起时前端不能无限转圈。 */
export const DEFAULT_REQUEST_TIMEOUT_MS = 30_000

/**
 * 401 的出口。
 *
 * 不在这个文件里直接 import store 或 router：client 是最底层，
 * 反过来依赖上层会造成循环引用（router → views → api → router）。
 * 由 main.ts 在启动时注册一个回调，这里只负责"喊一声"。
 */
let unauthorizedHandler: (() => void) | null = null

export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler
}

/** 已经在登录页就不要再跳了，否则会把用户想看的提示顶掉。 */
function onLoginPage(): boolean {
  return window.location.pathname.startsWith('/login')
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`))
  return match ? decodeURIComponent(match[2]) : null
}

function buildQuery(query?: Record<string, unknown>): string {
  if (!query) return ''
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    params.append(key, String(value))
  })
  const text = params.toString()
  return text ? `?${text}` : ''
}

async function request<T>(
  path: string,
  options: {
    method?: string
    body?: unknown
    query?: Record<string, unknown>
    raw?: boolean
    timeoutMs?: number
  } = {},
): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()

  // 断网演示：命中 fixture 就直接返回，不发请求、不碰真实数据。
  if (demoEnabled()) {
    const demo = demoEnvelope(path, method, options.query, options.body)
    if (demo !== undefined) {
      return (options.raw ? demo : demo.data) as T
    }
  }

  const headers: Record<string, string> = {}
  let body: BodyInit | undefined

  if (options.body instanceof FormData) {
    // 文件上传：让浏览器自己带 multipart boundary，别手写 Content-Type
    body = options.body
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  if (method !== 'GET' && method !== 'HEAD') {
    const csrf = readCookie('csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }

  const controller = new AbortController()
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
  )

  let response: Response
  try {
    response = await fetch(`/api${path}${buildQuery(options.query)}`, {
      method,
      headers,
      body,
      credentials: 'include',
      signal: controller.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(408, { code: 'REQUEST_TIMEOUT', message: '请求超时，请重试' })
    }
    throw err
  } finally {
    window.clearTimeout(timeoutId)
  }

  if (response.status === 204) return undefined as T

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    if (!response.ok) throw new ApiError(response.status, { code: 'HTTP_ERROR', message: '请求失败' })
    return (await response.blob()) as T
  }

  const payload = await response.json()
  if (!response.ok) {
    // 会话过期：清态并回登录页。以前注释写了"401 一律跳登录页"，
    // 但代码里从来没实现过 —— 结果 cookie 过期后每个页面都是空列表，
    // 用户以为数据丢了。
    if (response.status === 401 && !onLoginPage()) {
      unauthorizedHandler?.()
    }
    const error = (payload?.error ?? { code: 'HTTP_ERROR', message: '请求失败' }) as ApiErrorShape
    throw new ApiError(response.status, error)
  }
  if (options.raw) return payload as T
  return (payload as Envelope<T>).data
}

export const api = {
  get: <T>(path: string, query?: Record<string, unknown>) => request<T>(path, { query }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  raw: <T>(path: string) => request<T>(path, { raw: true }),
}