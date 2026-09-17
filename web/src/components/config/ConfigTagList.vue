<script setup lang="ts">
import { ref } from 'vue'
import { Plus, X } from 'lucide-vue-next'

interface Props {
  modelValue?: (string | number)[]
  label: string
  description?: string
  placeholder?: string
  suggestions?: { label: string; value: string | number; description?: string }[]
  isNumber?: boolean
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: () => [],
  description: '',
  placeholder: '输入并按回车添加...',
  suggestions: () => [],
  isNumber: false,
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [val: (string | number)[]]
}>()

const inputVal = ref('')

function addTag(valToAdd?: string | number) {
  if (props.disabled) return
  const raw = (valToAdd !== undefined ? String(valToAdd) : inputVal.value).trim()
  if (!raw) return

  const item: string | number = props.isNumber ? Number(raw) : raw
  if (props.isNumber && Number.isNaN(item)) return

  const list = [...(props.modelValue || [])]
  if (!list.includes(item)) {
    list.push(item)
    emit('update:modelValue', list)
  }
  inputVal.value = ''
}

function removeTag(index: number) {
  if (props.disabled) return
  const list = [...(props.modelValue || [])]
  list.splice(index, 1)
  emit('update:modelValue', list)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    e.preventDefault()
    addTag()
  }
}
</script>

<template>
  <div class="space-y-2">
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

    <!-- Active Tag Pills -->
    <div class="flex flex-wrap items-center gap-1.5 min-h-[32px] p-1.5 bg-gray-50 border border-gray-200 rounded-xl">
      <span
        v-for="(tag, idx) in modelValue || []"
        :key="idx"
        class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-mono font-medium bg-white text-gray-800 border border-gray-200 shadow-2xs group"
      >
        <span>{{ tag }}</span>
        <button
          v-if="!disabled"
          type="button"
          class="text-gray-400 hover:text-rose-600 rounded p-0.5 transition-colors cursor-pointer"
          @click="removeTag(idx)"
        >
          <X class="w-3 h-3" />
        </button>
      </span>

      <!-- Input box for custom tag -->
      <div
        v-if="!disabled"
        class="flex items-center gap-1 min-w-[120px] flex-1"
      >
        <input
          v-model="inputVal"
          type="text"
          :placeholder="modelValue?.length ? '+ 继续添加' : placeholder"
          class="w-full px-2 py-1 text-xs bg-transparent outline-none font-mono text-gray-700 placeholder:text-gray-400"
          @keydown="handleKeydown"
        >
        <button
          v-if="inputVal.trim()"
          type="button"
          class="px-2 py-0.5 text-xs bg-brand-50 text-brand-700 hover:bg-brand-100 rounded font-medium shrink-0 cursor-pointer"
          @click="addTag()"
        >
          添加
        </button>
      </div>

      <span
        v-else-if="!modelValue?.length"
        class="text-xs text-gray-400 px-2 py-1 italic"
      >
        未配置
      </span>
    </div>

    <!-- Quick Suggestions -->
    <div
      v-if="suggestions.length && !disabled"
      class="flex flex-wrap items-center gap-1.5 pt-0.5"
    >
      <span class="text-[10px] text-gray-400 font-semibold uppercase tracking-wider mr-1">快捷添加:</span>
      <button
        v-for="sug in suggestions"
        :key="String(sug.value)"
        type="button"
        :disabled="modelValue?.includes(sug.value)"
        class="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-mono rounded-md border transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
        :class="modelValue?.includes(sug.value) ? 'bg-gray-100 text-gray-400 border-gray-200' : 'bg-white text-gray-700 border-gray-200 hover:border-brand-300 hover:text-brand-700 hover:bg-brand-50/50'"
        :title="sug.description"
        @click="addTag(sug.value)"
      >
        <Plus
          v-if="!modelValue?.includes(sug.value)"
          class="w-3 h-3"
        />
        <span>{{ sug.label }}</span>
      </button>
    </div>
  </div>
</template>
