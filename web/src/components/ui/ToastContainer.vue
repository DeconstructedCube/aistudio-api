<script setup lang="ts">
import { useToastStore } from '@/stores/toast.ts'
import { CheckCircle2, AlertCircle, AlertTriangle, Info, X } from 'lucide-vue-next'

const toastStore = useToastStore()
</script>

<template>
  <div class="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none px-4 sm:px-0">
    <TransitionGroup
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="opacity-0 translate-y-[-8px] scale-95"
      enter-to-class="opacity-100 translate-y-0 scale-100"
      leave-active-class="transition duration-150 ease-in"
      leave-from-class="opacity-100 translate-y-0 scale-100"
      leave-to-class="opacity-0 translate-y-[-8px] scale-95"
    >
      <div
        v-for="toast in toastStore.toasts"
        :key="toast.id"
        class="pointer-events-auto flex items-start gap-3 p-3.5 rounded-xl shadow-lg border text-sm font-medium transition-all"
        :class="{
          'bg-white border-green-200 text-green-800': toast.type === 'success',
          'bg-white border-red-200 text-red-800': toast.type === 'error',
          'bg-white border-amber-200 text-amber-800': toast.type === 'warning',
          'bg-white border-blue-200 text-blue-800': toast.type === 'info',
        }"
      >
        <CheckCircle2
          v-if="toast.type === 'success'"
          class="w-5 h-5 text-green-500 shrink-0 mt-0.5"
        />
        <AlertCircle
          v-else-if="toast.type === 'error'"
          class="w-5 h-5 text-red-500 shrink-0 mt-0.5"
        />
        <AlertTriangle
          v-else-if="toast.type === 'warning'"
          class="w-5 h-5 text-amber-500 shrink-0 mt-0.5"
        />
        <Info
          v-else
          class="w-5 h-5 text-blue-500 shrink-0 mt-0.5"
        />

        <div class="flex-1 leading-snug break-words">
          {{ toast.message }}
        </div>

        <button
          type="button"
          class="text-gray-400 hover:text-gray-600 rounded-lg p-0.5 transition-colors"
          @click="toastStore.remove(toast.id)"
        >
          <X class="w-4 h-4" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>
