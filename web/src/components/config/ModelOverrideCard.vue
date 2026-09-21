<script setup lang="ts">
import { ref } from 'vue'
import type { ModelOverrideMap } from './types.ts'
import ConfigSelect from './ConfigSelect.vue'
import ConfigTagList from './ConfigTagList.vue'
import ConfigSafetyGrid from './ConfigSafetyGrid.vue'
import ConfigSwitch from './ConfigSwitch.vue'
import { Plus, Trash2, ChevronDown, ChevronRight, Layers } from 'lucide-vue-next'

type OverrideItem = NonNullable<ModelOverrideMap[string]>

const props = defineProps<{
  models: ModelOverrideMap
}>()

const emit = defineEmits<{
  'update:models': [val: ModelOverrideMap]
}>()

const newModelName = ref('')
const expandedMap = ref<Record<string, boolean>>({})

const toolSuggestions = [
  { label: '网页+图片搜索', value: 'google_search_and_image_search' },
  { label: 'Google 搜索', value: 'google_search' },
  { label: '图片搜索', value: 'image_search' },
  { label: '代码执行环境', value: 'code_execution' },
  { label: 'Google 地图', value: 'google_maps' },
  { label: '网页抓取与上下文', value: 'url_context' },
]

const imageModeOptions = [
  { value: 'image_only', label: 'image_only (仅输出图片)' },
  { value: 'text_and_image', label: 'text_and_image (文字与图片混排)' },
  { value: null, label: '不显式指定 (Null)' },
]

const thinkingLevelOptions = [
  { value: 'MINIMAL', label: 'MINIMAL (极速 / 最小思考)' },
  { value: 'LOW', label: 'LOW (轻度思考)' },
  { value: 'MEDIUM', label: 'MEDIUM (标准中度思考)' },
  { value: 'HIGH', label: 'HIGH (深度慢思考)' },
]

const mediaResolutionOptions = [
  { value: 'HIGH', label: 'HIGH (高分辨率)' },
  { value: 'MEDIUM', label: 'MEDIUM (标准清晰度)' },
  { value: 'LOW', label: 'LOW (低画质缩略)' },
  { value: null, label: '默认 (Null)' },
]

function toggleExpand(modelKey: string) {
  expandedMap.value[modelKey] = !expandedMap.value[modelKey]
}

