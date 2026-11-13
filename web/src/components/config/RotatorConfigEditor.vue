<script setup lang="ts">
import { ref } from 'vue'
import type { RotatorConfig } from './types.ts'
import ConfigSelect from './ConfigSelect.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import Button from '@/components/ui/Button.vue'
import { Repeat, Shield, Timer, Plus, Trash2 } from 'lucide-vue-next'

const props = defineProps<{
  rotator: RotatorConfig
}>()

const emit = defineEmits<{
  'update:rotator': [val: RotatorConfig]
}>()

const STRATEGY_OPTIONS = [
  { value: 'sticky', label: '黏性优先 (sticky)', description: '优先复用当前账号以提升缓存命中' },
  { value: 'round_robin', label: '均衡轮询 (round_robin)', description: '按配额与权重成本均衡分发' },
]

const newWeightTier = ref('')

function cloneRotator(): RotatorConfig {
  return JSON.parse(JSON.stringify(props.rotator || {})) as RotatorConfig
}

function updateField<K extends keyof RotatorConfig>(key: K, value: RotatorConfig[K]) {
  const next = cloneRotator()
  next[key] = value
  emit('update:rotator', next)
}

function updateNumericField(
  key: 'max_retries' | 'stickiness_bonus' | 'stickiness_threshold' | 'drip_penalty',
  raw: string,
  opts: { isInt?: boolean; min?: number; max?: number } = {},
) {
  const val = opts.isInt ? Math.floor(Number(raw)) : Number(raw)
  if (Number.isNaN(val)) return
  let clamped = val
  if (opts.min !== undefined) clamped = Math.max(opts.min, clamped)
  if (opts.max !== undefined) clamped = Math.min(opts.max, clamped)
  updateField(key, clamped)
}

function updateTierWeight(tier: string, raw: string) {
  const val = Number(raw)
  if (Number.isNaN(val)) return
  const next = cloneRotator()
  next.tier_weights = { ...(next.tier_weights || {}), [tier]: val }
  emit('update:rotator', next)
}

function addTierWeight() {
  const t = newWeightTier.value.trim()
  if (!t) return
  const next = cloneRotator()
  const weights = { ...(next.tier_weights || {}) }
  if (!(t in weights)) {
    weights[t] = 0.0
  }
  next.tier_weights = weights
  emit('update:rotator', next)
  newWeightTier.value = ''
}

function deleteTierWeight(tier: string) {
  const next = cloneRotator()
  if (next.tier_weights) {
    delete next.tier_weights[tier]
  }
  emit('update:rotator', next)
}

function updateCooldown(key: 'rate_limited_short' | 'rate_limited_drip' | 'rate_limited_drip_extended', raw: string) {
  const val = Math.max(0, Math.floor(Number(raw)))
  if (Number.isNaN(val)) return
  const next = cloneRotator()
  next.cooldowns = { ...(next.cooldowns || {}), [key]: val }
  emit('update:rotator', next)
}
</script>

