<script setup lang="ts">
import { Menu, Key, RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth.ts'

defineProps<{
  title: string
  refreshing?: boolean
}>()

const emit = defineEmits<{
  toggleSidebar: []
  openTokenModal: []
  refresh: []
}>()

const authStore = useAuthStore()
</script>

<template>
  <header class="h-16 bg-white/80 backdrop-blur-md border-b border-gray-200/80 px-4 sm:px-6 flex items-center justify-between sticky top-0 z-30">
    <div class="flex items-center gap-3">
      <button
        type="button"
        class="lg:hidden p-2 rounded-xl text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer"
        @click="emit('toggleSidebar')"
      >
        <Menu class="w-5 h-5" />
      </button>
      <h1 class="text-base sm:text-lg font-bold text-gray-900 tracking-tight">
        {{ title }}
      </h1>
    </div>

    <div class="flex items-center gap-2 sm:gap-3">
      <!-- Auth State Badge -->
      <div
        v-if="!authStore.authEnabled"
        class="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-50 border border-amber-200/70 text-[11px] font-medium text-amber-800"
        title="服务端未设置 AISTUDIO_WEB_PASSWORD，当前处于免密公开访问模式"
      >
        <ShieldAlert class="w-3.5 h-3.5 text-amber-600 shrink-0" />
        <span>免密模式</span>
      </div>
      <div
        v-else
        class="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-50 border border-emerald-200/70 text-[11px] font-medium text-emerald-800"
        title="服务端已启用 AISTUDIO_WEB_PASSWORD 控制台鉴权"
      >
        <ShieldCheck class="w-3.5 h-3.5 text-emerald-600 shrink-0" />
        <span>鉴权已启用</span>
      </div>

      <button
        type="button"
        :disabled="refreshing"
        class="p-2 text-gray-500 hover:text-gray-800 hover:bg-gray-100 rounded-xl transition-all cursor-pointer disabled:opacity-50"
        title="静默刷新数据"
        @click="emit('refresh')"
      >
        <RefreshCw
          class="w-4 h-4 transition-transform"
          :class="{ 'animate-spin text-brand-600': refreshing }"
        />
      </button>

      <button
        v-if="authStore.authEnabled"
        type="button"
        class="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-700 bg-gray-100 hover:bg-gray-200/80 rounded-xl transition-colors cursor-pointer"
        @click="emit('openTokenModal')"
      >
        <Key class="w-3.5 h-3.5 text-gray-500" />
        <span class="hidden sm:inline">密码设置</span>
        <span class="sm:hidden">密码</span>
      </button>
    </div>
  </header>
</template>
