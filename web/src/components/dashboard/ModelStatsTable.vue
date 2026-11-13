<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ModelStatItem } from '@/types/stats.ts'
import Badge from '@/components/ui/Badge.vue'
import { formatDate, formatTokenCount } from '@/utils/format.ts'
import {
  Layers,
  ChevronDown,
  ChevronRight,
  Brain,
  Database,
  Users,
} from 'lucide-vue-next'

const props = defineProps<{
  stats: Record<string, ModelStatItem>
}>()

const expandedModels = ref<Record<string, boolean>>({})

function toggleExpand(key: string) {
  expandedModels.value[key] = !expandedModels.value[key]
}

const statList = computed(() => {
  return Object.entries(props.stats || {}).map(([rawName, item]) => ({
    name: rawName.replace(/^models\//, ''),
    rawName,
    ...item,
  }))
})
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
      <div class="flex items-center gap-2">
        <Layers class="w-4 h-4 text-brand-600" />
        <h3 class="font-semibold text-gray-900 text-sm">
          模型调用统计
        </h3>
      </div>
      <span class="text-xs text-gray-400 font-mono">
        {{ statList.length }} 个模型记录
      </span>
    </div>

    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse text-sm">
        <thead>
          <tr class="bg-gray-50/70 border-b border-gray-100 text-xs font-semibold text-gray-500 uppercase tracking-wider">
            <th class="py-3 px-4 w-8" />
            <th class="py-3 px-4">
              模型标识
            </th>
            <th class="py-3 px-3 text-center">
              请求数
            </th>
            <th class="py-3 px-3 text-center">
              成功
            </th>
            <th class="py-3 px-3 text-center">
              429
            </th>
            <th class="py-3 px-3 text-center">
              错误
            </th>
            <th class="py-3 px-4 text-center">
              Tokens
            </th>
            <th class="py-3 px-6 text-right">
              最后调用
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 font-mono text-xs">
          <template
            v-for="item in statList"
            :key="item.rawName"
          >
            <tr
              class="hover:bg-gray-50/80 transition-colors cursor-pointer"
              :class="{ 'bg-gray-50/40': expandedModels[item.rawName] }"
              @click="toggleExpand(item.rawName)"
            >
              <td class="py-3 px-3 text-center text-gray-400">
                <ChevronDown
                  v-if="expandedModels[item.rawName]"
                  class="w-4 h-4 inline"
                />
                <ChevronRight
                  v-else
                  class="w-4 h-4 inline"
                />
              </td>
              <td class="py-3.5 px-4 font-semibold text-gray-900">
                <div class="flex items-center gap-1.5">
                  <span>{{ item.name }}</span>
                  <span
                    v-if="item.account_breakdown && item.account_breakdown.length"
                    class="px-1.5 py-0.2 rounded text-[10px] bg-gray-100 text-gray-600 font-normal"
                  >
                    {{ item.account_breakdown.length }} 账号
                  </span>
                </div>
                <!-- Reasoning / Cache Pills -->
                <div
                  v-if="(item.reasoning_tokens || 0) > 0 || (item.cached_tokens || 0) > 0"
                  class="flex items-center gap-2 mt-1 text-[10px] font-normal"
                >
                  <span
                    v-if="(item.reasoning_tokens || 0) > 0"
                    class="text-purple-700 bg-purple-50 px-1.5 py-0.2 rounded flex items-center gap-0.5"
                  >
                    <Brain class="w-2.5 h-2.5" />
                    <span>思考: {{ item.reasoning_tokens?.toLocaleString() }}</span>
                  </span>
                  <span
                    v-if="(item.cached_tokens || 0) > 0"
                    class="text-teal-700 bg-teal-50 px-1.5 py-0.2 rounded flex items-center gap-0.5"
                  >
                    <Database class="w-2.5 h-2.5" />
                    <span>缓存: {{ item.cached_tokens?.toLocaleString() }}</span>
                  </span>
                </div>
              </td>
              <td class="py-3.5 px-3 text-center font-medium text-gray-700">
                {{ (item.requests || 0).toLocaleString() }}
              </td>
              <td class="py-3.5 px-3 text-center">
                <Badge
                  variant="green"
                  size="sm"
                >
                  {{ (item.success || 0).toLocaleString() }}
                </Badge>
              </td>
              <td class="py-3.5 px-3 text-center">
                <Badge
                  :variant="(item.rate_limited || 0) > 0 ? 'red' : 'gray'"
                  size="sm"
                >
                  {{ (item.rate_limited || 0).toLocaleString() }}
                </Badge>
              </td>
              <td class="py-3.5 px-3 text-center">
                <span
                  class="font-medium"
                  :class="(item.errors || 0) > 0 ? 'text-rose-600 font-bold' : 'text-gray-400'"
                >
                  {{ (item.errors || 0).toLocaleString() }}
                </span>
              </td>
              <td class="py-3.5 px-4 text-center text-gray-600">
                <span :title="`Prompt: ${(item.prompt_tokens || 0).toLocaleString()} / Completion: ${(item.completion_tokens || 0).toLocaleString()}`">
                  {{ (item.total_tokens || 0).toLocaleString() }}
                </span>
              </td>
              <td class="py-3.5 px-6 text-right text-gray-500">
                {{ formatDate(item.last_used, { withSeconds: true }) }}
              </td>
            </tr>

            <!-- Accordion Sub-Drawer: Account Breakdown for this Model -->
            <tr
              v-if="expandedModels[item.rawName]"
              class="bg-gray-50/70 border-b border-gray-100"
            >
              <td
                colspan="8"
                class="p-4"
              >
                <div class="bg-white rounded-xl border border-gray-200/90 p-4 shadow-2xs space-y-3">
                  <div class="flex items-center justify-between text-xs font-semibold text-gray-700">
                    <span class="flex items-center gap-1.5 font-sans">
                      <Users class="w-3.5 h-3.5 text-brand-600" />
                      <span>各账号调用明细</span>
                    </span>
                  </div>

                  <div
                    v-if="item.account_breakdown && item.account_breakdown.length"
                    class="overflow-x-auto"
                  >
                    <table class="w-full text-left text-xs border-collapse">
                      <thead>
                        <tr class="text-gray-400 border-b border-gray-100 pb-2">
                          <th class="py-2 px-2">
                            子账号 / 邮箱
                          </th>
                          <th class="py-2 px-2 text-center">
                            索引
                          </th>
                          <th class="py-2 px-2 text-center">
                            分组
                          </th>
                          <th class="py-2 px-2 text-center">
                            订阅
                          </th>
                          <th class="py-2 px-2 text-center">
                            今日请求
                          </th>
                          <th class="py-2 px-2 text-center">
                            成功 / 429
                          </th>
                          <th class="py-2 px-2 text-center">
                            消耗进度
                          </th>
                          <th class="py-2 px-2 text-right">
                            状态
                          </th>
                        </tr>
                      </thead>
                      <tbody class="divide-y divide-gray-50">
                        <tr
                          v-for="acc in item.account_breakdown"
                          :key="acc.account_id"
                          class="hover:bg-gray-50/60"
                        >
                          <td class="py-2 px-2">
                            <div class="font-medium text-gray-900 truncate max-w-[180px]">
                              {{ acc.email || acc.name || acc.account_id }}
                            </div>
                            <div class="text-[10px] text-gray-400 truncate">
                              {{ acc.account_id }}
                            </div>
                          </td>
                          <td class="py-2 px-2 text-center font-bold text-gray-600">
                            u/{{ acc.auth_user }}
                          </td>
                          <td class="py-2 px-2 text-center">
                            <span class="px-1.5 py-0.2 rounded text-[10px] bg-gray-100 text-gray-600">
                              {{ acc.group }}
                            </span>
                          </td>
                          <td class="py-2 px-2 text-center">
                            <span
                              class="px-1.5 py-0.2 rounded text-[10px] font-semibold"
                              :class="[
                                acc.tier === 'Ultra' ? 'bg-purple-100 text-purple-800' :
                                acc.tier === 'Pro' ? 'bg-amber-100 text-amber-800' :
                                acc.tier === 'Plus' ? 'bg-emerald-100 text-emerald-800' :
                                'bg-gray-100 text-gray-600',
                              ]"
                            >
                              {{ acc.tier }}
                            </span>
                          </td>
                          <td class="py-2 px-2 text-center font-bold text-gray-900">
                            {{ acc.today_requests }}
                          </td>
                          <td class="py-2 px-2 text-center">
                            <span class="text-emerald-600 font-semibold">{{ acc.today_success }}</span>
                            <span class="text-gray-300 mx-1">/</span>
                            <span :class="acc.today_rate_limited > 0 ? 'text-rose-600 font-bold' : 'text-gray-400'">
                              {{ acc.today_rate_limited }}
                            </span>
                          </td>
                          <td class="py-2 px-2 text-center">
                            <div class="w-28 mx-auto space-y-0.5">
                              <div class="flex justify-between text-[10px] text-gray-500">
                                <span>{{ acc.today_requests }}/{{ acc.configured_quota }}次</span>
                                <span>{{ Math.round(acc.quota_used_percent) }}%</span>
                              </div>
                              <div
                                v-if="acc.configured_token_quota"
                                class="flex justify-between text-[9px] text-gray-400"
                              >
                                <span>{{ formatTokenCount(acc.today_tokens) }}/{{ formatTokenCount(acc.configured_token_quota) }}</span>
                                <span>{{ Math.round(acc.token_quota_used_percent || 0) }}%</span>
                              </div>
                              <div class="w-full bg-gray-100 rounded-full h-1 overflow-hidden">
                                <div
                                  class="h-1 rounded-full"
                                  :class="acc.quota_used_percent >= 100 ? 'bg-amber-500' : acc.quota_used_percent >= 80 ? 'bg-yellow-500' : 'bg-brand-500'"
                                  :style="{ width: `${Math.min(100, Math.round(acc.quota_used_percent))}%` }"
                                />
                              </div>
                            </div>
                          </td>
                          <td class="py-2 px-2 text-right">
                            <span
                              v-if="!acc.enabled"
                              class="text-[10px] text-gray-400"
                            >
                              停用
                            </span>
                            <span
                              v-else-if="acc.cooldown_remaining > 0"
                              class="px-1.5 py-0.2 rounded text-[10px] bg-rose-50 text-rose-700 font-medium"
                            >
                              429 ({{ acc.cooldown_remaining }}s)
                            </span>
                            <span
                              v-else-if="acc.is_available"
                              class="px-1.5 py-0.2 rounded text-[10px] bg-emerald-50 text-emerald-700 font-medium"
                            >
                              可用
                            </span>
                            <span
                              v-else
                              class="text-[10px] text-amber-600"
                            >
                              配额耗尽
                            </span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div
                    v-else
                    class="text-xs text-gray-400 italic py-2 text-center"
                  >
                    今日尚无账号调用此模型
                  </div>
                </div>
              </td>
            </tr>
          </template>

          <tr v-if="!statList.length">
            <td
              colspan="8"
              class="py-12 text-center text-gray-400 text-xs"
            >
              无调用记录
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
