<script setup lang="ts">
import type { AccountWithStats } from '@/types/accounts.ts'
import { formatDate } from '@/utils/format.ts'
import { Flame } from 'lucide-vue-next'

defineProps<{
  account: AccountWithStats
}>()

const emit = defineEmits<{
  clearModel: [model: string]
}>()
</script>

<template>
  <div class="mt-3 pt-3 border-t border-gray-100 space-y-2 bg-gray-50/60 p-3 rounded-lg">
    <div class="flex items-center justify-between text-[11px] font-semibold text-gray-600">
      <span class="flex items-center gap-1.5">
        <Flame class="w-3.5 h-3.5 text-amber-500" />
        <span>模型配额状态 (每日 00:00 PST 重置)</span>
      </span>
      <span class="text-gray-400 font-normal">
        最后调用: {{ formatDate(account.last_used) }}
      </span>
    </div>

    <div
      v-if="account.model_requests && Object.keys(account.model_requests).length"
      class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2 pt-1"
    >
      <div
        v-for="(reqCount, modelKey) in account.model_requests"
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
          <button
            v-if="account.model_cooldowns && account.model_cooldowns[String(modelKey)]"
            type="button"
            class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 cursor-pointer"
            title="点击解除此模型锁定"
            @click="emit('clearModel', String(modelKey))"
          >
            配额耗尽 (点击重置)
          </button>
          <span
            v-else-if="account.model_rate_limited && account.model_rate_limited[String(modelKey)]"
            class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-50 text-amber-700"
          >
            限流: {{ account.model_rate_limited[String(modelKey)] }}
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
      无调用记录
    </div>
  </div>
</template>
