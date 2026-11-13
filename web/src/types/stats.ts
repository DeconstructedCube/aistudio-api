export interface AccountBreakdownItem {
  account_id: string
  name: string
  auth_user: string
  email: string | null
  tier: string
  group: string
  enabled: boolean
  today_requests: number
  today_success: number
  today_rate_limited: number
  today_tokens: number
  today_reasoning_tokens: number
  configured_quota: number
  configured_token_quota?: number | null
  quota_used_percent: number
  token_quota_used_percent?: number
  is_available: boolean
  cooldown_remaining: number
}

export interface ModelStatItem {
  requests: number
  success: number
  rate_limited: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens?: number
  cached_tokens?: number
  total_tokens: number
  finish_reasons?: Record<string, number>
  last_used?: string | null
  account_breakdown?: AccountBreakdownItem[]
}

export interface StatsTotals {
  requests: number
  success: number
  rate_limited: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens?: number
  cached_tokens?: number
  total_tokens: number
}

export interface StatsResponse {
  models: Record<string, ModelStatItem>
  totals: StatsTotals
}
