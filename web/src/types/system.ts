export interface SystemConfig {
  port: number
  browser_port: number
  browser_headless: boolean
  proxy_configured: boolean
  auth_enabled: boolean
  snapshot_cache_ttl: number
  yaml_content: string
  log_level?: string
  dump_requests?: boolean
  debug_env_active?: boolean
}

export interface UpdateLoggingConfigRequest {
  level?: string
  dump_requests?: boolean
}

export interface UpdateLoggingConfigResponse {
  ok: boolean
  log_level: string
  dump_requests: boolean
  debug_env_active: boolean
}


export interface HealthCheckResponse {
  status: string
  busy: boolean
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
