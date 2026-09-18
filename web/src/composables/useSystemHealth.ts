import { ref } from 'vue'
import { usePolling } from './usePolling.ts'

export type SystemHealthState = 'checking' | 'online' | 'offline'

/**
 * 轮询 /health 接口的服务在线状态。
 * 页面不可见时自动暂停，恢复可见时自动探测。
 */
export function useSystemHealth(intervalMs = 15000) {
  const state = ref<SystemHealthState>('checking')

  async function check() {
    try {
      const res = await fetch('/health')
      state.value = res.ok ? 'online' : 'offline'
    } catch {
      state.value = 'offline'
    }
  }

  usePolling(check, intervalMs, { key: 'system_health', immediate: true })

  return { state }
}
