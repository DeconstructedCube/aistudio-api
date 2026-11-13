<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type {
  TierQuotasConfig,
  TierQuotaTierConfig,
  TierQuotaRule,
  TierQuotaOverrideValue,
  TierQuotaOverrideObject,
  MatchRules,
} from './types.ts'
import ConfigTagList from './ConfigTagList.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import Button from '@/components/ui/Button.vue'
import { formatTokenCount } from '@/utils/format.ts'
import {
  Gauge,
  Plus,
  Trash2,
  ArrowUp,
  ArrowDown,
  Copy,
  Layers,
  Sparkles,
  Table2,
  ChevronDown,
  ChevronRight,
} from 'lucide-vue-next'

const props = defineProps<{
  tierQuotas: TierQuotasConfig
  filterKeyword?: string
}>()

const emit = defineEmits<{
  'update:tierQuotas': [val: TierQuotasConfig]
}>()

const STANDARD_TIERS = ['Free', 'Plus', 'Pro', 'Ultra', 'Ultra_20X']

const TOKEN_PRESETS = [
  { label: '500万', value: 5_000_000 },
  { label: '1000万', value: 10_000_000 },
  { label: '1300万', value: 13_000_000 },
  { label: '1500万', value: 15_000_000 },
  { label: '2500万', value: 25_000_000 },
  { label: '不限', value: null },
]

const tierNames = computed(() => {
  const keys = Object.keys(props.tierQuotas || {}).filter((k) => k !== 'overrides')
  return keys.length ? keys : ['Free', 'Plus', 'Pro', 'Ultra', 'Ultra_20X']
})

const selectedTier = ref<string>(tierNames.value[0] || 'Free')
const showMatrix = ref(true)
const newTierName = ref('')
const newRuleName = ref('')
const newOverrideModel = ref('')
const copyTargetTier = ref('')

watch(
  tierNames,
  (list) => {
    if (!list.includes(selectedTier.value)) {
      selectedTier.value = list[0] || 'Free'
    }
  },
  { immediate: true },
)

const currentTierData = computed<TierQuotaTierConfig>(() => {
  const raw = props.tierQuotas?.[selectedTier.value]
  if (raw && typeof raw === 'object') {
    return raw as TierQuotaTierConfig
  }
  return { default_limit: 20 }
})

interface RuleEntry {
  key: string
  rule: TierQuotaRule
}

const currentTierRules = computed<RuleEntry[]>(() => {
  const td = currentTierData.value
  const kw = (props.filterKeyword || '').trim().toLowerCase()
  const list: RuleEntry[] = []
  for (const [k, v] of Object.entries(td)) {
    if (k === 'default_limit' || k === 'default_token_limit') continue
    if (v && typeof v === 'object') {
      const rule = v as TierQuotaRule
      if (kw) {
        const inKey = k.toLowerCase().includes(kw)
        const inContains = rule.match?.contains?.some((c) => c.toLowerCase().includes(kw))
        const inPrefix = rule.match?.prefixes?.some((p) => p.toLowerCase().includes(kw))
        const inExact = rule.match?.exact?.some((e) => e.toLowerCase().includes(kw))
        if (!inKey && !inContains && !inPrefix && !inExact) continue
      }
      list.push({ key: k, rule })
    }
  }
  return list
})

const overridesMap = computed<Record<string, TierQuotaOverrideValue>>(() => {
  const raw = props.tierQuotas?.overrides
  if (raw && typeof raw === 'object') {
    return raw
  }
  return {}
})

const filteredOverrides = computed<[string, TierQuotaOverrideValue][]>(() => {
  const kw = (props.filterKeyword || '').trim().toLowerCase()
  return Object.entries(overridesMap.value).filter(([m]) => !kw || m.toLowerCase().includes(kw))
})

function cloneQuotas(): TierQuotasConfig {
  return JSON.parse(JSON.stringify(props.tierQuotas || {})) as TierQuotasConfig
}

