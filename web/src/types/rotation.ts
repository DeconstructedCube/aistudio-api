export interface ModelDailyDetail {
  requests: number
  success: number
  rate_limited: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  total_tokens: number
  configured_quota: number
  quota_used_percent: number
  cooldown_remaining: number
  drip_mode: boolean
  is_available: boolean
}

export interface AccountRotationStats {
  name?: string
  email?: string | null
  auth_user?: string
  tier?: string
  enabled?: boolean
  group?: string
  notes?: string
  requests: number
  success: number
  rate_limited: number
  errors: number
  today_total_requests?: number
  today_total_tokens?: number
  last_used?: string | null
  last_rate_limited?: string | null
  is_available?: boolean
  session_expired?: boolean
  auth_errors?: number
  cooldown_remaining?: number
  model_cooldowns?: Record<string, number>
  today_models?: Record<string, ModelDailyDetail>
  model_requests?: Record<string, number>
  model_rate_limited?: Record<string, number>
  model_drip_mode?: Record<string, boolean>
}

export interface RotationStatusResponse {
  enabled: boolean
  mode: string
  accounts: Record<string, AccountRotationStats>
  message?: string
}

export interface ClearCooldownRequest {
  account_id?: string
  model?: string
}
