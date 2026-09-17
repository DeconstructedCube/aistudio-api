<script setup lang="ts">
import { ref } from 'vue'
import type { ModelProfileItem } from './types.ts'
import ConfigSwitch from './ConfigSwitch.vue'
import ConfigSelect from './ConfigSelect.vue'
import ConfigInput from './ConfigInput.vue'
import ConfigTagList from './ConfigTagList.vue'
import ConfigSafetyGrid from './ConfigSafetyGrid.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import {
  ChevronDown,
  ChevronRight,
  Trash2,
  Cpu,
  Wrench,
  Layers,
} from 'lucide-vue-next'

const props = defineProps<{
  profile: ModelProfileItem
  index: number
}>()

const emit = defineEmits<{
  'update:profile': [val: ModelProfileItem]
  delete: []
}>()

const expanded = ref(true)

const toolSuggestions = [
  { label: '网页+图片搜索', value: 'google_search_and_image_search', description: '适用于生图模型' },
  { label: 'Google 搜索', value: 'google_search', description: '网页搜索工具' },
  { label: '图片搜索', value: 'image_search', description: '仅图片检索' },
  { label: '代码执行环境', value: 'code_execution', description: 'Python 代码解释器' },
  { label: 'Google 地图', value: 'google_maps', description: '地理与地图位置' },
  { label: '网页抓取与上下文', value: 'url_context', description: 'URL 内容抓取' },
]

const imageModeOptions = [
  { value: 'image_only', label: 'image_only (仅输出图片)', description: '默认推荐生图模式' },
  { value: 'text_and_image', label: 'text_and_image (文字与图片混排)', description: '输出解说文字与图片' },
  { value: null, label: '不显式指定 (Null)', description: '遵循上游默认' },
]

const thinkingLevelOptions = [
  { value: 'MINIMAL', label: 'MINIMAL (极速 / 最小思考)', description: '生图或短回复推荐' },
  { value: 'LOW', label: 'LOW (轻度思考)' },
  { value: 'MEDIUM', label: 'MEDIUM (标准中度思考)' },
  { value: 'HIGH', label: 'HIGH (深度慢思考)', description: '推理模型默认' },
]

const mediaResolutionOptions = [
  { value: 'HIGH', label: 'HIGH (高分辨率 / 1024+)' },
  { value: 'MEDIUM', label: 'MEDIUM (标准清晰度)' },
  { value: 'LOW', label: 'LOW (低画质缩略)' },
  { value: null, label: '默认 (Null)' },
]

function updateField<K extends keyof ModelProfileItem>(key: K, val: ModelProfileItem[K]) {
  const next = { ...props.profile, [key]: val }
  emit('update:profile', next)
}

function updateGenerationConfig<K extends keyof NonNullable<ModelProfileItem['generation_config_defaults']>>(
  key: K,
  val: NonNullable<ModelProfileItem['generation_config_defaults']>[K]
) {
  const currentGen = props.profile.generation_config_defaults || {}
  const nextGen = { ...currentGen, [key]: val }
  updateField('generation_config_defaults', nextGen)
}

