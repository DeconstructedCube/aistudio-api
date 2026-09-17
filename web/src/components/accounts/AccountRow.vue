<script setup lang="ts">
import { ref } from 'vue'
import type { AccountWithStats } from '@/types/accounts.ts'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ModelQuotaGrid from './ModelQuotaGrid.vue'
import {
  CheckCircle2,
  Clock,
  Trash2,
  Edit2,
  Layers,
  RotateCcw,
} from 'lucide-vue-next'

defineProps<{
  account: AccountWithStats
  active: boolean
  activating?: boolean
}>()

const emit = defineEmits<{
  activate: []
  delete: []
  editName: []
  clearCooldown: []
  clearModel: [model: string]
}>()

const modelsExpanded = ref(false)
</script>

<template>
  <div
    class="relative bg-white border rounded-xl p-3 shadow-2xs transition-all hover:shadow-xs"
    :class="[
      active
        ? 'border-brand-300 bg-brand-50/20 ring-1 ring-brand-400/20'
        : 'border-gray-200/80 hover:border-gray-300',
    ]"
  >
    <!-- Sub-Account Header Bar -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
      <div class="flex items-center gap-2.5 min-w-0">
        <!-- User Index Badge: u/0, u/1... -->
        <span
          class="px-2 py-0.5 rounded-full text-xs font-mono font-bold shrink-0"
          :class="[
            active
              ? 'bg-brand-500 text-white'
              : 'bg-blue-50 text-blue-700 border border-blue-200/60',
          ]"
        >
          u/{{ account.auth_user }}
        </span>

        <!-- Account Name & Memo -->
        <div class="truncate">
          <div class="flex items-center gap-1.5">
            <span class="font-semibold text-gray-900 text-xs truncate">
              {{ account.name || 'Google Account' }}
            </span>
            <button
              type="button"
              class="text-gray-300 hover:text-gray-600 transition-colors cursor-pointer"
              title="重命名账号"
              @click="emit('editName')"
            >
              <Edit2 class="w-3 h-3" />
            </button>
          </div>
          <div class="text-[11px] text-gray-400 font-mono truncate">
            <span
              v-if="account.email"
              class="text-gray-600 mr-1"
            >{{ account.email }}</span>
            <span>ID: {{ account.id }}</span>
          </div>
        </div>
      </div>

      <!-- Sub-Account Stats & Controls -->
      <div class="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap">
        <div class="flex items-center gap-2 text-xs font-mono">
          <span class="text-gray-600">总计: <strong>{{ account.requests || 0 }}</strong></span>
          <Badge
            variant="green"
            size="sm"
          >
            {{ account.success || 0 }}
          </Badge>
          <Badge
            :variant="(account.rate_limited || 0) > 0 ? 'red' : 'gray'"
            size="sm"
          >
            429: {{ account.rate_limited || 0 }}
          </Badge>
        </div>

        <!-- Status Pill -->
        <div
          v-if="active"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200"
        >
          <CheckCircle2 class="w-3 h-3" />
          <span>激活</span>
        </div>
        <div
          v-else-if="account.is_available === false"
          class="inline-flex items-center gap-1 text-[11px] font-semibold text-rose-700 bg-rose-50 px-2 py-0.5 rounded-full border border-rose-200"
        >
          <Clock class="w-3 h-3" />
          <span>配额耗尽</span>
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
            v-if="!active"
            variant="secondary"
            size="sm"
            :loading="activating"
            @click="emit('activate')"
          >
            <span>激活</span>
          </Button>

          <button
            v-if="(account.rate_limited || 0) > 0"
            type="button"
            class="p-1 text-gray-400 hover:text-amber-600 hover:bg-amber-50 rounded-lg transition-colors cursor-pointer"
            title="清除该账号锁定"
            @click="emit('clearCooldown')"
          >
            <RotateCcw class="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            class="p-1 text-gray-400 hover:text-brand-600 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
            :class="{ 'text-brand-600 bg-brand-50': modelsExpanded }"
            title="展开/收起模型独立配额详情"
            @click="modelsExpanded = !modelsExpanded"
          >
            <Layers class="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            class="p-1 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
            title="删除此子账号"
            @click="emit('delete')"
          >
            <Trash2 class="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>

    <!-- Level 3: Per-Model Independent Quota/Rate Limits -->
    <ModelQuotaGrid
      v-if="modelsExpanded"
      :account="account"
      @clear-model="emit('clearModel', $event)"
    />
  </div>
</template>
