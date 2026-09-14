import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ToastItem {
  id: number
  message: string
  type: 'info' | 'success' | 'warning' | 'error'
  duration?: number
}

export const useToastStore = defineStore('toast', () => {
  const toasts = ref<ToastItem[]>([])
  let counter = 0

  function show(message: string, type: ToastItem['type'] = 'info', duration = 3000) {
    const id = ++counter
    toasts.value.push({ id, message, type, duration })

    if (duration > 0) {
      setTimeout(() => {
        remove(id)
      }, duration)
    }
  }

  function success(message: string, duration = 3000) {
    show(message, 'success', duration)
  }

  function error(message: string, duration = 4000) {
    show(message, 'error', duration)
  }

  function warning(message: string, duration = 3500) {
    show(message, 'warning', duration)
  }

  function info(message: string, duration = 3000) {
    show(message, 'info', duration)
  }

  function remove(id: number) {
    const idx = toasts.value.findIndex((t) => t.id === id)
    if (idx !== -1) {
      toasts.value.splice(idx, 1)
    }
  }

  return {
    toasts,
    show,
    success,
    error,
    warning,
    info,
    remove,
  }
})
