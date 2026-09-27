<script setup lang="ts">
import { ref, computed, toRef, onMounted } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import { systemApi } from '@/api/system.ts'
import { useCookieGroups, type CookieGroup } from './useCookieGroups.ts'
import CookieGroupBlock from './CookieGroupBlock.vue'
import AccountRow from './AccountRow.vue'
import {
  Cookie,
  Users,
  Search,
  Layers,
  Clock,
  Trash2,
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
const toast = useToastStore()

// 搜索与状态过滤
const searchKeyword = ref('')
const statusFilter = ref<'all' | 'ready' | 'limited' | 'expired'>('all')

const filteredAccounts = computed<AccountWithStats[]>(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  return props.accounts.filter((acc) => {
    // 状态过滤
    if (statusFilter.value === 'expired') {
      if (!acc.session_expired) return false
    } else if (statusFilter.value === 'limited') {
      if (acc.session_expired || acc.is_available !== false) return false
    } else if (statusFilter.value === 'ready') {
      if (acc.session_expired || acc.is_available === false) return false
    }

    // 关键词过滤
    if (!kw) return true
    const inName = (acc.name || '').toLowerCase().includes(kw)
    const inEmail = (acc.email || '').toLowerCase().includes(kw)
    const inId = acc.id.toLowerCase().includes(kw)
    const inAuthUser = `u/${acc.auth_user}`.toLowerCase().includes(kw) || acc.auth_user === kw
    return inName || inEmail || inId || inAuthUser
  })
})

const { cookieGroups } = useCookieGroups(filteredAccounts, toRef(props, 'activeId'))

// 折叠状态持久化
const STORAGE_COLLAPSED_KEY = 'aistudio_collapsed_cookie_ids'
const collapsedCookieIds = ref<Set<string>>(new Set())

onMounted(() => {
  try {
    const raw = localStorage.getItem(STORAGE_COLLAPSED_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        collapsedCookieIds.value = new Set(parsed)
      }
    }
  } catch {
    // ignore
  }
})

function saveCollapsedState(set: Set<string>) {
  try {
    localStorage.setItem(STORAGE_COLLAPSED_KEY, JSON.stringify(Array.from(set)))
  } catch {
    // ignore
  }
}

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
  saveCollapsedState(next)
}

function expandAll() {
  collapsedCookieIds.value = new Set()
  saveCollapsedState(collapsedCookieIds.value)
}

function collapseAll() {
  collapsedCookieIds.value = new Set(cookieGroups.value.map((g) => g.id))
  saveCollapsedState(collapsedCookieIds.value)
}

// 全局展开/收起模型今日配额明细
const forceExpandAllModels = ref(false)

function toggleAllModels() {
  forceExpandAllModels.value = !forceExpandAllModels.value
}

// 美西午夜刷新倒计时计算
const hoursUntilPacificMidnight = computed(() => {
  const now = new Date()
  // 计算基于 UTC-8（美西冬令时/夏令时标准区间）
  const utcNow = now.getTime() + now.getTimezoneOffset() * 60000
  const pacificOffsetHours = -8
  const pacificNow = new Date(utcNow + 3600000 * pacificOffsetHours)
  const hoursLeft = 23 - pacificNow.getHours()
  const minutesLeft = 59 - pacificNow.getMinutes()
  return `${hoursLeft} 小时 ${minutesLeft} 分`
})

// 清理幽灵残留状态
const cleaningGhosts = ref(false)