function handleAddModel() {
  const name = newModelName.value.trim().toLowerCase().replace(/^models\//, '')
  if (!name) return

  const next: ModelOverrideMap = { ...(props.models || {}) }
  if (!next[name]) {
    next[name] = {
      default_tools: [],
      generation_config_defaults: {
        image_output_mode: null,
        media_resolution: null,
      },
    }
    expandedMap.value[name] = true
    emit('update:models', next)
  }
  newModelName.value = ''
}

function handleDeleteModel(modelKey: string) {
  const next: ModelOverrideMap = { ...(props.models || {}) }
  delete next[modelKey]
  emit('update:models', next)
}

function updateModelField<K extends keyof OverrideItem>(modelKey: string, field: K, val: OverrideItem[K]) {
  const next: ModelOverrideMap = { ...(props.models || {}) }
  const current = next[modelKey] || {}
  next[modelKey] = { ...current, [field]: val }
  emit('update:models', next)
}

function updateModelGenConfig(modelKey: string, key: string, val: unknown) {
  const next: ModelOverrideMap = { ...(props.models || {}) }
  const current = next[modelKey] || {}
  const currentGen = current.generation_config_defaults || {}
  next[modelKey] = {
    ...current,
    generation_config_defaults: { ...currentGen, [key]: val },
  }
  emit('update:models', next)
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-3">
      <div>
        <h4 class="text-xs font-bold text-gray-900 flex items-center gap-2">
          <Layers class="w-4 h-4 text-brand-600" />
          <span>单模型精确覆盖规则 (Exact Model Overrides)</span>
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          针对特定具体模型（如 gemini-3.7-flash）精确覆盖规则，优先级高于 Profiles 分组规则
        </p>
      </div>

      <!-- Add Input -->
      <div class="flex items-center gap-2">
        <input
          v-model="newModelName"
          type="text"
          placeholder="例如: gemini-3.8-flash"
          class="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
          @keydown.enter.prevent="handleAddModel"
        >
        <button
          type="button"
          :disabled="!newModelName.trim()"
          class="px-3 py-1.5 bg-brand-500 hover:bg-brand-600 text-white rounded-xl text-xs font-medium flex items-center gap-1 transition-colors cursor-pointer disabled:opacity-50"
          @click="handleAddModel"
        >
          <Plus class="w-3.5 h-3.5" />
          <span>添加覆盖</span>
        </button>
      </div>
    </div>

    <!-- Override List -->
    <div
      v-if="Object.keys(models || {}).length"
      class="space-y-3"
    >
      <div
        v-for="(override, modelKey) in models"
        :key="modelKey"
        class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden"
      >
        <!-- Header -->
        <div
          class="px-4 py-3 bg-gray-50/70 border-b border-gray-100 flex items-center justify-between gap-3 cursor-pointer select-none hover:bg-gray-100/60 transition-colors"
          @click="toggleExpand(String(modelKey))"
        >
          <div class="flex items-center gap-2 font-mono text-xs font-bold text-gray-900">
            <button
              type="button"
              class="text-gray-400 p-0.5"
            >
              <ChevronDown
                v-if="expandedMap[String(modelKey)]"
                class="w-4 h-4"
              />
              <ChevronRight
                v-else
                class="w-4 h-4"
              />
            </button>
            <span class="text-brand-700 bg-brand-50 px-2 py-0.5 rounded border border-brand-200/60">
              {{ modelKey }}
            </span>
          </div>

          <button
            type="button"
            class="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
            title="删除此模型覆盖"
            @click.stop="handleDeleteModel(String(modelKey))"
          >
            <Trash2 class="w-4 h-4" />
          </button>
        </div>

        <!-- Body -->
        <div
          v-if="expandedMap[String(modelKey)]"
          class="p-4 space-y-4"
        >
          <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            <ConfigSelect
              :model-value="override.generation_config_defaults?.image_output_mode"
              label="图片输出模式 (image_output_mode)"
              :options="imageModeOptions"
              @update:model-value="updateModelGenConfig(String(modelKey), 'image_output_mode', $event)"
            />

            <ConfigSelect
              :model-value="override.generation_config_defaults?.thinking_config?.level"
              label="思考强度等级 (thinking_level)"
              :options="thinkingLevelOptions"
              @update:model-value="updateModelGenConfig(String(modelKey), 'thinking_config', $event ? { level: $event, mode: 1 } : null)"
            />

            <ConfigSelect
              :model-value="override.generation_config_defaults?.media_resolution"
              label="多模态输入分辨率 (media_resolution)"
              :options="mediaResolutionOptions"
              @update:model-value="updateModelGenConfig(String(modelKey), 'media_resolution', $event)"
            />
          </div>

          <!-- Tools -->
          <ConfigTagList
            :model-value="override.default_tools || []"
            label="指定该模型专用工具 (default_tools)"
            description="覆盖通用 profile 的工具列表"
            :suggestions="toolSuggestions"
            @update:model-value="updateModelField(String(modelKey), 'default_tools', $event as string[])"
          />

          <!-- Switches Grid -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <ConfigSwitch
              :model-value="Boolean(override.drop_unsupported_params)"
              label="自动丢弃不支持参数 (drop_unsupported_params)"
              description="自动过滤未知安全类别与不兼容工具"
              @update:model-value="updateModelField(String(modelKey), 'drop_unsupported_params', $event)"
            />

            <ConfigSwitch
              :model-value="Boolean(override.disable_safety_settings)"
              label="完全不下发安全规则 (disable_safety_settings)"
              description="生图模型通常开启此项以避免被安全机制误拦截"
              @update:model-value="updateModelField(String(modelKey), 'disable_safety_settings', $event)"
            />
          </div>
          <!-- Safety Settings -->
          <div v-if="!override.disable_safety_settings">
            <ConfigSafetyGrid
              :model-value="override.safety_settings || {}"
              @update:model-value="updateModelField(String(modelKey), 'safety_settings', $event)"
            />
          </div>
        </div>
      </div>
    </div>

    <div
      v-else
      class="p-8 text-center text-gray-400 text-xs border border-dashed border-gray-200 rounded-2xl bg-gray-50/50"
    >
      暂无单模型覆盖配置，全部模型遵循上方 Profiles 分组规则。
    </div>
  </div>
</template>
