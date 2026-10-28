/**
 * 后端 API 客户端。
 *
 * 约定：
 *  · 后端统一返回 {data, meta?} 或 {error:{code,message}}，这里只关心 data
 *  · 写操作要带 X-CSRF-Token（从 cookie 里读，后端登录时种的可读 cookie）
 *  · 401 一律跳登录页
 */

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
  } = {},
): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
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

  const response = await fetch(`/api${path}${buildQuery(options.query)}`, {
    method,
    headers,
    body,
    credentials: 'include',
  })

  if (response.status === 204) return undefined as T

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    if (!response.ok) throw new ApiError(response.status, { code: 'HTTP_ERROR', message: '请求失败' })
    return (await response.blob()) as T
  }

  const payload = await response.json()
  if (!response.ok) {
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