function getTierRuleEntries(tierName: string): RuleEntry[] {
  const raw = props.tierQuotas?.[tierName]
  if (!raw || typeof raw !== 'object') return []
  const list: RuleEntry[] = []
  for (const [k, v] of Object.entries(raw as TierQuotaTierConfig)) {
    if (k === 'default_limit' || k === 'default_token_limit') continue
    if (v && typeof v === 'object') {
      list.push({ key: k, rule: v as TierQuotaRule })
    }
  }
  return list
}

function getTierDefaultLimit(tierName: string): number {
  const raw = props.tierQuotas?.[tierName]
  if (raw && typeof raw === 'object') {
    return Number((raw as TierQuotaTierConfig).default_limit ?? 20)
  }
  return 20
}

function updateTierDefaultLimit(field: 'default_limit' | 'default_token_limit', rawVal: string) {
  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || {}) as TierQuotaTierConfig
  if (rawVal.trim() === '') {
    if (field === 'default_limit') {
      tierObj.default_limit = 20
    } else {
      delete tierObj.default_token_limit
    }
  } else {
    const parsed = Math.max(1, Math.floor(Number(rawVal)))
    if (!Number.isNaN(parsed)) {
      tierObj[field] = parsed
    }
  }
  next[selectedTier.value] = tierObj
  emit('update:tierQuotas', next)
}

function handleAddTier() {
  const name = newTierName.value.trim()
  if (!name || name === 'overrides') return
  const next = cloneQuotas()
  if (!next[name]) {
    next[name] = {
      default_limit: 50,
    }
    emit('update:tierQuotas', next)
  }
  selectedTier.value = name
  newTierName.value = ''
}

function handleDeleteTier(tierName: string) {
  if (tierNames.value.length <= 1) return
  if (!confirm(`确定要删除订阅层级 "${tierName}" 的额度配置吗？`)) return
  const next = cloneQuotas()
  delete next[tierName]
  emit('update:tierQuotas', next)
}

function handleCopyTierTo() {
  const target = copyTargetTier.value.trim()
  if (!target || target === selectedTier.value || target === 'overrides') return
  const next = cloneQuotas()
  next[target] = JSON.parse(JSON.stringify(currentTierData.value)) as TierQuotaTierConfig
  emit('update:tierQuotas', next)
  selectedTier.value = target
  copyTargetTier.value = ''
}

function handleAddRule() {
  const rawName = newRuleName.value.trim().replace(/\s+/g, '_')
  const ruleKey = rawName || `custom_rule_${currentTierRules.value.length + 1}`
  if (ruleKey === 'default_limit' || ruleKey === 'default_token_limit') return

  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || { default_limit: 50 }) as TierQuotaTierConfig
  if (!tierObj[ruleKey]) {
    tierObj[ruleKey] = {
      match: { contains: [] },
      daily_limit: Number(tierObj.default_limit ?? 50),
    }
    next[selectedTier.value] = tierObj
    emit('update:tierQuotas', next)
  }
  newRuleName.value = ''
}

function handleRenameRule(oldKey: string, newKeyRaw: string) {
  const newKey = newKeyRaw.trim().replace(/\s+/g, '_')
  if (!newKey || newKey === oldKey || newKey === 'default_limit' || newKey === 'default_token_limit') return
  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || {}) as TierQuotaTierConfig
  if (tierObj[newKey]) return

  const rebuilt: TierQuotaTierConfig = {}
  for (const [k, v] of Object.entries(tierObj)) {
    if (k === oldKey) {
      rebuilt[newKey] = v
    } else {
      rebuilt[k] = v
    }
  }
  next[selectedTier.value] = rebuilt
  emit('update:tierQuotas', next)
}

function handleDeleteRule(ruleKey: string) {
  if (!confirm(`确定要删除规则 "${ruleKey}" 吗？`)) return
  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || {}) as TierQuotaTierConfig
  delete tierObj[ruleKey]
  next[selectedTier.value] = tierObj
  emit('update:tierQuotas', next)
}

