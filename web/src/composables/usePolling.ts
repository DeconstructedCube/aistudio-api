import { onUnmounted } from 'vue'

export interface PollingOptions {
  /** 任务唯一标识（可选，用于跨组件去重） */
  key?: string
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

interface ActiveTask {
  fn: () => void | Promise<void>
  intervalMs: number
  timer?: number
  paused: boolean
  running: boolean
}

const activeTasks = new Map<string, ActiveTask>()
let visibilityListenerRegistered = false

function setupGlobalVisibilityListener() {
  if (visibilityListenerRegistered || typeof window === 'undefined') return
  visibilityListenerRegistered = true

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      for (const task of activeTasks.values()) {
        if (task.timer) {
          clearInterval(task.timer)
          task.timer = undefined
        }
      }
    } else {
      for (const task of activeTasks.values()) {
        if (!task.paused) {
          void runTask(task)
          startTaskTimer(task)
        }
      }
    }
  })
}

async function runTask(task: ActiveTask) {
  if (task.running || (typeof document !== 'undefined' && document.hidden)) return
  task.running = true
  try {
    await task.fn()
  } catch (err) {
    console.debug('[usePolling] task execution error:', err)
  } finally {
    task.running = false
  }
}

function startTaskTimer(task: ActiveTask) {
  if (task.timer || task.paused || (typeof document !== 'undefined' && document.hidden)) return
  task.timer = window.setInterval(() => {
    void runTask(task)
  }, task.intervalMs)
}

function stopTaskTimer(task: ActiveTask) {
  if (task.timer) {
    clearInterval(task.timer)
    task.timer = undefined
  }
}

let autoKeyCounter = 0

/**
 * 页面可见时周期性调度的统一轮询管理。
 * 支持页面隐藏自动冻结、销毁自动释放与全局并发去重。
 */
export function usePolling(
  fn: () => void | Promise<void>,
  intervalMs: number,
  options: PollingOptions = {}
): PollingHandle {
  setupGlobalVisibilityListener()

  const taskId = options.key || `poll_${++autoKeyCounter}`
  const immediate = options.immediate !== false

  const task: ActiveTask = {
    fn,
    intervalMs,
    paused: false,
    running: false,
  }
  activeTasks.set(taskId, task)

  if (typeof window !== 'undefined' && window.location.protocol.startsWith('http')) {
    if (!document.hidden) {
      if (immediate) {
        void runTask(task)
      }
      startTaskTimer(task)
    }
  }

  const handle: PollingHandle = {
    run: () => {
      void runTask(task)
    },
    pause: () => {
      task.paused = true
      stopTaskTimer(task)
    },
    resume: () => {
      task.paused = false
      startTaskTimer(task)
    },
  }

  onUnmounted(() => {
    stopTaskTimer(task)
    activeTasks.delete(taskId)
  })

  return handle
}