function updateThinkingConfig(level: 'MINIMAL' | 'LOW' | 'MEDIUM' | 'HIGH' | null) {
  const currentGen = props.profile.generation_config_defaults || {}
  const currentThinking = currentGen.thinking_config || { mode: 1 }
  if (level === null) {
    updateGenerationConfig('thinking_config', null)
  } else {
    updateGenerationConfig('thinking_config', { ...currentThinking, level })
  }
}
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden transition-all">
    <!-- Header / Toggle -->
    <div
      class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center justify-between gap-3 cursor-pointer select-none hover:bg-gray-100/60 transition-colors"
      @click="expanded = !expanded"
    >
      <div class="flex items-center gap-2.5 min-w-0">
        <button
          type="button"
          class="text-gray-400 p-0.5 rounded hover:bg-gray-200/60 transition-colors"
        >
          <ChevronDown
            v-if="expanded"
            class="w-4 h-4"
          />
          <ChevronRight
            v-else
            class="w-4 h-4"
          />
        </button>

        <div class="w-6 h-6 rounded-lg bg-brand-500 text-white flex items-center justify-center font-bold text-xs shrink-0 shadow-2xs">
          {{ index + 1 }}
        </div>

        <div class="truncate">
          <div class="flex items-center gap-2">
            <span class="font-bold text-gray-900 text-xs font-mono">
              {{ profile.name || 'unnamed_profile' }}
            </span>
            <span
              v-if="profile.is_image_model"
              class="px-2 py-0.2 text-[10px] font-semibold rounded-full bg-purple-100 text-purple-800"
            >
              生图模型规则
            </span>
          </div>
          <div class="text-[11px] text-gray-400 font-mono flex items-center gap-2 mt-0.5 truncate">
            <span v-if="profile.match?.contains?.length">包含: {{ profile.match.contains.join(', ') }}</span>
            <span v-if="profile.match?.prefixes?.length">前缀: {{ profile.match.prefixes.join(', ') }}</span>
            <span v-if="profile.match?.exact?.length">精确: {{ profile.match.exact.join(', ') }}</span>
          </div>
        </div>
      </div>

      <div
        class="flex items-center gap-2 shrink-0"
        @click.stop
      >
        <button
          type="button"
          class="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
          title="删除该规则预设"
          @click="emit('delete')"
        >
          <Trash2 class="w-4 h-4" />
        </button>
      </div>
    </div>

    <!-- Body -->
    <div
      v-if="expanded"
      class="p-5 space-y-5"
    >
      <!-- Profile Name & Image Model Toggle -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="space-y-1.5">
          <div class="flex items-center gap-1.5">
            <div class="flex-1">
              <ConfigInput
                :model-value="profile.name"
                label="规则分组名称 (Profile Name)"
                description="唯一标识该规则组"
                placeholder="例如: image_models"
                @update:model-value="updateField('name', String($event || ''))"
              />
            </div>
            <FieldHelpTip schema-key="profile.name" />
          </div>
        </div>

        <div class="flex items-center gap-1.5">
          <div class="flex-1">
            <ConfigSwitch
              :model-value="Boolean(profile.is_image_model)"
              label="作为生图模型 (is_image_model)"
              description="开启后自动采用生图请求管线与多模态图片编码"
              @update:model-value="updateField('is_image_model', $event)"
            />
          </div>
          <FieldHelpTip schema-key="profile.is_image_model" />
        </div>
      </div>

      <!-- Match Conditions -->
      <div class="p-3.5 bg-gray-50/70 border border-gray-200/70 rounded-xl space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
            <Layers class="w-4 h-4 text-brand-600" />
            <span>模型名称匹配条件 (Match Rules)</span>
          </div>
          <FieldHelpTip
            title="匹配规则"
            content="系统按 contains、prefixes、exact 顺序依次检测模型名，命中后自动应用该组配置。"
          />
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <ConfigTagList
            :model-value="profile.match?.contains || []"
            label="包含关键词 (contains)"
            description="如 image"
            placeholder="输入关键词并按回车"
            @update:model-value="updateField('match', { ...profile.match, contains: $event as string[] })"
          />

          <ConfigTagList
            :model-value="profile.match?.prefixes || []"
            label="前缀匹配 (prefixes)"
            description="如 gemini-, gemma-"
            placeholder="输入前缀并按回车"
            @update:model-value="updateField('match', { ...profile.match, prefixes: $event as string[] })"
          />

          <ConfigTagList
            :model-value="profile.match?.exact || []"
            label="精确全称匹配 (exact)"
            description="如特定模型名"
            placeholder="输入完整模型名并按回车"
            @update:model-value="updateField('match', { ...profile.match, exact: $event as string[] })"
          />
        </div>
      </div>

      <!-- Default Tools -->
      <div class="p-3.5 bg-gray-50/70 border border-gray-200/70 rounded-xl space-y-2">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
            <Wrench class="w-4 h-4 text-brand-600" />
            <span>默认内置工具下发 (Default Tools)</span>
          </div>
          <FieldHelpTip schema-key="profile.default_tools" />
        </div>
        <ConfigTagList
          label="已启用的默认工具"
          description="客户端未显式传 tools 时，默认自动挂载的工具"
          :suggestions="toolSuggestions"
          @update:model-value="updateField('default_tools', $event as string[])"
        />
      </div>

      <!-- Generation Config Defaults -->
      <div class="p-3.5 bg-gray-50/70 border border-gray-200/70 rounded-xl space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
            <Cpu class="w-4 h-4 text-brand-600" />
            <span>生成参数默认值 (Generation Config Defaults)</span>
          </div>
          <FieldHelpTip
            title="生成参数"
            content="映射至 Google Wire 请求中的 generation_config 结构，控制思考深度、生图模式与多模态分辨率。"
          />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <ConfigSelect
            :model-value="profile.generation_config_defaults?.image_output_mode"
            label="图片输出模式 (image_output_mode)"
            :options="imageModeOptions"
            @update:model-value="updateGenerationConfig('image_output_mode', $event as string | null)"
          />

          <ConfigSelect
            :model-value="profile.generation_config_defaults?.thinking_config?.level"
            label="思考强度等级 (thinking_level)"
            :options="thinkingLevelOptions"
            @update:model-value="updateThinkingConfig($event as any)"
          />

          <ConfigSelect
            :model-value="profile.generation_config_defaults?.media_resolution"
            label="多模态输入分辨率 (media_resolution)"
            :options="mediaResolutionOptions"
            @update:model-value="updateGenerationConfig('media_resolution', $event as string | null)"
          />
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
          <ConfigTagList
            :model-value="profile.clear_generation_config_indexes || []"
            label="清空 generation_config 特殊下标 (clear_indexes)"
            description="针对特定模型发送前清除的 wire 数组索引 (如 7, 13, 17)"
            :is-number="true"
            placeholder="输入数字下标按回车"
            @update:model-value="updateField('clear_generation_config_indexes', $event as number[])"
          />

          <ConfigSwitch
            :model-value="Boolean(profile.disable_safety_settings)"
            label="完全不下发安全规则 (disable_safety_settings)"
            description="生图模型通常开启此项以避免被安全机制误拦截"
            @update:model-value="updateField('disable_safety_settings', $event)"
          />
        </div>
      </div>

      <!-- Safety Settings (If not completely disabled) -->
      <div v-if="!profile.disable_safety_settings">
        <ConfigSafetyGrid
          :model-value="profile.safety_settings || {}"
          @update:model-value="updateField('safety_settings', $event)"
        />
      </div>
    </div>
  </div>
</template>
