export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

const TOKEN_KEY = 'asp_api_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token: string): void {
  if (token.trim()) {
    localStorage.setItem(TOKEN_KEY, token.trim())
  } else {
    localStorage.removeItem(TOKEN_KEY)
  }
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {})
  const token = getToken()

  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    let errorDetail = `请求失败 (${response.status})`
    try {
      const errJson = await response.json()
      if (errJson && typeof errJson.detail === 'string') {
        errorDetail = errJson.detail
      } else if (errJson && typeof errJson.error?.message === 'string') {
        errorDetail = errJson.error.message
      }
    } catch {
      // ignore json parse error
    }
    throw new ApiError(response.status, errorDetail)
  }

  // 204 No Content
  if (response.status === 204) {
    return {} as T
  }

  return response.json()
}

export async function* streamSSE(
  url: string,
  body: object,
  signal?: AbortSignal
): AsyncGenerator<{ text?: string; thinking?: string; error?: string }> {
  const headers = new Headers({
    'Content-Type': 'application/json',
  })
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    let msg = `请求失败 (${response.status})`
    try {
      const err = await response.json()
      msg = err.detail || err.error?.message || msg
    } catch {
      // ignore
    }
    throw new ApiError(response.status, msg)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('当前环境不支持流式响应')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || trimmed.startsWith(':')) continue

        if (trimmed.startsWith('data: ')) {
          const dataStr = trimmed.slice(6).trim()
          if (dataStr === '[DONE]') return

          try {
            const parsed = JSON.parse(dataStr)
            const candidate = parsed.candidates?.[0]
            const parts = candidate?.content?.parts || []

            for (const part of parts) {
              if (part.thought) {
                yield { thinking: part.text || '' }
              } else if (part.text) {
                yield { text: part.text }
              }
            }
          } catch {
            // non-json sse chunk
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
