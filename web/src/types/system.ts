export interface SystemConfig {
  port: number
  signer_mode?: string
  signer_ready?: boolean
  signer_running?: boolean
  proxy_configured: boolean
  auth_enabled: boolean
  yaml_content: string
  log_level?: string
  dump_requests?: boolean
  debug_env_active?: boolean
  dump_to_file?: boolean
  dump_dir?: string
}
export interface UpdateLoggingConfigRequest {
  level?: string
  dump_requests?: boolean
  dump_to_file?: boolean
  dump_dir?: string
}
export interface UpdateLoggingConfigResponse {
  ok: boolean
  log_level: string
  dump_requests: boolean
  debug_env_active: boolean
  dump_to_file: boolean
  dump_dir: string
}

export interface HealthCheckResponse {
  status: string
}
export interface ApiKeyItem {
  name: string
  key: string
  created_at?: string | null
}

export interface CreateApiKeyRequest {
  name?: string
  key?: string
}

export interface UpdateApiKeyRequest {
  name: string
}
