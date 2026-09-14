export interface ModelStatItem {
  requests: number
  success: number
  rate_limited: number
  errors: number
  last_used?: string | null
}

export interface StatsResponse {
  models: Record<string, ModelStatItem>
  requests?: {
    total: number
    success: number
    rate_limited: number
    errors: number
  }
}
