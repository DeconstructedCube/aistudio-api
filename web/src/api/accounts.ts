import { request } from './client.ts'
import type {
  Account,
  ImportCookiesRequest,
  ImportCookiesResponse,
  ProbeAndImportRequest,
  ProbeAndImportResponse,
  ImportBundleRequest,
  ImportBundleResponse,
} from '@/types'

export const accountsApi = {
  list(): Promise<Account[]> {
    return request<Account[]>('/accounts')
  },

  getActive(): Promise<Account> {
    return request<Account>('/accounts/active')
  },

  activate(id: string): Promise<Account> {
    return request<Account>(`/accounts/${id}/activate`, {
      method: 'POST',
    })
  },

  delete(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/accounts/${id}`, {
      method: 'DELETE',
    })
  },

  update(id: string, name: string): Promise<Account> {
    return request<Account>(`/accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    })
  },

  importCookies(req: ImportCookiesRequest): Promise<ImportCookiesResponse> {
    return request<ImportCookiesResponse>('/accounts/import-cookies', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  probeAndImport(req: ProbeAndImportRequest): Promise<ProbeAndImportResponse> {
    return request<ProbeAndImportResponse>('/accounts/probe-import', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
  deleteCookieGroup(cookieId: string): Promise<{ deleted: number }> {
    return request<{ deleted: number }>(`/accounts/group/${encodeURIComponent(cookieId)}`, {
      method: 'DELETE',
    })
  },
  importBundle(req: ImportBundleRequest): Promise<ImportBundleResponse> {
    return request<ImportBundleResponse>('/accounts/import-bundle', {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },
}
