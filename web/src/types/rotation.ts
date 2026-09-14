export type RotationMode = 'sticky' | 'round_robin' | 'lru' | 'least_rl'

export interface AccountRotationStats {
  requests: number
  success: number
  rate_limited: number
  errors: number
  last_used?: string | null
  last_rate_limited?: string | null
  is_available?: boolean
  cooldown_remaining?: number
  model_cooldowns?: Record<string, number>
  model_requests?: Record<string, number>
  model_rate_limited?: Record<string, number>
}

export interface RotationStatusResponse {
  enabled: boolean
  mode: RotationMode
  cooldown_seconds: number
  accounts: Record<string, AccountRotationStats>
  message?: string
}

export interface SetRotationModeRequest {
  mode: RotationMode
  cooldown_seconds?: number
}
