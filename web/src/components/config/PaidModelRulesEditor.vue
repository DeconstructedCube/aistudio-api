<script setup lang="ts">
import { ref, computed } from 'vue'
import type { PaidModelRulesConfig, PaidModelRuleItem } from './types.ts'
import ConfigTagList from './ConfigTagList.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import Button from '@/components/ui/Button.vue'
import { ShieldCheck, Plus, Trash2 } from 'lucide-vue-next'

const props = defineProps<{
  rules: PaidModelRulesConfig
  availableTiers?: string[]
}>()

const emit = defineEmits<{
  'update:rules': [val: PaidModelRulesConfig]
}>()

const DEFAULT_TIERS = ['Free', 'Plus', 'Pro', 'Ultra', 'Ultra_20X']
const newRuleKey = ref('')

const tierChoices = computed(() => {
  const set = new Set<string>(DEFAULT_TIERS)
  for (const t of props.availableTiers || []) {
    if (t && t !== 'overrides') set.add(t)
  }
  for (const rule of Object.values(props.rules || {})) {
    for (const t of rule.required_tiers || []) {
      set.add(t)
    }
  }
  return Array.from(set)
})

function cloneRules(): PaidModelRulesConfig {
  return JSON.parse(JSON.stringify(props.rules || {})) as PaidModelRulesConfig
}

function handleAddRule() {
  const key = newRuleKey.value.trim().replace(/\s+/g, '_') || `paid_rule_${Object.keys(props.rules || {}).length + 1}`
  const next = cloneRules()
  if (!next[key]) {
    next[key] = {
      contains: [],
      required_tiers: ['Pro', 'Ultra', 'Ultra_20X'],
    }
    emit('update:rules', next)
  }
  newRuleKey.value = ''
}

function handleRenameRule(oldKey: string, rawNewKey: string) {
  const newKey = rawNewKey.trim().replace(/\s+/g, '_')
  if (!newKey || newKey === oldKey) return
  const next = cloneRules()
  if (next[newKey]) return
  const rebuilt: PaidModelRulesConfig = {}
  for (const [k, v] of Object.entries(next)) {
    rebuilt[k === oldKey ? newKey : k] = v
  }
  emit('update:rules', rebuilt)
}

function handleDeleteRule(key: string) {
  if (!confirm(`确定要删除付费准入规则 "${key}" 吗？`)) return
  const next = cloneRules()
  delete next[key]
  emit('update:rules', next)
}

function updateMatchList(key: string, field: 'contains' | 'prefixes' | 'exact', tags: string[]) {
  const next = cloneRules()
  const rule = { ...(next[key] || { required_tiers: [] }) } as PaidModelRuleItem
  if (tags.length > 0) {
    rule[field] = tags
  } else {
    delete rule[field]
  }
  next[key] = rule
  emit('update:rules', next)
}

function toggleTier(key: string, tier: string) {
  const next = cloneRules()
  const rule = { ...(next[key] || { required_tiers: [] }) } as PaidModelRuleItem
  const current = new Set(rule.required_tiers || [])
  if (current.has(tier)) {
    if (current.size <= 1) return // Keep at least one tier
    current.delete(tier)
  } else {
    current.add(tier)
  }
  rule.required_tiers = tierChoices.value.filter((t) => current.has(t))
  next[key] = rule
  emit('update:rules', next)
}

