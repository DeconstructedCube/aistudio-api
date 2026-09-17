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

  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
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

