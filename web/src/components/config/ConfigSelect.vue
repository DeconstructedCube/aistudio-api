<script setup lang="ts">
export interface SelectOption {
  value: string | number | null
  label: string
  description?: string
  badge?: string
}

interface Props {
  modelValue: string | number | null | undefined
  label: string
  description?: string
  options: SelectOption[]
  disabled?: boolean
}

withDefaults(defineProps<Props>(), {
  description: '',
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [val: string | number | null]
}>()

function handleChange(e: Event) {
  const val = (e.target as HTMLSelectElement).value
  if (val === '__null__') {
    emit('update:modelValue', null)
  } else if (!Number.isNaN(Number(val)) && typeof val === 'string' && val.trim() !== '' && !val.includes('.')) {
    // If original option was number, cast back
    emit('update:modelValue', val)
  } else {
    emit('update:modelValue', val)
  }
}
</script>

<template>
  <div class="space-y-1.5">
    <div class="flex items-center justify-between">
      <label class="block text-xs font-semibold text-gray-700">
        {{ label }}
      </label>
      <span
        v-if="description"
        class="text-[11px] text-gray-400"
      >
        {{ description }}
      </span>
    </div>

    <div class="relative">
      <select
        :value="modelValue === null || modelValue === undefined ? '__null__' : modelValue"
        :disabled="disabled"
        class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all cursor-pointer disabled:opacity-50"
        @change="handleChange"
      >
        <option
          v-for="opt in options"
          :key="String(opt.value)"
          :value="opt.value === null ? '__null__' : opt.value"
        >
          {{ opt.label }}{{ opt.description ? ` (${opt.description})` : '' }}
        </option>
      </select>
    </div>
  </div>
</template>
