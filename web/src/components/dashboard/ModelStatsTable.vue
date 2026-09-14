<script setup lang="ts">
import { computed } from 'vue'
import type { ModelStatItem } from '@/types'
import Badge from '@/components/ui/Badge.vue'
import { Layers } from 'lucide-vue-next'

const props = defineProps<{
  stats: Record<string, ModelStatItem>
  loading?: boolean
}>()

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return dateStr
  }
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
          模型调用统计 (各模型限额相互独立)
        </h3>
      </div>
      <span class="text-xs text-gray-400 font-mono">
        共 {{ statList.length }} 个模型活跃记录
      </span>
    </div>

    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse text-sm">
        <thead>
          <tr class="bg-gray-50/70 border-b border-gray-100 text-xs font-semibold text-gray-500 uppercase tracking-wider">
            <th class="py-3 px-6">
              模型标识
            </th>
            <th class="py-3 px-4 text-center">
              总请求
            </th>
            <th class="py-3 px-4 text-center">
              成功
            </th>
            <th class="py-3 px-4 text-center">
              429 限流
            </th>
            <th class="py-3 px-4 text-center">
              异常错误
            </th>
            <th class="py-3 px-6 text-right">
              最后调用时间
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr
            v-for="item in statList"
            :key="item.rawName"
            class="hover:bg-gray-50/60 transition-colors"
          >
            <td class="py-3.5 px-6 font-medium text-gray-900 font-mono text-xs">
              {{ item.name }}
            </td>
            <td class="py-3.5 px-4 text-center font-medium text-gray-700">
              {{ item.requests || 0 }}
            </td>
            <td class="py-3.5 px-4 text-center">
              <Badge
                variant="green"
                size="sm"
              >
                {{ item.success || 0 }}
              </Badge>
            </td>
            <td class="py-3.5 px-4 text-center">
              <Badge
                :variant="(item.rate_limited || 0) > 0 ? 'red' : 'gray'"
                size="sm"
              >
                {{ item.rate_limited || 0 }}
              </Badge>
            </td>
            <td class="py-3.5 px-4 text-center">
              <span
                class="text-xs font-medium"
                :class="(item.errors || 0) > 0 ? 'text-rose-600 font-bold' : 'text-gray-400'"
              >
                {{ item.errors || 0 }}
              </span>
            </td>
            <td class="py-3.5 px-6 text-right text-xs text-gray-500 font-mono">
              {{ formatDate(item.last_used) }}
            </td>
          </tr>

          <tr v-if="!statList.length">
            <td
              colspan="6"
              class="py-12 text-center text-gray-400 text-xs"
            >
              暂无模型调用数据
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
