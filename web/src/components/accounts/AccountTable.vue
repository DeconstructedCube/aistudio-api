<script setup lang="ts">
import { ref, toRef } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useCookieGroups, type CookieGroup } from './useCookieGroups.ts'
import CookieGroupBlock from './CookieGroupBlock.vue'
import AccountRow from './AccountRow.vue'
import {
  Cookie,
  Users,
} from 'lucide-vue-next'

const props = defineProps<{
  accounts: AccountWithStats[]
  activeId: string
}>()

const emit = defineEmits<{
  editName: [account: AccountWithStats]
}>()

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()

const { cookieGroups } = useCookieGroups(toRef(props, 'accounts'), toRef(props, 'activeId'))

// 记录被折叠的 Cookie 组 ID，默认所有组全部展开展示账号
const collapsedCookieIds = ref<Set<string>>(new Set())

function isGroupCollapsed(cid: string): boolean {
  return collapsedCookieIds.value.has(cid)
}

function toggleCookieGroup(cid: string) {
  const next = new Set(collapsedCookieIds.value)
  if (next.has(cid)) {
    next.delete(cid)
  } else {
    next.add(cid)
  }
  collapsedCookieIds.value = next
}

function expandAll() {
  collapsedCookieIds.value = new Set()
}

function collapseAll() {
  collapsedCookieIds.value = new Set(cookieGroups.value.map((g) => g.id))
}

async function handleDeleteAccount(acc: AccountWithStats) {
  const name = acc.name || acc.email || acc.id
  if (confirm(`确定要删除子账号 "${name}" 吗？此操作不可逆。`)) {
    await accountsStore.deleteAccount(acc.id)
  }
}

async function handleDeleteCookieGroup(group: CookieGroup) {
  if (confirm(`确定要删除该 Cookie 凭据及其关联的全部 ${group.accounts.length} 个子账号吗？`)) {
    await accountsStore.deleteCookieGroup(group.id)
  }
}

async function handleClearAccountCooldown(accountId: string) {
  await systemStore.clearCooldown({ account_id: accountId })
  await accountsStore.fetchAll()
}

async function handleClearModelCooldown(accountId: string, model: string) {
  await systemStore.clearCooldown({ account_id: accountId, model })
  await accountsStore.fetchAll()
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden space-y-0">
    <!-- Header Bar -->
    <div class="px-6 py-4 border-b border-gray-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-gray-50/40">
      <div>
        <div class="flex items-center gap-2">
          <Users class="w-4 h-4 text-brand-600" />
          <h3 class="font-semibold text-gray-900 text-sm">
            已配置账号
          </h3>
        </div>
        <p class="text-xs text-gray-400 mt-0.5">
          查看已导入的 Google 账号列表及各模型调用状态
        </p>
      </div>

      <div class="flex items-center gap-2 self-start sm:self-auto">
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          @click="expandAll"
        >
          展开全部
        </button>
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          @click="collapseAll"
        >
          收起全部
        </button>
        <span class="text-xs font-mono text-gray-500 bg-white border border-gray-200 px-2.5 py-0.5 rounded-full">
          {{ cookieGroups.length }} 组会话 · {{ accounts.length }} 个账号
        </span>
      </div>
    </div>

    <!-- Tree Body -->
    <div class="divide-y divide-gray-100">
      <CookieGroupBlock
        v-for="group in cookieGroups"
        :key="group.id"
        :group="group"
        :collapsed="isGroupCollapsed(group.id)"
        @toggle="toggleCookieGroup(group.id)"
        @delete-group="handleDeleteCookieGroup(group)"
      >
        <AccountRow
          v-for="acc in group.accounts"
          :key="acc.id"
          :account="acc"
          :active="acc.id === activeId"
          :activating="accountsStore.activatingId === acc.id"
          @activate="accountsStore.activateAccount(acc.id)"
          @delete="handleDeleteAccount(acc)"
          @edit-name="emit('editName', acc)"
          @clear-cooldown="handleClearAccountCooldown(acc.id)"
          @clear-model="handleClearModelCooldown(acc.id, $event)"
        />
      </CookieGroupBlock>

      <!-- Empty State -->
      <div
        v-if="!accounts.length"
        class="py-16 text-center text-gray-400 space-y-3"
      >
        <Cookie class="w-10 h-10 text-gray-300 mx-auto" />
        <div class="text-sm font-medium text-gray-600">
          未导入账号
        </div>
        <p class="text-xs text-gray-400 max-w-sm mx-auto">
          点击上方导入按钮添加凭据。
        </p>
      </div>
    </div>
  </div>
</template>