function updateRequiredTiers(key: string, tiers: string[]) {
  const next = cloneRules()
  const rule = { ...(next[key] || { required_tiers: [] }) } as PaidModelRuleItem
  rule.required_tiers = tiers.length ? tiers : ['Pro']
  next[key] = rule
  emit('update:rules', next)
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div>
        <h4 class="text-xs font-bold text-gray-900 flex items-center gap-2">
          <ShieldCheck class="w-4 h-4 text-brand-600" />
          <span>付费模型准入与抢切规则 (paid_model_rules)</span>
          <FieldHelpTip schema-key="paid.required_tiers" />
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          命中规则的模型将自动限制仅调度至允许的订阅层级账号；若当前激活账号等级不足，会自动触发本地抢切或 403 快速拦截
        </p>
      </div>

      <div class="flex items-center gap-2">
        <input
          v-model="newRuleKey"
          type="text"
          placeholder="规则键名，如 ultra_required"
          class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
          @keydown.enter.prevent="handleAddRule"
        >
        <Button
          variant="secondary"
          size="sm"
          @click="handleAddRule"
        >
          <Plus class="w-3.5 h-3.5" />
          <span>添加准入规则</span>
        </Button>
      </div>
    </div>

    <div
      v-if="Object.keys(rules || {}).length"
      class="space-y-4"
    >
      <div
        v-for="(rule, ruleKey) in rules"
        :key="ruleKey"
        class="bg-white border border-gray-200/90 rounded-2xl p-4 shadow-2xs space-y-4"
      >
        <div class="flex items-center justify-between gap-2 border-b border-gray-100 pb-3">
          <div class="flex items-center gap-2">
            <input
              type="text"
              :value="ruleKey"
              class="px-2.5 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono font-bold text-gray-900 outline-none focus:bg-white focus:border-brand-500"
              @change="handleRenameRule(String(ruleKey), ($event.target as HTMLInputElement).value)"
            >
            <span class="text-[11px] text-gray-400 font-mono">
              允许层级: {{ (rule.required_tiers || []).join(', ') }}
            </span>
          </div>

          <button
            type="button"
            class="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
            title="删除此准入规则"
            @click="handleDeleteRule(String(ruleKey))"
          >
            <Trash2 class="w-4 h-4" />
          </button>
        </div>

        <!-- Required Tiers Toggle Pills + Custom Tags -->
        <div class="space-y-2">
          <label class="block text-xs font-semibold text-gray-700">
            允许调用该类模型的订阅等级 (required_tiers)
          </label>
          <div class="flex flex-wrap items-center gap-2">
            <button
              v-for="t in tierChoices"
              :key="t"
              type="button"
              class="px-3 py-1 rounded-lg text-xs font-mono font-semibold border transition-all cursor-pointer"
              :class="[
                (rule.required_tiers || []).includes(t)
                  ? 'bg-brand-600 text-white border-brand-600 shadow-2xs'
                  : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100',
              ]"
              @click="toggleTier(String(ruleKey), t)"
            >
              {{ t }}
            </button>
          </div>
          <ConfigTagList
            :model-value="rule.required_tiers || []"
            label="或直接编辑允许层级列表"
            placeholder="输入层级名按回车"
            @update:model-value="updateRequiredTiers(String(ruleKey), $event as string[])"
          />
        </div>

        <!-- Match Conditions -->
        <div class="p-3 bg-gray-50/70 border border-gray-200/70 rounded-xl grid grid-cols-1 md:grid-cols-3 gap-3">
          <ConfigTagList
            :model-value="rule.contains || []"
            label="包含关键词 (contains)"
            placeholder="如 argon, carbon, veo"
            @update:model-value="updateMatchList(String(ruleKey), 'contains', $event as string[])"
          />
          <ConfigTagList
            :model-value="rule.prefixes || []"
            label="前缀匹配 (prefixes)"
            placeholder="如 veo-, imagen-"
            @update:model-value="updateMatchList(String(ruleKey), 'prefixes', $event as string[])"
          />
          <ConfigTagList
            :model-value="rule.exact || []"
            label="精确匹配 (exact)"
            placeholder="如 gemini-3.1-pro-preview"
            @update:model-value="updateMatchList(String(ruleKey), 'exact', $event as string[])"
          />
        </div>
      </div>
    </div>

    <div
      v-else
      class="p-8 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-2xl"
    >
      未配置本地付费模型准入规则，将仅依赖上游 ListModels accessModes 判定。
    </div>
  </div>
</template>
