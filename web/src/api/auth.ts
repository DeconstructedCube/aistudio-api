import { request } from './client.ts'
import type { AuthCheckResponse, StatsResponse } from '@/types'

export const authApi = {
  checkAuth(): Promise<AuthCheckResponse> {
    return request<AuthCheckResponse>('/auth/check')
  },

  verifyToken(token: string): Promise<StatsResponse> {
    return request<StatsResponse>('/stats', {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  },
}