<template>
  <div class="space-y-6">
    <!-- Core Strategy & Thresholds -->
    <div class="p-4 bg-gray-50/70 border border-gray-200/80 rounded-2xl space-y-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-900">
          <Repeat class="w-4 h-4 text-brand-600" />
          <span>多账号调度策略与切换参数 (rotator)</span>
        </div>
        <FieldHelpTip schema-key="rotator.strategy" />
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div>
          <ConfigSelect
            :model-value="rotator.strategy || 'sticky'"
            label="调度策略 (strategy)"
            :options="STRATEGY_OPTIONS"
            @update:model-value="updateField('strategy', String($event || 'sticky'))"
          />
        </div>

        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700">
            最大故障转移重试次数 (max_retries)
          </label>
          <input
            type="number"
            min="0"
            :value="rotator.max_retries ?? 5"
            class="w-full px-3 py-2 bg-white border border-gray-200 rounded-xl text-xs font-mono outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
            @input="updateNumericField('max_retries', ($event.target as HTMLInputElement).value, { isInt: true, min: 0 })"
          >
        </div>

        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-xs font-semibold text-gray-700">
              黏性阈值 (stickiness_threshold)
            </label>
            <FieldHelpTip schema-key="rotator.stickiness_threshold" />
          </div>
          <div class="flex items-center gap-2">
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              :value="rotator.stickiness_threshold ?? 0.8"
              class="w-full px-3 py-2 bg-white border border-gray-200 rounded-xl text-xs font-mono outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
              @input="updateNumericField('stickiness_threshold', ($event.target as HTMLInputElement).value, { min: 0, max: 1 })"
            >
            <span class="text-xs font-mono text-gray-500 shrink-0">
              {{ Math.round((rotator.stickiness_threshold ?? 0.8) * 100) }}%
            </span>
          </div>
        </div>

        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700">
            黏性减分奖励 (stickiness_bonus)
          </label>
          <input
            type="number"
            step="10"
            min="0"
            :value="rotator.stickiness_bonus ?? 400"
            class="w-full px-3 py-2 bg-white border border-gray-200 rounded-xl text-xs font-mono outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
            @input="updateNumericField('stickiness_bonus', ($event.target as HTMLInputElement).value, { min: 0 })"
          >
        </div>

        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700">
            滴灌惩罚权重 (drip_penalty)
          </label>
          <input
            type="number"
            step="10"
            min="0"
            :value="rotator.drip_penalty ?? 500"
            class="w-full px-3 py-2 bg-white border border-gray-200 rounded-xl text-xs font-mono outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
            @input="updateNumericField('drip_penalty', ($event.target as HTMLInputElement).value, { min: 0 })"
          >
        </div>
      </div>
    </div>

    <!-- Tier Weights -->
    <div class="p-4 bg-white border border-gray-200/90 rounded-2xl space-y-4 shadow-2xs">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-3">
        <div class="flex items-center gap-2">
          <Shield class="w-4 h-4 text-brand-600" />
          <span class="text-xs font-bold text-gray-900">订阅等级保护权重 (tier_weights)</span>
          <FieldHelpTip schema-key="rotator.tier_weights" />
        </div>

        <div class="flex items-center gap-1.5">
          <input
            v-model="newWeightTier"
            type="text"
            placeholder="新增层级权重..."
            class="w-32 px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
            @keydown.enter.prevent="addTierWeight"
          >
          <Button
            variant="secondary"
            size="sm"
            :disabled="!newWeightTier.trim()"
            @click="addTierWeight"
          >
            <Plus class="w-3.5 h-3.5" />
            <span>添加</span>
          </Button>
        </div>
      </div>

      <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
        <div
          v-for="(weight, tierName) in rotator.tier_weights || {}"
          :key="tierName"
          class="p-3 bg-gray-50/70 border border-gray-200/80 rounded-xl space-y-1.5"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs font-mono font-bold text-gray-800">{{ tierName }}</span>
            <button
              type="button"
              class="text-gray-300 hover:text-rose-600 transition-colors cursor-pointer"
              title="移除该等级权重"
              @click="deleteTierWeight(String(tierName))"
            >
              <Trash2 class="w-3 h-3" />
            </button>
          </div>
          <input
            type="number"
            step="0.5"
            :value="weight"
            class="w-full px-2.5 py-1.5 bg-white border border-gray-200 rounded-lg text-xs font-mono outline-none focus:border-brand-500"
            @input="updateTierWeight(String(tierName), ($event.target as HTMLInputElement).value)"
          >
        </div>
      </div>
    </div>

    <!-- Cooldowns -->
    <div class="p-4 bg-white border border-gray-200/90 rounded-2xl space-y-4 shadow-2xs">
      <div class="flex items-center justify-between border-b border-gray-100 pb-3">
        <div class="flex items-center gap-2">
          <Timer class="w-4 h-4 text-brand-600" />
          <span class="text-xs font-bold text-gray-900">429 限流冷却时长 (cooldowns / 秒)</span>
        </div>
        <FieldHelpTip schema-key="rotator.cooldowns" />
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700">
            短时冷却 (rate_limited_short)
          </label>
          <div class="flex items-center gap-2">
            <input
              type="number"
              min="0"
              :value="rotator.cooldowns?.rate_limited_short ?? 60"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
              @input="updateCooldown('rate_limited_short', ($event.target as HTMLInputElement).value)"
            >
            <span class="text-xs text-gray-500 shrink-0">秒</span>
          </div>
        </div>

        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700">
            滴灌冷却 (rate_limited_drip)
          </label>
          <div class="flex items-center gap-2">
            <input
              type="number"
              min="0"
              :value="rotator.cooldowns?.rate_limited_drip ?? 300"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
              @input="updateCooldown('rate_limited_drip', ($event.target as HTMLInputElement).value)"
            >
            <span class="text-xs text-gray-500 shrink-0">秒</span>
          </div>
        </div>

        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700">
            延长滴灌 (rate_limited_drip_extended)
          </label>
          <div class="flex items-center gap-2">
            <input
              type="number"
              min="0"
              :value="rotator.cooldowns?.rate_limited_drip_extended ?? 600"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
              @input="updateCooldown('rate_limited_drip_extended', ($event.target as HTMLInputElement).value)"
            >
            <span class="text-xs text-gray-500 shrink-0">秒</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
