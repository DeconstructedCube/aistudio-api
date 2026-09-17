import { onUnmounted } from 'vue'

export interface PollingOptions {
  /** 是否在挂载或恢复可见时立即执行一次（默认 true） */
  immediate?: boolean
}

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
 * 默认在挂载时立即执行一次回调，页面可见时按指定周期循环拉取。
 */
export function usePolling(
  fn: () => void | Promise<void>,
  intervalMs: number,
  options: PollingOptions = {}
): PollingHandle {
  const immediate = options.immediate !== false
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

    if (!document.hidden) {
      if (immediate) {
        run()
      }
      start()
    }
  }
  return {
    run,
    pause: stop,
    resume: start,
  }
}
