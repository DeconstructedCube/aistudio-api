<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import type { AccountWithStats } from '@/types'
import Button from '@/components/ui/Button.vue'
import AccountTable from '@/components/accounts/AccountTable.vue'
import RotationConfigCard from '@/components/accounts/RotationConfigCard.vue'
import CookieImportModal from '@/components/modals/CookieImportModal.vue'
import EditAccountModal from '@/components/modals/EditAccountModal.vue'
import { Plus } from 'lucide-vue-next'

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()

const importModalOpen = ref(false)
const editModalOpen = ref(false)
const editingAccount = ref<AccountWithStats | null>(null)

async function loadData() {
  await Promise.all([
    accountsStore.fetchAll(),
    systemStore.fetchRotation(),
  ])
}

onMounted(() => {
  loadData()
})

function handleEditName(acc: AccountWithStats) {
  editingAccount.value = acc
  editModalOpen.value = true
}
</script>

<template>
  <div class="space-y-6">
    <!-- Top Action Bar -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      <div>
        <h2 class="text-xl font-bold text-gray-900 tracking-tight">
          账号管理与调度
        </h2>
        <p class="text-xs text-gray-500 mt-0.5">
          配置多 Google 账号 Cookie，设置轮询策略与限流冷却调度
        </p>
      </div>

      <div class="flex items-center gap-2">
        <Button
          variant="primary"
          size="md"
          @click="importModalOpen = true"
        >
          <Plus class="w-4 h-4" />
          <span>导入 Cookies</span>
        </Button>
      </div>
    </div>

    <!-- Rotation Strategy Configuration -->
    <RotationConfigCard />

    <!-- Account Table -->
    <AccountTable
      :accounts="accountsStore.accountRows"
      :active-id="accountsStore.activeId"
      :loading="accountsStore.loading"
      @edit-name="handleEditName"
    />

    <!-- Cookie Import Modal -->
    <CookieImportModal v-model="importModalOpen" />

    <!-- Edit Account Name Modal -->
    <EditAccountModal
      v-model="editModalOpen"
      :account="editingAccount"
    />
  </div>
</template>
