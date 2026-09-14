<script setup lang="ts">
import { ref, computed } from 'vue'
import type { AccountWithStats } from '@/types'
import { useAccountsStore } from '@/stores/accounts.ts'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import {
  Cookie,
  CheckCircle2,
  Clock,
  Trash2,
  Edit2,
  ChevronDown,
  ChevronRight,
  Flame,
  FolderTree,
  FolderMinus,
  Layers,
} from 'lucide-vue-next'

const props = defineProps<{
  accounts: AccountWithStats[]
  activeId: string
  loading?: boolean
}>()

const emit = defineEmits<{
  editName: [account: AccountWithStats]
}>()

const accountsStore = useAccountsStore()

// 展开/折叠的 Cookie 组集合
const expandedCookieIds = ref<Set<string>>(new Set())

// 展开查看单个账号的模型细分状态
const expandedAccountIds = ref<Set<string>>(new Set())

interface CookieGroup {
  id: string
  name: string
  createdAt: string
  accounts: AccountWithStats[]
  totalRequests: number
  totalSuccess: number
  totalRateLimited: number
  hasActive: boolean
}

const cookieGroups = computed<CookieGroup[]>(() => {
  const map: Record<string, AccountWithStats[]> = {}

  for (const acc of props.accounts) {
    // 优先使用显式 cookie_id，无显式 cookie_id 时使用 created_at 前 16 位归组
    const cid = acc.cookie_id || `cookie_${acc.created_at.slice(0, 16)}`
    if (!map[cid]) {
      map[cid] = []
    }
    map[cid].push(acc)
  }

  return Object.entries(map).map(([cid, accList], idx) => {
    // 按 auth_user 升序排序子账号
    accList.sort((a, b) => {
      const uA = parseInt(a.auth_user || '0', 10)
      const uB = parseInt(b.auth_user || '0', 10)
      return uA - uB
    })

    const earliestCreatedAt = accList[0]?.created_at || ''
    const totalRequests = accList.reduce((sum, a) => sum + (a.requests || 0), 0)
    const totalSuccess = accList.reduce((sum, a) => sum + (a.success || 0), 0)
    const totalRateLimited = accList.reduce((sum, a) => sum + (a.rate_limited || 0), 0)
    const hasActive = accList.some((a) => a.id === props.activeId)

    // 默认如果展开集合为空，自动展开所有组
    if (!expandedCookieIds.value.has(cid) && expandedCookieIds.value.size === 0) {
      expandedCookieIds.value.add(cid)
    }

    return {
      id: cid,
      name: `Cookie 凭据 #${idx + 1}`,
      createdAt: earliestCreatedAt,
      accounts: accList,
      totalRequests,
      totalSuccess,
      totalRateLimited,
      hasActive,
    }
  })
})

function toggleCookieGroup(cid: string) {
  if (expandedCookieIds.value.has(cid)) {
    expandedCookieIds.value.delete(cid)
  } else {
    expandedCookieIds.value.add(cid)
  }
}

function expandAll() {
  for (const g of cookieGroups.value) {
    expandedCookieIds.value.add(g.id)
  }
}

function collapseAll() {
  expandedCookieIds.value.clear()
}

