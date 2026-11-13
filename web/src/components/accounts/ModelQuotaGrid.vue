<script setup lang="ts">
import type { AccountWithStats } from '@/types/accounts.ts'
import type { ModelDailyDetail } from '@/types/rotation.ts'
import { formatDate, formatTokenCount } from '@/utils/format.ts'
import { Flame, RotateCcw, Brain, Database } from 'lucide-vue-next'

const props = defineProps<{
  account: AccountWithStats
}>()

const emit = defineEmits<{
  clearModel: [model: string]
}>()

function getModelDetail(modelKey: string): ModelDailyDetail | null {
  const today = props.account.today_models || {}
  return today[modelKey] || null
}
</script>

<template>
  <div class="mt-3 pt-3 border-t border-gray-100 space-y-2 bg-gray-50/80 p-3.5 rounded-xl">
    <div class="flex items-center justify-between text-[11px] font-semibold text-gray-700">
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
      class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 pt-1"
    >
      <div
        v-for="(reqCount, modelKey) in account.model_requests"
        :key="String(modelKey)"
        class="p-3 bg-white rounded-xl border border-gray-200/90 flex flex-col justify-between text-xs shadow-2xs gap-2"
      >
        <div>
          <div class="flex items-center justify-between gap-1 font-mono">
            <span
              class="font-semibold text-gray-900 truncate text-[11px]"
              :title="String(modelKey)"
            >
              {{ String(modelKey).replace(/^models\//, '') }}
            </span>
            <div class="shrink-0 flex items-center gap-1">
              <button
                v-if="account.model_cooldowns && account.model_cooldowns[String(modelKey)]"
                type="button"
                class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 cursor-pointer flex items-center gap-0.5"
                title="点击重置该模型锁定"
                @click="emit('clearModel', String(modelKey))"
              >
                <RotateCcw class="w-2.5 h-2.5" />
                <span>429 ({{ account.model_cooldowns[String(modelKey)] }}s)</span>
              </button>
              <span
                v-else-if="account.model_drip_mode && account.model_drip_mode[String(modelKey)]"
                class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-50 text-blue-700 border border-blue-200"
              >
                滴灌
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

          <!-- Usage & Quota Progress Bar -->
          <div
            v-if="getModelDetail(String(modelKey))"
            class="mt-2 space-y-1 font-mono text-[10px]"
          >
            <div class="flex justify-between text-gray-500">
              <span>今日调用: <strong>{{ reqCount }}</strong> / {{ getModelDetail(String(modelKey))!.configured_quota }} 次</span>
              <span>{{ Math.round(getModelDetail(String(modelKey))!.quota_used_percent) }}%</span>
            </div>
            <div
              v-if="getModelDetail(String(modelKey))!.configured_token_quota"
              class="flex justify-between text-gray-500"
            >
              <span>今日 Token: <strong>{{ formatTokenCount(getModelDetail(String(modelKey))!.total_tokens) }}</strong> / {{ formatTokenCount(getModelDetail(String(modelKey))!.configured_token_quota) }}</span>
              <span>{{ Math.round(getModelDetail(String(modelKey))!.token_quota_used_percent || 0) }}%</span>
            </div>
            <div
              v-else-if="getModelDetail(String(modelKey))!.total_tokens > 0"
              class="flex justify-between text-gray-400"
            >
              <span>今日 Token: {{ formatTokenCount(getModelDetail(String(modelKey))!.total_tokens) }}</span>
              <span>不限</span>
            </div>
            <div class="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
              <div
                class="h-1.5 rounded-full transition-all duration-300"
                :class="[
                  getModelDetail(String(modelKey))!.quota_used_percent >= 100
                    ? 'bg-amber-500'
                    : getModelDetail(String(modelKey))!.quota_used_percent >= 80
                      ? 'bg-amber-400'
                      : 'bg-brand-500',
                ]"
                :style="{ width: `${Math.min(100, Math.round(getModelDetail(String(modelKey))!.quota_used_percent))}%` }"
              />
            </div>
          </div>
          <div
            v-else
            class="mt-1.5 text-[10px] text-gray-500 font-mono"
          >
            今日调用: <strong>{{ reqCount }}</strong> 次
          </div>
        </div>

        <!-- Token breakdown pills if available -->
        <div
          v-if="getModelDetail(String(modelKey)) && (getModelDetail(String(modelKey))!.reasoning_tokens > 0 || getModelDetail(String(modelKey))!.cached_tokens > 0)"
          class="pt-1.5 border-t border-gray-100 flex items-center gap-1.5 flex-wrap font-mono text-[10px]"
        >
          <span
            v-if="getModelDetail(String(modelKey))!.reasoning_tokens > 0"
            class="inline-flex items-center gap-0.5 text-purple-700 bg-purple-50 px-1.5 py-0.2 rounded border border-purple-200/50"
            title="思考推理 Token"
          >
            <Brain class="w-2.5 h-2.5" />
            <span>{{ getModelDetail(String(modelKey))!.reasoning_tokens.toLocaleString() }}</span>
          </span>
          <span
            v-if="getModelDetail(String(modelKey))!.cached_tokens > 0"
            class="inline-flex items-center gap-0.5 text-teal-700 bg-teal-50 px-1.5 py-0.2 rounded border border-teal-200/50"
            title="命中缓存 Token"
          >
            <Database class="w-2.5 h-2.5" />
            <span>{{ getModelDetail(String(modelKey))!.cached_tokens.toLocaleString() }}</span>
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
