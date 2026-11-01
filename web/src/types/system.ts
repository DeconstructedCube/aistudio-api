export interface SystemConfig {
  port: number
  proof_server_url?: string
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

export interface SignerServerItem {
  id: string
  name: string
  description?: string
  url: string
  user_agent?: string
  headers?: Record<string, string>
  is_active?: boolean
}

export interface SignerServersResponse {
  active_server: string
  servers: SignerServerItem[]
}

export interface TestSignerServerResponse {
  ok: boolean
  status_code?: number
  elapsed_ms: number
  error?: string
  data?: Record<string, unknown>
}
