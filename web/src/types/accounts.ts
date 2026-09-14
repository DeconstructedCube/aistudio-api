import type { AccountRotationStats } from './rotation.ts'

export interface Account {
  id: string
  name: string
  email: string | null
  created_at: string
  last_used?: string | null
  auth_user: string
  cookie_id?: string | null
}

export interface AccountWithStats extends Omit<Account, 'last_used'>, Partial<AccountRotationStats> {
  last_used?: string | null
}

export interface ImportCookiesRequest {
  cookies: string
  name?: string
  email?: string
  account_id?: string
  auth_user?: string
}

export interface ImportCookiesResponse {
  account_id: string
  name: string
  cookie_count: number
  domain_summary: Record<string, number>
  auth_user: string
}

export interface ProbeAndImportRequest {
  cookies: string
  name_prefix?: string
}

export interface ProbeAndImportResponse {
  imported_count: number
  accounts: Account[]
}