function toggleAccountModels(id: string) {
  if (expandedAccountIds.value.has(id)) {
    expandedAccountIds.value.delete(id)
  } else {
    expandedAccountIds.value.add(id)
  }
}

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return dateStr
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
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden space-y-0">
    <!-- Tree Header Bar -->
    <div class="px-6 py-4 border-b border-gray-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-gray-50/40">
      <div>
        <div class="flex items-center gap-2">
          <FolderTree class="w-4 h-4 text-brand-600" />
          <h3 class="font-semibold text-gray-900 text-sm">
            Google 账号树状层级视图
          </h3>
        </div>
        <p class="text-xs text-gray-400 mt-0.5">
          按导入的 Cookie 凭据分组，层级展开管理子登录账号 (u/0, u/1...) 与模型独立限流
        </p>
      </div>

      <div class="flex items-center gap-2 self-start sm:self-auto">
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          @click="expandAll"
        >
          全部展开
        </button>
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
          @click="collapseAll"
        >
          全部折叠
        </button>
        <span class="text-xs font-mono text-gray-500 bg-white border border-gray-200 px-2.5 py-0.5 rounded-full">
          {{ cookieGroups.length }} 组 Cookie · {{ accounts.length }} 个账号
        </span>
      </div>
    </div>

    <!-- Tree Body -->
    <div class="divide-y divide-gray-100">
      <div
        v-for="group in cookieGroups"
        :key="group.id"
        class="transition-colors"
      >
        <!-- Level 1: Cookie Credential Parent Node -->
        <div
          class="px-5 py-3.5 flex items-center justify-between gap-3 cursor-pointer select-none transition-colors"
          :class="[
            group.hasActive ? 'bg-brand-50/30 hover:bg-brand-50/50' : 'bg-white hover:bg-gray-50/80',
          ]"
          @click="toggleCookieGroup(group.id)"
        >
          <div class="flex items-center gap-3 min-w-0">
            <!-- Expand Chevron -->
            <button
              type="button"
              class="p-1 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-200/60 transition-colors"
            >
              <ChevronDown
                v-if="expandedCookieIds.has(group.id)"
                class="w-4 h-4"
              />
              <ChevronRight
                v-else
                class="w-4 h-4"
              />
            </button>

            <!-- Cookie Icon -->
            <div
              class="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 shadow-2xs"
              :class="group.hasActive ? 'bg-brand-500 text-white' : 'bg-gray-100 text-gray-600'"
            >
              <Cookie class="w-4 h-4" />
            </div>

            <!-- Group Info -->
            <div class="truncate">
              <div class="flex items-center gap-2">
                <span class="font-bold text-gray-900 text-xs tracking-tight">
                  {{ group.name }}
                </span>
                <span class="text-[11px] font-mono text-gray-400">
                  ({{ group.accounts.length }} 个子账号)
                </span>
                <span
                  v-if="group.hasActive"
                  class="px-2 py-0.5 text-[10px] font-semibold rounded-full bg-emerald-100 text-emerald-800"
                >
                  当前激活位于此组
                </span>
              </div>
              <div class="text-[11px] text-gray-400 font-mono flex items-center gap-2 mt-0.5">
                <span>导入时间: {{ formatDate(group.createdAt) }}</span>
                <span>·</span>
                <span
                  class="truncate max-w-[120px]"
                  :title="group.id"
                >分组 ID: {{ group.id }}</span>
              </div>
            </div>
          </div>

          <!-- Group Summary & Group Actions -->
          <div
            class="flex items-center gap-3 shrink-0"
            @click.stop
          >
            <div class="hidden sm:flex items-center gap-2 text-xs font-mono">
              <span class="text-gray-500">调用: <strong class="text-gray-800">{{ group.totalRequests }}</strong></span>
              <span class="text-gray-300">|</span>
              <span :class="group.totalRateLimited > 0 ? 'text-rose-600 font-bold' : 'text-gray-400'">
                429: {{ group.totalRateLimited }}
              </span>
            </div>

            <button
              type="button"
              class="flex items-center gap-1 px-2 py-1 text-[11px] text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
              title="删除整份 Cookie 及其下所有子账号"
              @click="handleDeleteCookieGroup(group)"
            >
              <FolderMinus class="w-3.5 h-3.5" />
              <span class="hidden md:inline">删除整组</span>
            </button>
          </div>
        </div>

        <!-- Level 2: Sub-Accounts (Branch Nodes) -->
        <div
          v-if="expandedCookieIds.has(group.id)"
          class="bg-gray-50/30 px-4 sm:px-6 py-2"
        >
          <div class="relative pl-6 space-y-2 border-l-2 border-dashed border-gray-200 ml-4 my-1">
            <div
              v-for="(acc, index) in group.accounts"
              :key="acc.id"
              class="relative bg-white border rounded-xl p-3 shadow-2xs transition-all hover:shadow-xs"
              :class="[
                acc.id === activeId
                  ? 'border-brand-300 bg-brand-50/20 ring-1 ring-brand-400/20'
                  : 'border-gray-200/80 hover:border-gray-300',
              ]"
            >
              <!-- Tree Horizontal Branch Indicator Line -->
              <div
                class="absolute -left-6 top-5 w-6 h-px border-t-2 border-dashed border-gray-200"
              />

              <!-- Sub-Account Header Bar -->
              <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div class="flex items-center gap-2.5 min-w-0">
                  <!-- User Index Badge: u/0, u/1... -->
                  <span
                    class="px-2 py-0.5 rounded-full text-xs font-mono font-bold shrink-0"
                    :class="[
                      acc.id === activeId
                        ? 'bg-brand-500 text-white'
                        : 'bg-blue-50 text-blue-700 border border-blue-200/60',
                    ]"
                  >
                    u/{{ acc.auth_user !== undefined ? acc.auth_user : index }}
                  </span>

                  <!-- Account Name & Memo -->
                  <div class="truncate">
                    <div class="flex items-center gap-1.5">
                      <span class="font-semibold text-gray-900 text-xs truncate">
                        {{ acc.name || 'Google Account' }}
                      </span>
                      <button
                        type="button"
                        class="text-gray-300 hover:text-gray-600 transition-colors cursor-pointer"
                        title="重命名账号"
                        @click="emit('editName', acc)"
                      >
                        <Edit2 class="w-3 h-3" />
                      </button>
                    </div>
                    <div class="text-[11px] text-gray-400 font-mono truncate">
                      <span
                        v-if="acc.email"
                        class="text-gray-600 mr-1"
                      >{{ acc.email }}</span>
                      <span>ID: {{ acc.id }}</span>
                    </div>
                  </div>
                </div>

                <!-- Sub-Account Stats & Controls -->
                <div class="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap">
                  <div class="flex items-center gap-2 text-xs font-mono">
                    <span class="text-gray-600">总计: <strong>{{ acc.requests || 0 }}</strong></span>
                    <Badge
                      variant="green"
                      size="sm"
                    >
                      {{ acc.success || 0 }}
                    </Badge>
                    <Badge
                      :variant="(acc.rate_limited || 0) > 0 ? 'red' : 'gray'"
                      size="sm"
                    >
                      429: {{ acc.rate_limited || 0 }}
                    </Badge>
                  </div>

                  <!-- Status Pill -->
                  <div
                    v-if="acc.id === activeId"
                    class="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200"
                  >
                    <CheckCircle2 class="w-3 h-3" />
                    <span>激活</span>
                  </div>
                  <div
                    v-else-if="acc.cooldown_remaining && acc.cooldown_remaining > 0"
                    class="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200"
                  >
                    <Clock class="w-3 h-3 animate-pulse" />
                    <span>冷却 {{ acc.cooldown_remaining }}s</span>
                  </div>
                  <div
                    v-else
                    class="text-[11px] text-gray-400 font-medium"
                  >
                    就绪
                  </div>

                  <!-- Action Buttons -->
                  <div class="flex items-center gap-1.5 ml-1">
                    <Button
                      v-if="acc.id !== activeId"
                      variant="secondary"
                      size="sm"
                      :loading="accountsStore.activatingId === acc.id"
                      @click="accountsStore.activateAccount(acc.id)"
                    >
                      <span>激活</span>
                    </Button>

                    <button
                      type="button"
                      class="p-1 text-gray-400 hover:text-brand-600 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
                      :class="{ 'text-brand-600 bg-brand-50': expandedAccountIds.has(acc.id) }"
                      title="展开/收起模型独立限流详情"
                      @click="toggleAccountModels(acc.id)"
                    >
                      <Layers class="w-3.5 h-3.5" />
                    </button>

                    <button
                      type="button"
                      class="p-1 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
                      title="删除此子账号"
                      @click="handleDeleteAccount(acc)"
                    >
                      <Trash2 class="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>

              <!-- Level 3: Per-Model Independent Quota/Rate Limits -->
              <div
                v-if="expandedAccountIds.has(acc.id)"
                class="mt-3 pt-3 border-t border-gray-100 space-y-2 bg-gray-50/60 p-3 rounded-lg"
              >
                <div class="flex items-center justify-between text-[11px] font-semibold text-gray-600">
                  <span class="flex items-center gap-1.5">
                    <Flame class="w-3.5 h-3.5 text-amber-500" />
                    <span>该子账号各模型独立限额状态 (美西时间每日独立重置)</span>
                  </span>
                  <span class="text-gray-400 font-normal">
                    最后调用: {{ formatDate(acc.last_used) }}
                  </span>
                </div>

                <div
                  v-if="acc.model_requests && Object.keys(acc.model_requests).length"
                  class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2 pt-1"
                >
                  <div
                    v-for="(reqCount, modelKey) in acc.model_requests"
                    :key="String(modelKey)"
                    class="p-2 bg-white rounded-md border border-gray-200/70 flex items-center justify-between text-xs"
                  >
                    <div class="truncate mr-2 font-mono">
                      <div
                        class="font-medium text-gray-800 truncate"
                        :title="String(modelKey)"
                      >
                        {{ String(modelKey).replace(/^models\//, '') }}
                      </div>
                      <div class="text-[10px] text-gray-400">
                        调用: {{ reqCount }} 次
                      </div>
                    </div>

                    <div>
                      <span
                        v-if="acc.model_cooldowns && acc.model_cooldowns[String(modelKey)]"
                        class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-50 text-rose-700 border border-rose-200"
                      >
                        冷却 ({{ acc.model_cooldowns[String(modelKey)] }}s)
                      </span>
                      <span
                        v-else-if="acc.model_rate_limited && acc.model_rate_limited[String(modelKey)]"
                        class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-50 text-amber-700"
                      >
                        曾限流 ({{ acc.model_rate_limited[String(modelKey)] }})
                      </span>
                      <span
                        v-else
                        class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-50 text-emerald-700"
                      >
                        正常
                      </span>
                    </div>
                  </div>
                </div>

                <div
                  v-else
                  class="text-[11px] text-gray-400 italic py-1"
                >
                  暂无该账号各模型的调用历史
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Empty State -->
      <div
        v-if="!accounts.length"
        class="py-16 text-center text-gray-400 space-y-3"
      >
        <Cookie class="w-10 h-10 text-gray-300 mx-auto" />
        <div class="text-sm font-medium text-gray-600">
          暂无配置的 Google 账号
        </div>
        <p class="text-xs text-gray-400 max-w-sm mx-auto">
          点击右上角“+ 导入 Cookies”按钮，粘贴从浏览器导出的 Cookie 即可自动探活并导入账号。
        </p>
      </div>
    </div>
  </div>
</template>
