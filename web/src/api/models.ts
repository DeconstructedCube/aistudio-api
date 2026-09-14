import { request } from './client.ts'
import type { GeminiModelListResponse, GeminiModel } from '@/types'

export const modelsApi = {
  list(): Promise<GeminiModelListResponse> {
    return request<GeminiModelListResponse>('/v1beta/models')
  },

  get(modelId: string): Promise<GeminiModel> {
    const cleanId = modelId.replace(/^models\//, '')
    return request<GeminiModel>(`/v1beta/models/${cleanId}`)
  },
}
