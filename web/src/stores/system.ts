import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { systemApi } from '@/api/system.ts'
import type { StatsResponse, RotationMode, RotationStatusResponse } from '@/types'
import { useToastStore } from './toast.ts'

export const useSystemStore = defineStore('system', () => {
  const stats = ref<StatsResponse | null>(null)
  const rotation = ref<RotationStatusResponse | null>(null)
  const loading = ref(false)
  const savingRotation = ref(false)
  const switchingNext = ref(false)

  const rotationMode = computed<RotationMode>(() => rotation.value?.mode || 'round_robin')
  const cooldownSeconds = computed(() => rotation.value?.cooldown_seconds ?? 60)

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

  async function saveRotation(mode: RotationMode, cooldown: number) {
    const toast = useToastStore()
    savingRotation.value = true
    try {
      await systemApi.setRotationMode({ mode, cooldown_seconds: cooldown })
      toast.success('轮询设置已保存')
      await fetchRotation()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '保存失败'
      toast.error(msg)
    } finally {
      savingRotation.value = false
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
    savingRotation,
    switchingNext,
    rotationMode,
    cooldownSeconds,
    totalRequests,
    totalRateLimited,
    totalSuccess,
    totalErrors,
    fetchStats,
    fetchRotation,
    saveRotation,
    forceNextAccount,
  }
})
