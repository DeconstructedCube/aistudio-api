import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { accountsApi } from '@/api/accounts.ts'
import { systemApi } from '@/api/system.ts'
import type {
  Account,
  AccountWithStats,
  AccountRotationStats,
  ImportCookiesRequest,
  ProbeAndImportRequest,
} from '@/types'
import { useToastStore } from './toast.ts'

export const useAccountsStore = defineStore('accounts', () => {
  const accounts = ref<Account[]>([])
  const activeAccount = ref<Account | null>(null)
  const rotationAccounts = ref<Record<string, AccountRotationStats>>({})
  const loading = ref(false)
  const activatingId = ref<string | null>(null)
  const importing = ref(false)

  const activeId = computed(() => activeAccount.value?.id || '')

  const accountRows = computed<AccountWithStats[]>(() => {
    return accounts.value.map((a) => {
      const rot = rotationAccounts.value[a.id] || {}
      return {
        ...a,
        ...rot,
      }
    })
  })

  async function fetchAll() {
    loading.value = true
    try {
      const [accsRes, activeRes, rotStatsRes] = await Promise.allSettled([
        accountsApi.list(),
        accountsApi.getActive(),
        systemApi.getRotation(),
      ])

      if (accsRes.status === 'fulfilled') {
        accounts.value = accsRes.value
      } else {
        console.warn('获取账号列表失败，保留当前展示数据:', accsRes.reason)
      }

      if (activeRes.status === 'fulfilled') {
        activeAccount.value = activeRes.value
      } else if (accsRes.status === 'fulfilled' && accsRes.value.length === 0) {
        activeAccount.value = null
      }

      if (rotStatsRes.status === 'fulfilled' && rotStatsRes.value?.accounts) {
        rotationAccounts.value = rotStatsRes.value.accounts
      }
    } finally {
      loading.value = false
    }
  }

  async function activateAccount(id: string) {
    const toast = useToastStore()
    activatingId.value = id
    try {
      const updated = await accountsApi.activate(id)
      activeAccount.value = updated
      toast.success(`已成功激活账号: ${updated.name || updated.email || updated.id}`)
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '激活失败'
      toast.error(msg)
    } finally {
      activatingId.value = null
    }
  }

  async function deleteAccount(id: string) {
    const toast = useToastStore()
    try {
      await accountsApi.delete(id)
      toast.success('账号已删除')
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '删除失败'
      toast.error(msg)
    }
  }

  async function deleteCookieGroup(cookieId: string) {
    const toast = useToastStore()
    try {
      const res = await accountsApi.deleteCookieGroup(cookieId)
      toast.success(`已成功删除该 Cookie 凭据下的全部 ${res.deleted} 个子账号`)
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '删除失败'
      toast.error(msg)
    }
  }

  async function updateAccountName(id: string, name: string) {
    const toast = useToastStore()
    try {
      await accountsApi.update(id, name)
      toast.success('账号名称已更新')
      await fetchAll()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '更新失败'
      toast.error(msg)
    }
  }

  async function importCookies(payload: ImportCookiesRequest) {
    const toast = useToastStore()
    importing.value = true
    try {
      const res = await accountsApi.importCookies(payload)
      toast.success(`导入成功: 解析出 ${res.cookie_count} 个 Cookie (u/${res.auth_user})`)
      await fetchAll()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '导入失败'
      toast.error(msg)
      return false
    } finally {
      importing.value = false
    }
  }

  async function probeAndImport(payload: ProbeAndImportRequest) {
    const toast = useToastStore()
    importing.value = true
    try {
      const res = await accountsApi.probeAndImport(payload)
      toast.success(`已完成探测并导入 ${res.imported_count} 个账号`)
      await fetchAll()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '自动探活导入失败'
      toast.error(msg)
      return false
    } finally {
      importing.value = false
    }
  }

  return {
    accounts,
    activeAccount,
    activeId,
    rotationAccounts,
    accountRows,
    loading,
    activatingId,
    importing,
    fetchAll,
    activateAccount,
    deleteAccount,
    deleteCookieGroup,
    updateAccountName,
    importCookies,
    probeAndImport,
  }
})
