<script setup lang="ts">
import { ref } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import Button from '@/components/ui/Button.vue'
import { FileCode, Save } from 'lucide-vue-next'

const props = defineProps<{
  initialContent?: string
}>()

const emit = defineEmits<{
  saved: []
}>()

const toast = useToastStore()
const yamlContent = ref(props.initialContent || '')
const saving = ref(false)

async function handleSaveYaml() {
  saving.value = true
  try {
    await systemApi.updateConfigYaml(yamlContent.value)
    toast.success('模型规则配置已保存并完成热重载')
    emit('saved')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存失败'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-4">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2">
        <FileCode class="w-4 h-4 text-brand-600" />
        <div>
          <h3 class="font-semibold text-gray-900 text-sm">
            模型默认规则与安全配置 (config.yaml)
          </h3>
          <p class="text-xs text-gray-400 mt-0.5">
            控制生图模型默认工具、Gemma/Gemini 内置搜索与安全拦截等级
          </p>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          :loading="saving"
          @click="handleSaveYaml"
        >
          <Save class="w-3.5 h-3.5" />
          <span>保存并热重载配置</span>
        </Button>
      </div>
    </div>

    <div class="space-y-1.5">
      <textarea
        v-model="yamlContent"
        rows="18"
        spellcheck="false"
        placeholder="正在加载 config.yaml..."
        class="w-full p-4 font-mono text-xs bg-gray-900 text-gray-100 rounded-xl outline-none focus:ring-2 focus:ring-brand-500 leading-relaxed shadow-inner border border-gray-800"
      />
    </div>
  </div>
</template>
