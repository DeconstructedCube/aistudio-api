<script setup lang="ts">
import { LayoutDashboard, Users, Sliders, Key, LogOut } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth.ts'
import { useRouter, useRoute } from 'vue-router'
import { useSystemHealth } from '@/composables/useSystemHealth.ts'

defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [val: boolean]
  openTokenModal: []
}>()

const authStore = useAuthStore()
const router = useRouter()
const route = useRoute()
const { state: healthState } = useSystemHealth(15000)

const navItems = [
  {
    name: '控制面板',
    path: '/',
    icon: LayoutDashboard,
  },
  {
    name: '账号管理',
    path: '/accounts',
    icon: Users,
  },
  {
    name: '系统与模型配置',
    path: '/settings',
    icon: Sliders,
  },
]

function handleNav(path: string) {
  router.push(path)
  emit('update:open', false)
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<template>
  <!-- Mobile Backdrop -->
  <div
    v-if="open"
    class="fixed inset-0 z-40 bg-gray-900/40 backdrop-blur-xs lg:hidden transition-opacity"
    @click="emit('update:open', false)"
  />

  <!-- Sidebar -->
  <aside
    :class="[
      'fixed top-0 bottom-0 left-0 z-40 w-64 bg-white border-r border-gray-200/80 flex flex-col transition-transform duration-200 ease-in-out lg:translate-x-0',
      open ? 'translate-x-0 shadow-2xl lg:shadow-none' : '-translate-x-full',
    ]"
  >
    <!-- Logo Header -->
    <div class="h-16 px-6 border-b border-gray-100 flex items-center gap-3">
      <div class="w-8 h-8 rounded-xl bg-brand-500 text-white font-bold text-sm flex items-center justify-center shadow-xs">
        AI
      </div>
      <div class="flex flex-col">
        <span class="font-bold text-sm text-gray-900 leading-none">AI Studio Proxy</span>
        <span class="text-[11px] text-gray-400 font-mono mt-1">控制台</span>
      </div>
    </div>

    <!-- Nav Items -->
    <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
      <button
        v-for="item in navItems"
        :key="item.path"
        type="button"
        :class="[
          'w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition-colors text-left cursor-pointer',
          route.path === item.path
            ? 'bg-brand-50 text-brand-700 font-semibold'
            : 'text-gray-600 hover:bg-gray-100/80 hover:text-gray-900',
        ]"
        @click="handleNav(item.path)"
      >
        <component
          :is="item.icon"
          class="w-4 h-4 shrink-0"
          :class="route.path === item.path ? 'text-brand-600' : 'text-gray-400'"
        />
        <span>{{ item.name }}</span>
      </button>
    </nav>

    <!-- Footer Status / Action -->
    <div class="p-3 border-t border-gray-100 flex flex-col gap-2">
      <button
        v-if="authStore.authEnabled"
        type="button"
        class="w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer"
        @click="emit('openTokenModal')"
      >
        <div class="flex items-center gap-2">
          <Key class="w-4 h-4 text-gray-400" />
          <span>控制台密码设置</span>
        </div>
        <span class="text-[10px] text-gray-400 font-mono">配置</span>
      </button>
      <div
        v-else
        class="px-3 py-2 rounded-xl text-xs bg-amber-50/70 border border-amber-200/60 text-amber-800"
      >
        <div class="font-semibold flex items-center gap-1.5 text-[11px]">
          <span>免密访问模式</span>
        </div>
        <div class="text-[10px] text-amber-700/80 mt-0.5 leading-tight">
          如需保护控制台，请在服务端环境变量配置 AISTUDIO_WEB_PASSWORD
        </div>
      </div>

      <div class="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-xl text-xs text-gray-500">
        <div class="flex items-center gap-2">
          <span
            class="w-2 h-2 rounded-full transition-colors"
            :class="{
              'bg-emerald-500 animate-pulse': healthState === 'online',
              'bg-rose-500': healthState === 'offline',
              'bg-amber-400 animate-pulse': healthState === 'checking',
            }"
          />
          <span class="font-medium text-gray-700">
            {{ healthState === 'online' ? '系统在线' : healthState === 'offline' ? '服务离线' : '探测中' }}
          </span>
        </div>

        <button
          v-if="authStore.authEnabled"
          type="button"
          class="text-gray-400 hover:text-rose-600 transition-colors p-1 rounded hover:bg-gray-200 cursor-pointer"
          title="退出登录"
          @click="handleLogout"
        >
          <LogOut class="w-4 h-4" />
        </button>
      </div>
    </div>
  </aside>
</template>
