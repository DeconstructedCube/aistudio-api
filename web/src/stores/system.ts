import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { systemApi } from '@/api/system.ts'
import type { StatsResponse, RotationStatusResponse, ClearCooldownRequest } from '@/types'
import { useToastStore } from './toast.ts'

export const useSystemStore = defineStore('system', () => {
  const stats = ref<StatsResponse | null>(null)
  const rotation = ref<RotationStatusResponse | null>(null)
  const loading = ref(false)
  const resettingCooldown = ref(false)
  const switchingNext = ref(false)

  const totalRequests = computed(() => {
    if (!stats.value?.models) return 0
    return Object.values(stats.value.models).reduce((sum, item) => sum + (item.requests || 0), 0)
  })

  const totalRateLimited = computed(() => {
    if (!stats.value?.models) return 0
    return Object.values(stats.value.models).reduce((sum, item) => sum + (item.rate_limited || 0), 0)
  })

  const totalSuccess = computed(() => {
    if (!stats.value?.models) return 0
    return Object.values(stats.value.models).reduce((sum, item) => sum + (item.success || 0), 0)
  })

  const totalErrors = computed(() => {
    if (!stats.value?.models) return 0
    return Object.values(stats.value.models).reduce((sum, item) => sum + (item.errors || 0), 0)
  })

  async function fetchStats() {
    loading.value = true
    try {
      stats.value = await systemApi.getStats()
    } catch {
      // ignore
    } finally {
      loading.value = false
    }
  }

  async function fetchRotation() {
    try {
      rotation.value = await systemApi.getRotation()
    } catch {
      // ignore
    }
  }

  async function clearCooldown(req: ClearCooldownRequest = {}) {
    const toast = useToastStore()
    resettingCooldown.value = true
    try {
      await systemApi.clearCooldown(req)
      toast.success(req.account_id ? '账号配额锁定已清除' : '所有账号配额锁定已重置')
      await fetchRotation()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '重置失败'
      toast.error(msg)
    } finally {
      resettingCooldown.value = false
    }
  }

  async function forceNextAccount() {
    const toast = useToastStore()
    switchingNext.value = true
    try {
      const res = await systemApi.forceNextAccount()
      toast.success(`已切换至账号: ${res.account.name || res.account.email || res.account.id}`)
      await fetchRotation()
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '切换失败'
      toast.error(msg)
      return false
    } finally {
      switchingNext.value = false
    }
  }

  return {
    stats,
    rotation,
    loading,
    resettingCooldown,
    switchingNext,
    totalRequests,
    totalRateLimited,
    totalSuccess,
    totalErrors,
    fetchStats,
    fetchRotation,
    clearCooldown,
    forceNextAccount,
  }
})
