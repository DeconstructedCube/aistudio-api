export interface MatchRules {
  contains?: string[]
  prefixes?: string[]
  exact?: string[]
}

export interface ThinkingConfig {
  level?: 'MINIMAL' | 'LOW' | 'MEDIUM' | 'HIGH'
  mode?: number
}

export interface GenerationConfigDefaults {
  response_mime_type?: string | null
  image_output_mode?: 'image_only' | 'text_and_image' | string | null
  media_resolution?: 'LOW' | 'MEDIUM' | 'HIGH' | number | null
  thinking_config?: ThinkingConfig | null
  temperature?: number | null
  top_p?: number | null
  top_k?: number | null
  max_output_tokens?: number | null
}

export type SafetyCategory = 'Harassment' | 'Hate' | 'Sexually Explicit' | 'Dangerous Content'

export type SafetySettingsMap = Record<string, number>

export interface ModelProfileItem {
  name: string
  match?: MatchRules
  is_image_model?: boolean
  default_tools?: string[]
  generation_config_defaults?: GenerationConfigDefaults
  clear_generation_config_indexes?: number[]
  disable_safety_settings?: boolean
  drop_unsupported_params?: boolean
  safety_settings?: SafetySettingsMap
}

export interface ModelOverrideItem {
  is_image_model?: boolean
  default_tools?: string[]
  generation_config_defaults?: GenerationConfigDefaults
  clear_generation_config_indexes?: number[]
  disable_safety_settings?: boolean
  drop_unsupported_params?: boolean
  safety_settings?: SafetySettingsMap
}

export type ModelOverrideMap = Record<string, ModelOverrideItem>

export interface ModelDefaultsConfig {
  drop_unsupported_params?: boolean
  profiles?: ModelProfileItem[]
  models?: ModelOverrideMap
}

export interface ApiKeyConfigItem {
  name?: string
  key: string
  created_at?: string
}

export interface TierQuotaRule {
  match?: MatchRules
  daily_limit: number
  daily_token_limit?: number | null
}

export interface TierQuotaTierConfig {
  default_limit?: number
  default_token_limit?: number | null
  [ruleName: string]: TierQuotaRule | number | null | undefined
}

export interface TierQuotaOverrideObject {
  daily_limit?: number
  daily_token_limit?: number | null
}

export type TierQuotaOverrideValue = number | TierQuotaOverrideObject

export interface TierQuotasConfig {
  overrides?: Record<string, TierQuotaOverrideValue>
  [tierName: string]: TierQuotaTierConfig | Record<string, TierQuotaOverrideValue> | undefined
}

export interface PaidModelRuleItem {
  contains?: string[]
  prefixes?: string[]
  exact?: string[]
  required_tiers: string[]
}

export type PaidModelRulesConfig = Record<string, PaidModelRuleItem>

export interface RotatorCooldownsConfig {
  rate_limited_short?: number
  rate_limited_drip?: number
  rate_limited_drip_extended?: number
}

export interface RotatorConfig {
  max_retries?: number
  strategy?: 'sticky' | 'round_robin' | string
  stickiness_bonus?: number
  stickiness_threshold?: number
  drip_penalty?: number
  tier_weights?: Record<string, number>
  cooldowns?: RotatorCooldownsConfig
}

export interface SystemConfigDefaults {
  model_defaults?: ModelDefaultsConfig
  tier_quotas?: TierQuotasConfig
  paid_model_rules?: PaidModelRulesConfig
  rotator?: RotatorConfig
}

export interface ParsedConfigYaml {
  api_keys?: (ApiKeyConfigItem | string)[]
  model_defaults?: ModelDefaultsConfig
  tier_quotas?: TierQuotasConfig
  paid_model_rules?: PaidModelRulesConfig
  rotator?: RotatorConfig
  [key: string]: unknown
}
