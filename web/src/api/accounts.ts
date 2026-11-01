import { request } from './client.ts'
import type {
  Account,
  ImportCookiesRequest,
  ImportCookiesResponse,
  ProbeAndImportRequest,
  ProbeAndImportResponse,
  ImportBundleRequest,
  ImportBundleResponse,
  AccountPreset,
  CreatePresetRequest,
  SnapshotPresetRequest,
  ApplyPresetResponse,
} from '@/types'

export const accountsApi = {
  list(): Promise<Account[]> {
    return request<Account[]>('/api/accounts')
  },

  getActive(): Promise<Account> {
    return request<Account>('/api/accounts/active')
  },

  activate(id: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/activate`, {
      method: 'POST',
    })
  },
  detectEmail(id: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/detect-email`, {
      method: 'POST',
    })
  },
  detectTier(id: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/detect-tier`, {
      method: 'POST',
    })
  },

  rotateCookies(): Promise<{ ok: boolean; message: string }> {
    return request('/api/accounts/rotate-cookies', {
      method: 'POST',
    })
  },

  test(id: string): Promise<{
    ok: boolean
    alive: boolean
    status_code: number
    elapsed_ms: number
    tier?: string
    email?: string
    reply?: string
    error?: string
  }> {
    return request(`/api/accounts/${id}/test`, {
      method: 'POST',
    })
  },

  delete(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api/accounts/${id}`, {
      method: 'DELETE',
    })
  },

  update(id: string, name: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    })
  },

  importCookies(req: ImportCookiesRequest): Promise<ImportCookiesResponse> {
    return request<ImportCookiesResponse>('/api/accounts/import-cookies', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  probeAndImport(req: ProbeAndImportRequest): Promise<ProbeAndImportResponse> {
    return request<ProbeAndImportResponse>('/api/accounts/probe-import', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
  deleteCookieGroup(cookieId: string): Promise<{ deleted: number }> {
    return request<{ deleted: number }>(`/api/accounts/group/${encodeURIComponent(cookieId)}`, {
      method: 'DELETE',
    })
  },
  importBundle(req: ImportBundleRequest): Promise<ImportBundleResponse> {
    return request<ImportBundleResponse>('/api/accounts/import-bundle', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  toggle(id: string, enabled?: boolean): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    })
  },

  updateGroup(id: string, group: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/group`, {
      method: 'PUT',
      body: JSON.stringify({ group }),
    })
  },

  updateNotes(id: string, notes: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/notes`, {
      method: 'PUT',
      body: JSON.stringify({ notes }),
    })
  },

  listGroups(): Promise<string[]> {
    return request<string[]>('/api/accounts/groups/list')
  },

  batchToggleGroup(group: string, enabled: boolean): Promise<{ ok: boolean; count: number; group: string; enabled: boolean }> {
    return request(`/api/accounts/groups/${encodeURIComponent(group)}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    })
  },

  listPresets(): Promise<AccountPreset[]> {
    return request<AccountPreset[]>('/api/accounts/presets/list')
  },

  createPreset(req: CreatePresetRequest): Promise<AccountPreset> {
    return request<AccountPreset>('/api/accounts/presets', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  createSnapshotPreset(req: SnapshotPresetRequest): Promise<AccountPreset> {
    return request<AccountPreset>('/api/accounts/presets/snapshot', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  deletePreset(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api/accounts/presets/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
  },

  applyPreset(id: string): Promise<ApplyPresetResponse> {
    return request<ApplyPresetResponse>(`/api/accounts/presets/${encodeURIComponent(id)}/apply`, {
      method: 'POST',
    })
  },
}
