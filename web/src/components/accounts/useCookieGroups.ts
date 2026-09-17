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
    const map: Record<string, AccountWithStats[]> = {}

    for (const acc of accounts.value) {
      const cid = acc.cookie_id || (acc.created_at ? `cookie_${acc.created_at.slice(0, 16)}` : 'cookie_default')
      if (!map[cid]) {
        map[cid] = []
      }
      map[cid].push(acc)
    }

    return Object.entries(map).map(([cid, accList], idx) => {
      accList.sort((a, b) => {
        const uA = parseInt(a.auth_user || '0', 10)
        const uB = parseInt(b.auth_user || '0', 10)
        return uA - uB
      })

      const earliestCreatedAt = accList[0]?.created_at || ''
      const totalRequests = accList.reduce((sum, a) => sum + (a.requests || 0), 0)
      const totalSuccess = accList.reduce((sum, a) => sum + (a.success || 0), 0)
      const totalRateLimited = accList.reduce((sum, a) => sum + (a.rate_limited || 0), 0)
      const hasActive = accList.some((a) => a.id === activeId.value)

      const primaryEmail = accList.find((a) => a.email)?.email
      const sessionTitle = primaryEmail || `Cookie 会话 #${idx + 1}`

      return {
        id: cid,
        name: sessionTitle,
        createdAt: earliestCreatedAt,
        accounts: accList,
        totalRequests,
        totalSuccess,
        totalRateLimited,
        hasActive,
      }
    })
  })

  return { cookieGroups }
}