function handleMoveRule(ruleKey: string, direction: -1 | 1) {
  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || {}) as TierQuotaTierConfig
  const ruleKeys = Object.keys(tierObj).filter(
    (k) => k !== 'default_limit' && k !== 'default_token_limit' && typeof tierObj[k] === 'object',
  )
  const idx = ruleKeys.indexOf(ruleKey)
  const targetIdx = idx + direction
  if (idx < 0 || targetIdx < 0 || targetIdx >= ruleKeys.length) return

  const temp = ruleKeys[idx]
  ruleKeys[idx] = ruleKeys[targetIdx]
  ruleKeys[targetIdx] = temp

  const rebuilt: TierQuotaTierConfig = {}
  for (const rk of ruleKeys) {
    rebuilt[rk] = tierObj[rk]
  }
  if (tierObj.default_limit !== undefined) rebuilt.default_limit = tierObj.default_limit
  if (tierObj.default_token_limit !== undefined) rebuilt.default_token_limit = tierObj.default_token_limit
  next[selectedTier.value] = rebuilt
  emit('update:tierQuotas', next)
}

function updateRuleMatch(ruleKey: string, field: keyof MatchRules, tags: string[]) {
  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || {}) as TierQuotaTierConfig
  const rule = (tierObj[ruleKey] || { daily_limit: 50 }) as TierQuotaRule
  const nextMatch: MatchRules = { ...(rule.match || {}) }
  if (tags.length > 0) {
    nextMatch[field] = tags
  } else {
    delete nextMatch[field]
  }
  rule.match = nextMatch
  tierObj[ruleKey] = rule
  next[selectedTier.value] = tierObj
  emit('update:tierQuotas', next)
}

function updateRuleDailyLimit(ruleKey: string, rawVal: string) {
  const parsed = Math.max(1, Math.floor(Number(rawVal)))
  if (Number.isNaN(parsed)) return
  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || {}) as TierQuotaTierConfig
  const rule = (tierObj[ruleKey] || { daily_limit: 50 }) as TierQuotaRule
  rule.daily_limit = parsed
  tierObj[ruleKey] = rule
  next[selectedTier.value] = tierObj
  emit('update:tierQuotas', next)
}

function updateRuleTokenLimit(ruleKey: string, val: number | null | string) {
  const next = cloneQuotas()
  const tierObj = (next[selectedTier.value] || {}) as TierQuotaTierConfig
  const rule = (tierObj[ruleKey] || { daily_limit: 50 }) as TierQuotaRule
  if (val === null || val === '') {
    delete rule.daily_token_limit
  } else {
    const parsed = Math.max(1, Math.floor(Number(val)))
    if (!Number.isNaN(parsed)) {
      rule.daily_token_limit = parsed
    }
  }
  tierObj[ruleKey] = rule
  next[selectedTier.value] = tierObj
  emit('update:tierQuotas', next)
}

function normalizeOverride(val: TierQuotaOverrideValue): TierQuotaOverrideObject {
  if (typeof val === 'number') {
    return { daily_limit: val }
  }
  return { ...val }
}

