<script setup lang="ts">
import type { SafetySettingsMap } from './types.ts'
import { Shield, ShieldAlert, ShieldCheck } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    modelValue?: SafetySettingsMap
    disabled?: boolean
  }>(),
  {
    modelValue: () => ({}),
    disabled: false,
  }
)

const emit = defineEmits<{
  'update:modelValue': [val: SafetySettingsMap]
}>()

const categories = [
  { key: 'Harassment', label: '骚扰内容 (Harassment)', desc: '负面或恶意言语攻击' },
  { key: 'Hate', label: '仇恨言论 (Hate Speech)', desc: '针对特定群体的仇恨偏见' },
  { key: 'Sexually Explicit', label: '色情内容 (Sexually Explicit)', desc: '性暗示或露骨色情' },
  { key: 'Dangerous Content', label: '危险内容 (Dangerous Content)', desc: '促进危险行为或暴力' },
]

const levels = [
  { val: 1, label: '1 - 严苛', desc: 'BLOCK_LOW_AND_ABOVE (极易误伤)', color: 'border-rose-300 text-rose-700 bg-rose-50' },
  { val: 2, label: '2 - 较严', desc: 'BLOCK_MEDIUM_AND_ABOVE', color: 'border-amber-300 text-amber-700 bg-amber-50' },
  { val: 3, label: '3 - 中等', desc: 'BLOCK_ONLY_HIGH', color: 'border-blue-300 text-blue-700 bg-blue-50' },
  { val: 4, label: '4 - 宽松', desc: 'BLOCK_FEW (宽松过滤)', color: 'border-emerald-300 text-emerald-700 bg-emerald-50' },
  { val: 5, label: '5 - 关闭', desc: 'BLOCK_NONE (完全不拦截，推荐代理使用)', color: 'border-gray-400 text-gray-800 bg-gray-100 font-bold' },
]

function getLevel(catKey: string): number {
  return props.modelValue?.[catKey] ?? 5
}

function setLevel(catKey: string, val: number) {
  if (props.disabled) return
  const next: SafetySettingsMap = { ...(props.modelValue || {}) }
  next[catKey] = val
  emit('update:modelValue', next)
}

function setAll(val: number) {
  if (props.disabled) return
  const next: SafetySettingsMap = {}
  for (const cat of categories) {
    next[cat.key] = val
  }
  emit('update:modelValue', next)
}
</script>

<template>
  <div class="space-y-3 p-4 bg-gray-50/70 border border-gray-200/80 rounded-xl">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-gray-200/60 pb-2.5">
      <div class="flex items-center gap-2">
        <Shield class="w-4 h-4 text-brand-600" />
        <div>
          <h4 class="text-xs font-bold text-gray-900">
            安全拦截等级过滤 (Safety Settings)
          </h4>
          <p class="text-[11px] text-gray-400">
            控制 Google 对各敏感类别的过滤强度（5 = 完全关闭拦截，避免正常生成被截断）
          </p>
        </div>
      </div>

      <!-- Batch Actions -->
      <div
        v-if="!disabled"
        class="flex items-center gap-1.5"
      >
        <button
          type="button"
          class="px-2 py-1 text-[11px] font-medium bg-white text-emerald-700 border border-emerald-200 rounded-lg hover:bg-emerald-50 transition-colors flex items-center gap-1 cursor-pointer"
          @click="setAll(5)"
        >
          <ShieldAlert class="w-3 h-3 text-emerald-600" />
          <span>全设为关闭拦截 (5)</span>
        </button>
        <button
          type="button"
          class="px-2 py-1 text-[11px] font-medium bg-white text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-100 transition-colors flex items-center gap-1 cursor-pointer"
          @click="setAll(3)"
        >
          <ShieldCheck class="w-3 h-3 text-gray-500" />
          <span>全设为中等 (3)</span>
        </button>
      </div>
    </div>

    <!-- 4 Categories Grid -->
    <div class="space-y-3 pt-1">
      <div
        v-for="cat in categories"
        :key="cat.key"
        class="p-2.5 bg-white border border-gray-200/60 rounded-xl space-y-1.5 shadow-2xs"
      >
        <div class="flex items-center justify-between">
          <div>
            <span class="text-xs font-semibold text-gray-800">{{ cat.label }}</span>
            <span class="text-[11px] text-gray-400 ml-2">{{ cat.desc }}</span>
          </div>
          <span
            class="text-[11px] font-mono font-bold px-2 py-0.5 rounded"
            :class="getLevel(cat.key) === 5 ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800'"
          >
            当前: {{ getLevel(cat.key) === 5 ? '已关闭拦截 (5)' : `等级 ${getLevel(cat.key)}` }}
          </span>
        </div>

        <div class="grid grid-cols-5 gap-1.5 pt-1">
          <button
            v-for="lvl in levels"
            :key="lvl.val"
            type="button"
            :disabled="disabled"
            class="py-1.5 px-1 text-center rounded-lg border text-xs font-mono transition-all cursor-pointer select-none disabled:opacity-50"
            :class="[
              getLevel(cat.key) === lvl.val
                ? `${lvl.color} ring-2 ring-brand-500/20 shadow-xs font-bold`
                : 'bg-gray-50/80 text-gray-600 border-gray-200/80 hover:bg-gray-100',
            ]"
            :title="lvl.desc"
            @click="setLevel(cat.key, lvl.val)"
          >
            <div class="font-bold">
              {{ lvl.val }}
            </div>
            <div class="text-[9px] truncate">
              {{ lvl.val === 5 ? '关闭' : lvl.val === 1 ? '最严' : '中等' }}
            </div>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
