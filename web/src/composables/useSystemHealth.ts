import { ref, onUnmounted } from 'vue'

export type SystemHealthState = 'checking' | 'online' | 'offline'

/**
 * 轮询 /health 接口的服务在线状态。
 * 从静态文件（无 HTTP 服务）打开时保持初始状态，不轮询。
 */
export function useSystemHealth(intervalMs = 15000) {
  const state = ref<SystemHealthState>('checking')
  let timer: ReturnType<typeof setInterval> | undefined

  async function check() {
    try {
      const res = await fetch('/health')
      state.value = res.ok ? 'online' : 'offline'
    } catch {
      state.value = 'offline'
    }
  }

  if (typeof window !== 'undefined' && window.location.protocol.startsWith('http')) {
    void check()
    timer = setInterval(check, intervalMs)
  }

  onUnmounted(() => {
    clearInterval(timer)
  })

  return { state }
}