async function handleCleanupGhosts() {
  cleaningGhosts.value = true
  try {
    const res = await systemApi.cleanupGhosts()
    if (res.cleaned_count > 0) {
      toast.success(`已清理 ${res.cleaned_count} 个已删除账号的状态残留`)
    } else {
      toast.info('未发现幽灵状态残留，凭据库与调度器完全一致')
    }
    await accountsStore.fetchAll()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '清理失败'
    toast.error(msg)
  } finally {
    cleaningGhosts.value = false
  }
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

// 针对登录态失效或受阻账号执行重新验证（解除禁用并在浏览器中重放连接）
async function handleReverifyAccount(acc: AccountWithStats) {
  await systemStore.clearCooldown({ account_id: acc.id })
  await accountsStore.activateAccount(acc.id)
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
    <div class="px-6 py-4 border-b border-gray-100 flex flex-col gap-3 bg-gray-50/40">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <Users class="w-4 h-4 text-brand-600" />
          <h3 class="font-semibold text-gray-900 text-sm">
            已配置账号
          </h3>
          <span class="text-xs font-mono text-gray-500 bg-white border border-gray-200 px-2.5 py-0.5 rounded-full ml-1">
            {{ cookieGroups.length }} 组会话 · {{ filteredAccounts.length }} 个账号
          </span>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            title="一键展开或收起所有账号的模型独立调用统计"
            @click="toggleAllModels"
          >
            <Layers class="w-3.5 h-3.5 text-gray-500" />
            <span>{{ forceExpandAllModels ? '收起模型明细' : '展开模型明细' }}</span>
          </button>

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

          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium text-gray-500 hover:text-rose-700 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer flex items-center gap-1"
            title="清除已从存储中删除但残留在调度器中的无效幽灵状态"
            :disabled="cleaningGhosts"
            @click="handleCleanupGhosts"
          >
            <Trash2 class="w-3.5 h-3.5" />
            <span>{{ cleaningGhosts ? '清理中...' : '清理残留' }}</span>
          </button>
        </div>
      </div>

      <!-- Search, Status Filter & Quota Countdown Bar -->
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 pt-2 border-t border-gray-200/60">
        <!-- Search Input & Status Pills -->
        <div class="flex items-center gap-2 flex-wrap flex-1 min-w-0">
          <div class="relative w-full sm:w-64">
            <Search class="w-3.5 h-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              v-model="searchKeyword"
              type="text"
              placeholder="搜索账号名、邮箱、u/0..."
              class="w-full pl-8 pr-3 py-1.5 bg-white border border-gray-200 rounded-xl text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 font-mono transition-all"
            >
          </div>

          <!-- Status Filter Pills -->
          <div class="flex items-center bg-gray-100 p-0.5 rounded-xl text-xs font-medium">
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'all' ? 'bg-white text-gray-900 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'all'"
            >
              全部
            </button>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'ready' ? 'bg-white text-emerald-700 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'ready'"
            >
              就绪
            </button>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'limited' ? 'bg-white text-amber-700 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'limited'"
            >
              配额耗尽
            </button>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg transition-all cursor-pointer"
              :class="statusFilter === 'expired' ? 'bg-white text-rose-700 font-semibold shadow-2xs' : 'text-gray-500 hover:text-gray-900'"
              @click="statusFilter = 'expired'"
            >
              登录态失效
            </button>
          </div>
        </div>

        <!-- Quota Reset Functional Info -->
        <div class="flex items-center gap-1.5 text-xs text-gray-500 font-mono shrink-0">
          <Clock class="w-3.5 h-3.5 text-gray-400" />
          <span>每日配额刷新 (00:00 PST): 还剩 {{ hoursUntilPacificMidnight }}</span>
        </div>
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
          :force-expanded="forceExpandAllModels"
          @activate="accountsStore.activateAccount(acc.id)"
          @delete="handleDeleteAccount(acc)"
          @edit-name="emit('editName', acc)"
          @clear-cooldown="acc.session_expired ? handleReverifyAccount(acc) : handleClearAccountCooldown(acc.id)"
          @clear-model="handleClearModelCooldown(acc.id, $event)"
        />
      </CookieGroupBlock>

      <!-- Empty State -->
      <div
        v-if="!filteredAccounts.length"
        class="py-16 text-center text-gray-400 space-y-3"
      >
        <Cookie class="w-10 h-10 text-gray-300 mx-auto" />
        <div class="text-sm font-medium text-gray-600">
          {{ searchKeyword || statusFilter !== 'all' ? '未找到匹配的账号' : '未导入账号' }}
        </div>
        <p class="text-xs text-gray-400 max-w-sm mx-auto">
          {{ searchKeyword || statusFilter !== 'all' ? '请尝试更换搜索词或筛选条件。' : '点击上方导入按钮添加凭据。' }}
        </p>
      </div>
    </div>
  </div>
</template>