function handleAddOverride() {
  const modelId = newOverrideModel.value.trim().toLowerCase().replace(/^models\//, '')
  if (!modelId) return
  const next = cloneQuotas()
  const ov = (next.overrides && typeof next.overrides === 'object' ? next.overrides : {}) as Record<
    string,
    TierQuotaOverrideValue
  >
  if (!(modelId in ov)) {
    ov[modelId] = { daily_limit: 50 }
  }
  next.overrides = ov
  emit('update:tierQuotas', next)
  newOverrideModel.value = ''
}

function updateOverrideField(modelId: string, field: 'daily_limit' | 'daily_token_limit', rawVal: string | number | null) {
  const next = cloneQuotas()
  const ov = (next.overrides && typeof next.overrides === 'object' ? next.overrides : {}) as Record<
    string,
    TierQuotaOverrideValue
  >
  const obj = normalizeOverride(ov[modelId] ?? { daily_limit: 50 })
  if (rawVal === null || rawVal === '') {
    if (field === 'daily_limit') {
      delete obj.daily_limit
    } else {
      delete obj.daily_token_limit
    }
  } else {
    const parsed = Math.max(1, Math.floor(Number(rawVal)))
    if (!Number.isNaN(parsed)) {
      obj[field] = parsed
    }
  }
  // Keep YAML concise if only daily_limit is present
  if (obj.daily_limit !== undefined && obj.daily_token_limit === undefined) {
    ov[modelId] = obj.daily_limit
  } else {
    ov[modelId] = obj
  }
  next.overrides = ov
  emit('update:tierQuotas', next)
}

function handleDeleteOverride(modelId: string) {
  const next = cloneQuotas()
  if (next.overrides && typeof next.overrides === 'object') {
    delete next.overrides[modelId]
  }
  emit('update:tierQuotas', next)
}
</script>

<template>
  <div class="space-y-6">
    <!-- Overview Matrix (Collapsible) -->
    <div class="border border-gray-200/80 rounded-2xl overflow-hidden bg-gray-50/50">
      <button
        type="button"
        class="w-full px-4 py-3 flex items-center justify-between text-xs font-bold text-gray-800 hover:bg-gray-100/70 transition-colors cursor-pointer"
        @click="showMatrix = !showMatrix"
      >
        <span class="flex items-center gap-2">
          <Table2 class="w-4 h-4 text-brand-600" />
          <span>全订阅层级每日额度总览（次数 / Token 任一耗尽即触发调度分流）</span>
        </span>
        <component
          :is="showMatrix ? ChevronDown : ChevronRight"
          class="w-4 h-4 text-gray-400"
        />
      </button>

      <div
        v-if="showMatrix"
        class="border-t border-gray-200/70 overflow-x-auto bg-white"
      >
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-gray-50/80 border-b border-gray-100 text-[11px] font-semibold text-gray-500">
              <th class="py-2.5 px-3">
                订阅层级
              </th>
              <th class="py-2.5 px-3">
                已配置模型规则（按优先级顺序）
              </th>
              <th class="py-2.5 px-3 text-right">
                兜底默认
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100 font-mono text-[11px]">
            <tr
              v-for="tName in tierNames"
              :key="tName"
              class="hover:bg-brand-50/30 cursor-pointer transition-colors"
              :class="{ 'bg-brand-50/50': selectedTier === tName }"
              @click="selectedTier = tName"
            >
              <td class="py-2.5 px-3 font-bold text-gray-900 whitespace-nowrap">
                <span
                  class="px-2 py-0.5 rounded-full text-[11px] border"
                  :class="[
                    tName.startsWith('Ultra') ? 'bg-purple-50 text-purple-800 border-purple-200' :
                    tName === 'Pro' ? 'bg-amber-50 text-amber-800 border-amber-200' :
                    tName === 'Plus' ? 'bg-emerald-50 text-emerald-800 border-emerald-200' :
                    'bg-gray-100 text-gray-700 border-gray-200',
                  ]"
                >
                  {{ tName }}
                </span>
              </td>
              <td class="py-2.5 px-3">
                <div class="flex flex-wrap gap-1.5">
                  <span
                    v-for="entry in getTierRuleEntries(tName)"
                    :key="entry.key"
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg bg-gray-50 border border-gray-200 text-gray-700"
                  >
                    <span class="font-semibold text-gray-900">{{ entry.key }}:</span>
                    <span>{{ entry.rule.daily_limit }}次</span>
                    <span
                      v-if="entry.rule.daily_token_limit"
                      class="text-brand-700"
                    >/ {{ formatTokenCount(entry.rule.daily_token_limit) }}</span>
                  </span>
                  <span
                    v-if="!getTierRuleEntries(tName).length"
                    class="text-gray-400 italic"
                  >
                    仅使用兜底额度
                  </span>
                </div>
              </td>
              <td class="py-2.5 px-3 text-right text-gray-600 whitespace-nowrap">
                {{ getTierDefaultLimit(tName) }} 次
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Tier Selector Bar + Add/Copy Controls -->
    <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-1.5 flex-wrap">
        <button
          v-for="tName in tierNames"
          :key="tName"
          type="button"
          class="px-3 py-1.5 rounded-xl text-xs font-mono font-semibold transition-all cursor-pointer border"
          :class="[
            selectedTier === tName
              ? 'bg-brand-600 text-white border-brand-600 shadow-xs'
              : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100',
          ]"
          @click="selectedTier = tName"
        >
          {{ tName }}
        </button>
      </div>

      <div class="flex items-center gap-2 flex-wrap">
        <!-- Copy current tier config to another tier -->
        <div class="flex items-center gap-1">
          <select
            v-model="copyTargetTier"
            class="px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:border-brand-500"
          >
            <option value="">
              复制当前配置到...
            </option>
            <option
              v-for="tOpt in [...new Set([...STANDARD_TIERS, ...tierNames])].filter((t) => t !== selectedTier)"
              :key="tOpt"
              :value="tOpt"
            >
              {{ tOpt }}
            </option>
          </select>
          <Button
            v-if="copyTargetTier"
            variant="secondary"
            size="sm"
            @click="handleCopyTierTo"
          >
            <Copy class="w-3.5 h-3.5" />
            <span>覆盖复制</span>
          </Button>
        </div>

        <!-- Add Custom Tier -->
        <div class="flex items-center gap-1">
          <input
            v-model="newTierName"
            type="text"
            placeholder="新增层级名..."
            class="w-28 px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
            @keydown.enter.prevent="handleAddTier"
          >
          <Button
            variant="secondary"
            size="sm"
            :disabled="!newTierName.trim()"
            @click="handleAddTier"
          >
            <Plus class="w-3.5 h-3.5" />
            <span>新增层级</span>
          </Button>
        </div>

        <button
          v-if="!STANDARD_TIERS.includes(selectedTier) && tierNames.length > 1"
          type="button"
          class="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
          title="删除当前自定义层级"
          @click="handleDeleteTier(selectedTier)"
        >
          <Trash2 class="w-4 h-4" />
        </button>
      </div>
    </div>

    <!-- Selected Tier Defaults & Rules -->
    <div class="space-y-4">
      <!-- Tier Default Limits -->
      <div class="p-4 bg-gray-50/70 border border-gray-200/80 rounded-2xl grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-xs font-bold text-gray-800 flex items-center gap-1.5">
              <Gauge class="w-3.5 h-3.5 text-brand-600" />
              <span>{{ selectedTier }} 兜底默认请求额度 (default_limit)</span>
            </label>
            <FieldHelpTip schema-key="quota.default_limit" />
          </div>
          <div class="flex items-center gap-2">
            <input
              type="number"
              min="1"
              :value="currentTierData.default_limit ?? 20"
              class="w-full px-3 py-2 bg-white border border-gray-200 rounded-xl text-xs font-mono outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
              @input="updateTierDefaultLimit('default_limit', ($event.target as HTMLInputElement).value)"
            >
            <span class="text-xs text-gray-500 shrink-0">次/日</span>
          </div>
        </div>

        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-xs font-bold text-gray-800 flex items-center gap-1.5">
              <Sparkles class="w-3.5 h-3.5 text-brand-600" />
              <span>{{ selectedTier }} 兜底默认 Token 上限 (可选)</span>
            </label>
            <FieldHelpTip schema-key="quota.daily_token_limit" />
          </div>
          <div class="flex items-center gap-2">
            <input
              type="number"
              min="1"
              :value="currentTierData.default_token_limit ?? ''"
              placeholder="留空表示不限制 Token"
              class="w-full px-3 py-2 bg-white border border-gray-200 rounded-xl text-xs font-mono outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
              @input="updateTierDefaultLimit('default_token_limit', ($event.target as HTMLInputElement).value)"
            >
            <span class="text-xs text-gray-500 shrink-0 font-mono">
              {{ currentTierData.default_token_limit ? formatTokenCount(currentTierData.default_token_limit) : '不限' }}
            </span>
          </div>
        </div>
      </div>

      <!-- Rules Header + Add Rule -->
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h4 class="text-xs font-bold text-gray-900 flex items-center gap-2">
            <Layers class="w-4 h-4 text-brand-600" />
            <span>{{ selectedTier }} 模型分类额度规则（自上而下优先匹配）</span>
          </h4>
          <p class="text-[11px] text-gray-400 mt-0.5">
            提示：更具体的规则（如 image_models / hydrogen_models）应排在泛匹配（如 flash_models / pro_models）之前
          </p>
        </div>

        <div class="flex items-center gap-2">
          <input
            v-model="newRuleName"
            type="text"
            placeholder="规则键名，如 hydrogen_models"
            class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
            @keydown.enter.prevent="handleAddRule"
          >
          <Button
            variant="secondary"
            size="sm"
            @click="handleAddRule"
          >
            <Plus class="w-3.5 h-3.5" />
            <span>添加规则</span>
          </Button>
        </div>
      </div>

      <!-- Rule Cards -->
      <div
        v-if="currentTierRules.length"
        class="space-y-3"
      >
        <div
          v-for="(entry, idx) in currentTierRules"
          :key="entry.key"
          class="bg-white border border-gray-200/90 rounded-2xl p-4 shadow-2xs space-y-4"
        >
          <!-- Rule Header -->
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 pb-3">
            <div class="flex items-center gap-2">
              <span class="w-5 h-5 rounded-md bg-gray-100 text-gray-700 font-mono text-[11px] font-bold flex items-center justify-center">
                {{ idx + 1 }}
              </span>
              <input
                type="text"
                :value="entry.key"
                class="px-2.5 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono font-bold text-gray-900 outline-none focus:bg-white focus:border-brand-500"
                title="修改规则名称"
                @change="handleRenameRule(entry.key, ($event.target as HTMLInputElement).value)"
              >
              <span class="px-2 py-0.5 rounded-full text-[11px] font-mono bg-brand-50 text-brand-700 border border-brand-200/60">
                {{ entry.rule.daily_limit }} 次{{ entry.rule.daily_token_limit ? ` / ${formatTokenCount(entry.rule.daily_token_limit)}` : '' }}
              </span>
            </div>

            <div class="flex items-center gap-1">
              <button
                type="button"
                :disabled="idx === 0"
                class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                title="上移提高匹配优先级"
                @click="handleMoveRule(entry.key, -1)"
              >
                <ArrowUp class="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                :disabled="idx === currentTierRules.length - 1"
                class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                title="下移降低匹配优先级"
                @click="handleMoveRule(entry.key, 1)"
              >
                <ArrowDown class="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                class="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
                title="删除此额度规则"
                @click="handleDeleteRule(entry.key)"
              >
                <Trash2 class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <!-- Quota Inputs -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="space-y-1.5">
              <div class="flex items-center justify-between">
                <label class="text-xs font-semibold text-gray-700">每日请求次数上限 (daily_limit)</label>
                <FieldHelpTip schema-key="quota.daily_limit" />
              </div>
              <div class="flex items-center gap-2">
                <input
                  type="number"
                  min="1"
                  :value="entry.rule.daily_limit"
                  class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                  @input="updateRuleDailyLimit(entry.key, ($event.target as HTMLInputElement).value)"
                >
                <span class="text-xs text-gray-500 shrink-0">次/日</span>
              </div>
            </div>

            <div class="space-y-1.5">
              <div class="flex items-center justify-between">
                <label class="text-xs font-semibold text-gray-700">每日 Token 上限 (daily_token_limit)</label>
                <FieldHelpTip schema-key="quota.daily_token_limit" />
              </div>
              <div class="flex items-center gap-2">
                <input
                  type="number"
                  min="1"
                  :value="entry.rule.daily_token_limit ?? ''"
                  placeholder="留空表示不限制 Token"
                  class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                  @input="updateRuleTokenLimit(entry.key, ($event.target as HTMLInputElement).value)"
                >
                <span class="text-xs text-gray-500 shrink-0 font-mono min-w-[44px] text-right">
                  {{ entry.rule.daily_token_limit ? formatTokenCount(entry.rule.daily_token_limit) : '不限' }}
                </span>
              </div>
              <!-- Token Quick Presets -->
              <div class="flex items-center gap-1 flex-wrap pt-0.5">
                <button
                  v-for="preset in TOKEN_PRESETS"
                  :key="preset.label"
                  type="button"
                  class="px-2 py-0.5 text-[10px] font-mono rounded-md border transition-colors cursor-pointer"
                  :class="(entry.rule.daily_token_limit ?? null) === preset.value ? 'bg-brand-50 text-brand-700 border-brand-300 font-semibold' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'"
                  @click="updateRuleTokenLimit(entry.key, preset.value)"
                >
                  {{ preset.label }}
                </button>
              </div>
            </div>
          </div>

          <!-- Match Conditions -->
          <div class="p-3 bg-gray-50/70 border border-gray-200/70 rounded-xl grid grid-cols-1 md:grid-cols-3 gap-3">
            <ConfigTagList
              :model-value="entry.rule.match?.contains || []"
              label="包含关键词 (contains)"
              placeholder="如 hydrogen, flash"
              @update:model-value="updateRuleMatch(entry.key, 'contains', $event as string[])"
            />
            <ConfigTagList
              :model-value="entry.rule.match?.prefixes || []"
              label="前缀匹配 (prefixes)"
              placeholder="如 gemini-3.1-"
              @update:model-value="updateRuleMatch(entry.key, 'prefixes', $event as string[])"
            />
            <ConfigTagList
              :model-value="entry.rule.match?.exact || []"
              label="精确匹配 (exact)"
              placeholder="如 gemini-4-argon"
              @update:model-value="updateRuleMatch(entry.key, 'exact', $event as string[])"
            />
          </div>
        </div>
      </div>

      <div
        v-else
        class="p-6 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-2xl"
      >
        当前层级暂无模型分类规则，全部模型将使用兜底默认额度。
      </div>
    </div>

    <!-- Exact Model Overrides Section -->
    <div class="pt-2 border-t border-gray-100 space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <h4 class="text-xs font-bold text-gray-900">
            单模型精确额度覆盖 (tier_quotas.overrides)
          </h4>
          <FieldHelpTip schema-key="quota.overrides" />
        </div>

        <div class="flex items-center gap-2">
          <input
            v-model="newOverrideModel"
            type="text"
            placeholder="例如: gemini-3.1-pro-preview"
            class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
            @keydown.enter.prevent="handleAddOverride"
          >
          <Button
            variant="primary"
            size="sm"
            :disabled="!newOverrideModel.trim()"
            @click="handleAddOverride"
          >
            <Plus class="w-3.5 h-3.5" />
            <span>添加覆盖</span>
          </Button>
        </div>
      </div>

      <div
        v-if="filteredOverrides.length"
        class="grid grid-cols-1 md:grid-cols-2 gap-3"
      >
        <div
          v-for="[modelId, ovVal] in filteredOverrides"
          :key="modelId"
          class="p-3.5 bg-white border border-gray-200/90 rounded-xl space-y-3 shadow-2xs"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="font-mono text-xs font-bold text-brand-700 bg-brand-50 px-2 py-0.5 rounded border border-brand-200/60 truncate">
              {{ modelId }}
            </span>
            <button
              type="button"
              class="p-1 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
              title="删除此覆盖"
              @click="handleDeleteOverride(modelId)"
            >
              <Trash2 class="w-3.5 h-3.5" />
            </button>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="space-y-1">
              <label class="text-[11px] font-semibold text-gray-600">每日次数</label>
              <input
                type="number"
                min="1"
                :value="normalizeOverride(ovVal).daily_limit ?? ''"
                placeholder="继承层级"
                class="w-full px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
                @input="updateOverrideField(modelId, 'daily_limit', ($event.target as HTMLInputElement).value)"
              >
            </div>
            <div class="space-y-1">
              <label class="text-[11px] font-semibold text-gray-600">
                每日 Token ({{ normalizeOverride(ovVal).daily_token_limit ? formatTokenCount(normalizeOverride(ovVal).daily_token_limit) : '继承/不限' }})
              </label>
              <input
                type="number"
                min="1"
                :value="normalizeOverride(ovVal).daily_token_limit ?? ''"
                placeholder="留空不限"
                class="w-full px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs font-mono outline-none focus:bg-white focus:border-brand-500"
                @input="updateOverrideField(modelId, 'daily_token_limit', ($event.target as HTMLInputElement).value)"
              >
            </div>
          </div>
        </div>
      </div>

      <div
        v-else
        class="p-6 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-2xl bg-gray-50/40"
      >
        暂无单模型跨层级额度覆盖，全部模型按上方各订阅层级规则匹配。
      </div>
    </div>
  </div>
</template>
