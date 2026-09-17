export interface ModelStatItem {
  requests: number
  success: number
  rate_limited: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  last_used?: string | null
}

export interface StatsTotals {
  requests: number
  success: number
  rate_limited: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface StatsResponse {
  models: Record<string, ModelStatItem>
  totals: StatsTotals
}
