import { onUnmounted } from 'vue'

export interface PollingHandle {
  /** 立即执行一次回调 */
  run: () => void
  /** 暂停轮询（可恢复） */
  pause: () => void
  /** 恢复轮询 */
  resume: () => void
}

/**
 * 页面隐藏时自动暂停的轮询。
 * 从静态资源打开（origin 无 HTTP 服务）或非安全上下文时静默不启动。
 */
export function usePolling(fn: () => void | Promise<void>, intervalMs: number): PollingHandle {
  let timer: ReturnType<typeof setInterval> | undefined

  function run() {
    void fn()
  }

  function start() {
    if (timer) return
    timer = setInterval(run, intervalMs)
  }

  function stop() {
    clearInterval(timer)
    timer = undefined
  }

  if (typeof window !== 'undefined' && window.location.protocol.startsWith('http')) {
    const onVisibility = () => {
      if (document.hidden) {
        stop()
      } else {
        run()
        start()
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    onUnmounted(() => {
      document.removeEventListener('visibilitychange', onVisibility)
      stop()
    })

    if (!document.hidden) start()
  }

  return {
    run,
    pause: stop,
    resume: start,
  }
}
