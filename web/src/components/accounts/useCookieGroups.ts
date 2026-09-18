import { computed, type Ref } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'

export interface CookieGroup {
  id: string
  name: string
  createdAt: string
  accounts: AccountWithStats[]
  totalRequests: number
  totalSuccess: number
  totalRateLimited: number
  hasActive: boolean
}

export function useCookieGroups(accounts: Ref<AccountWithStats[]>, activeId: Ref<string>) {
  const cookieGroups = computed<CookieGroup[]>(() => {
    const rawAccounts = accounts.value
    if (!rawAccounts || rawAccounts.length === 0) return []

    const map: Record<string, AccountWithStats[]> = {}
    const groupOrder: string[] = []

    for (let i = 0; i < rawAccounts.length; i++) {
      const acc = rawAccounts[i]
      const cid =
        acc.cookie_id ||
        (acc.created_at ? `cookie_${acc.created_at.slice(0, 16)}` : 'cookie_default')
      if (!map[cid]) {
        map[cid] = []
        groupOrder.push(cid)
      }
      map[cid].push(acc)
    }

    const currentActiveId = activeId.value
    const result: CookieGroup[] = []

    for (let i = 0; i < groupOrder.length; i++) {
      const cid = groupOrder[i]
      const accList = map[cid]

      let totalRequests = 0
      let totalSuccess = 0
      let totalRateLimited = 0
      let hasActive = false
      let primaryEmail = ''

      for (let j = 0; j < accList.length; j++) {
        const a = accList[j]
        totalRequests += a.requests || 0
        totalSuccess += a.success || 0
        totalRateLimited += a.rate_limited || 0
        if (a.id === currentActiveId) {
          hasActive = true
        }
        if (!primaryEmail && a.email) {
          primaryEmail = a.email
        }
      }

      accList.sort((a, b) => {
        const uA = parseInt(a.auth_user || '0', 10)
        const uB = parseInt(b.auth_user || '0', 10)
        return uA - uB
      })

      const earliestCreatedAt = accList[0]?.created_at || ''
      const sessionTitle = primaryEmail || `Cookie 会话 #${i + 1}`

      result.push({
        id: cid,
        name: sessionTitle,
        createdAt: earliestCreatedAt,
        accounts: accList,
        totalRequests,
        totalSuccess,
        totalRateLimited,
        hasActive,
      })
    }

    return result
  })

  return { cookieGroups }
}
