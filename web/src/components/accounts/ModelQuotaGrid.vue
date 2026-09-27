<script setup lang="ts">
import type { AccountWithStats } from '@/types/accounts.ts'
import { formatDate } from '@/utils/format.ts'
import { Flame, RotateCcw } from 'lucide-vue-next'

defineProps<{
  account: AccountWithStats
}>()

const emit = defineEmits<{
  clearModel: [model: string]
}>()
</script>

<template>
  <div class="mt-3 pt-3 border-t border-gray-100 space-y-2 bg-gray-50/70 p-3 rounded-xl">
    <div class="flex items-center justify-between text-[11px] font-semibold text-gray-600">
      <span class="flex items-center gap-1.5">
        <Flame class="w-3.5 h-3.5 text-amber-500" />
        <span>各模型今日调用与配额</span>
      </span>
      <span class="text-gray-400 font-normal font-mono">
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
        class="p-2.5 bg-white rounded-lg border border-gray-200/80 flex items-center justify-between text-xs shadow-2xs"
      >
        <div class="truncate mr-2 font-mono">
          <div
            class="font-semibold text-gray-900 truncate"
            :title="String(modelKey)"
          >
            {{ String(modelKey).replace(/^models\//, '') }}
          </div>
          <div class="text-[10px] text-gray-500 mt-0.5 space-x-1">
            <span>今日: <strong>{{ reqCount }}</strong></span>
            <span>·</span>
            <span :class="(account.model_rate_limited?.[String(modelKey)] || 0) > 0 ? 'text-rose-600 font-bold' : 'text-gray-400'">
              429: {{ account.model_rate_limited?.[String(modelKey)] || 0 }}
            </span>
          </div>
        </div>

        <div class="shrink-0 flex items-center gap-1.5">
          <button
            v-if="account.model_cooldowns && account.model_cooldowns[String(modelKey)]"
            type="button"
            class="px-2 py-0.5 rounded text-[10px] font-medium bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 cursor-pointer flex items-center gap-1"
            title="点击重置该模型锁定"
            @click="emit('clearModel', String(modelKey))"
          >
            <RotateCcw class="w-2.5 h-2.5" />
            <span>配额耗尽 ({{ account.model_cooldowns[String(modelKey)] }}s)</span>
          </button>
          <span
            v-else-if="account.model_drip_mode && account.model_drip_mode[String(modelKey)]"
            class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-50 text-blue-700 border border-blue-200"
          >
            滴灌重试中
          </span>
          <span
            v-else-if="account.model_rate_limited && account.model_rate_limited[String(modelKey)]"
            class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-200"
          >
            429: {{ account.model_rate_limited[String(modelKey)] }}
          </span>
          <span
            v-else
            class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200"
          >
            就绪
          </span>
        </div>
      </div>
    </div>
    <div
      v-else
      class="text-[11px] text-gray-400 italic py-1"
    >
      今日暂无模型调用记录
    </div>
  </div>
</template>
