<script setup lang="ts">
import { ref, watch } from 'vue'
import type { AccountWithStats } from '@/types'
import { useAccountsStore } from '@/stores/accounts.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'

const props = defineProps<{
  modelValue: boolean
  account: AccountWithStats | null
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
}>()

const accountsStore = useAccountsStore()
const inputName = ref('')
const selectedTier = ref('Free')

const TIER_OPTIONS = [
  { value: 'Free', label: 'Free (免费版)' },
  { value: 'Plus', label: 'Plus (AI Plus)' },
  { value: 'Pro', label: 'Pro (AI Pro / TIER1)' },
  { value: 'Ultra', label: 'Ultra (AI Ultra / TIER2)' },
  { value: 'Ultra_20X', label: 'Ultra_20X (旗舰 20X)' },
]

watch(
  () => props.account,
  (acc) => {
    if (acc) {
      inputName.value = acc.name || ''
      selectedTier.value = acc.tier || 'Free'
    }
  },
  { immediate: true }
)

async function handleSave() {
  if (!props.account) return
  const trimmed = inputName.value.trim()
  if (!trimmed) return

  if (trimmed !== (props.account.name || '')) {
    await accountsStore.updateAccountName(props.account.id, trimmed)
  }
  if (selectedTier.value !== (props.account.tier || 'Free')) {
    await accountsStore.updateAccountTier(props.account.id, selectedTier.value)
  }
  emit('update:modelValue', false)
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    title="编辑账号信息"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <div class="space-y-1.5">
        <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
          账号名称 / 备注
        </label>
        <input
          v-model="inputName"
          type="text"
          placeholder="例如: 主账号 (Pro)、备用账号 1 等"
          class="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-sm outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
        >
      </div>

      <div class="space-y-1.5">
        <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
          订阅等级 (Tier)
        </label>
        <select
          v-model="selectedTier"
          class="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-sm outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
        >
          <option
            v-for="opt in TIER_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </option>
        </select>
        <p class="text-[11px] text-gray-400">
          若自动探测未能识别 Pro / Ultra 等级，可在此手动校正并立即生效。
        </p>
      </div>
    </div>

    <template #footer>
      <Button
        variant="ghost"
        size="sm"
        @click="emit('update:modelValue', false)"
      >
        取消
      </Button>
      <Button
        variant="primary"
        size="sm"
        :disabled="!inputName.trim()"
        @click="handleSave"
      >
        保存修改
      </Button>
    </template>
  </Modal>
</template>
