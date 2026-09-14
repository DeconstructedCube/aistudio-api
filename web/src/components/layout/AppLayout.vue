<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from './Sidebar.vue'
import Topbar from './Topbar.vue'
import TokenSettingsModal from '@/components/modals/TokenSettingsModal.vue'
import ToastContainer from '@/components/ui/ToastContainer.vue'

const route = useRoute()
const sidebarOpen = ref(false)
const tokenModalOpen = ref(false)
const refreshing = ref(false)

const titleMap: Record<string, string> = {
  '/': '控制面板',
  '/accounts': '账号管理与调度',
  '/settings': '系统与模型配置',
}

const currentTitle = computed(() => {
  return titleMap[route.path] || 'AI Studio Proxy'
})

function handleRefresh() {
  window.location.reload()
}
</script>

<template>
  <div class="min-h-screen bg-gray-50 flex">
    <ToastContainer />

    <!-- Sidebar -->
    <Sidebar
      :open="sidebarOpen"
      @update:open="sidebarOpen = $event"
      @open-token-modal="tokenModalOpen = true"
    />

    <!-- Main Content Area -->
    <div class="flex-1 lg:pl-64 flex flex-col min-w-0">
      <Topbar
        :title="currentTitle"
        :refreshing="refreshing"
        @toggle-sidebar="sidebarOpen = !sidebarOpen"
        @open-token-modal="tokenModalOpen = true"
        @refresh="handleRefresh"
      />

      <main class="flex-1 p-4 sm:p-6 lg:p-8 max-w-7xl w-full mx-auto">
        <slot />
      </main>
    </div>

    <!-- Global Token Settings Modal -->
    <TokenSettingsModal v-model="tokenModalOpen" />
  </div>
</template>
