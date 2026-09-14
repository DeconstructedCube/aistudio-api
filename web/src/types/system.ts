export interface SystemConfig {
  port: number
  browser_port: number
  browser_headless: boolean
  proxy_configured: boolean
  auth_enabled: boolean
  max_concurrency: number
  account_rotation_mode: string
  account_cooldown_seconds: number
  snapshot_cache_ttl: number
  yaml_content: string
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
