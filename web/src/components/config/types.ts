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

export type ModelOverrideMap = Record<string, {
  default_tools?: string[]
  generation_config_defaults?: GenerationConfigDefaults
  clear_generation_config_indexes?: number[]
  disable_safety_settings?: boolean
  drop_unsupported_params?: boolean
  safety_settings?: SafetySettingsMap
}>

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

export interface ParsedConfigYaml {
  api_keys?: (ApiKeyConfigItem | string)[]
  model_defaults?: ModelDefaultsConfig
  [key: string]: unknown
}
