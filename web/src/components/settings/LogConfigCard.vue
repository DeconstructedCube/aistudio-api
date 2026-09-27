<script setup lang="ts">
import { ref, watch } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SystemConfig } from '@/types/system.ts'
import Button from '@/components/ui/Button.vue'
import ConfigSelect from '@/components/config/ConfigSelect.vue'
import ConfigSwitch from '@/components/config/ConfigSwitch.vue'
import ConfigInput from '@/components/config/ConfigInput.vue'
import {
  Activity,
  Save,
  Bug,
  Info,
  HardDrive,
  Folder,
} from 'lucide-vue-next'

const props = defineProps<{
  initialConfig: SystemConfig
}>()

const emit = defineEmits<{
  saved: []
}>()

const toast = useToastStore()

const logLevel = ref(props.initialConfig.log_level || 'INFO')
const dumpRequests = ref(Boolean(props.initialConfig.dump_requests))
const dumpToFile = ref(Boolean(props.initialConfig.dump_to_file))
const dumpDir = ref(props.initialConfig.dump_dir || 'dumps')
const debugEnvActive = ref(Boolean(props.initialConfig.debug_env_active))
const saving = ref(false)

watch(
  () => props.initialConfig,
  (cfg) => {
    logLevel.value = cfg.log_level || 'INFO'
    dumpRequests.value = Boolean(cfg.dump_requests)
    dumpToFile.value = Boolean(cfg.dump_to_file)
    dumpDir.value = cfg.dump_dir || 'dumps'
    debugEnvActive.value = Boolean(cfg.debug_env_active)
  },
  { deep: true },
)

const logLevels = [
  { value: 'DEBUG', label: '调试 (DEBUG)', description: '详细底层跟踪' },
  { value: 'INFO', label: '信息 (INFO)', description: '标准运行信息' },
  { value: 'WARNING', label: '警告 (WARNING)', description: '仅警告与错误' },
  { value: 'ERROR', label: '错误 (ERROR)', description: '仅严重错误' },
]

async function handleSave() {
  saving.value = true
  try {
    const res = await systemApi.updateLoggingConfig({
      level: logLevel.value,
      dump_requests: dumpRequests.value,
      dump_to_file: dumpToFile.value,
      dump_dir: dumpDir.value,
    })
    logLevel.value = res.log_level
    dumpRequests.value = res.dump_requests
    dumpToFile.value = res.dump_to_file
    dumpDir.value = res.dump_dir
    debugEnvActive.value = res.debug_env_active
    emit('saved')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存配置失败'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-5">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2.5">
        <div class="p-2 rounded-xl bg-purple-50 text-purple-600 border border-purple-100">
          <Activity class="w-5 h-5" />
        </div>
        <div>
          <h3 class="font-semibold text-gray-900 text-sm flex items-center gap-2">
            <span>日志与调试</span>
            <span
              v-if="debugEnvActive"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-100 text-emerald-800"
            >
              <Bug class="w-3 h-3" /> DEBUG 环境变量生效中
            </span>
          </h3>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          :loading="saving"
          @click="handleSave"
        >
          <Save class="w-3.5 h-3.5" />
          <span>保存日志配置</span>
        </Button>
      </div>
    </div>

    <!-- Env variable hint banner if DEBUG is active -->
    <div
      v-if="debugEnvActive"
      class="p-3.5 bg-blue-50/80 border border-blue-200 rounded-xl flex items-start gap-2.5 text-xs text-blue-900 leading-relaxed"
    >
      <Info class="w-4 h-4 text-blue-600 shrink-0 mt-0.5" />
      <div>
        <span class="font-bold">DEBUG 环境变量提示：</span>
        系统已设置 <code>DEBUG=true</code> 环境变量，服务已激活请求报文转储。日志级别遵循下方独立设置。
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <!-- 1. Log Level Configuration -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <ConfigSelect
          :model-value="logLevel"
          label="日志输出级别"
          :options="logLevels"
          @update:model-value="logLevel = String($event || 'INFO')"
        />
      </div>

      <!-- 2. Request Dump Toggle -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <ConfigSwitch
          :model-value="dumpRequests"
          label="请求报文转储"
          description="在标准输出中打印完整的请求与响应详情"
          @update:model-value="dumpRequests = $event"
        />
      </div>

      <!-- 3. Dump To File Switch -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
          <HardDrive class="w-3.5 h-3.5 text-brand-600" />
          <span>报文持久化落盘</span>
        </div>
        <ConfigSwitch
          :model-value="dumpToFile"
          label="将报文保存为文件"
          description="将每次请求与响应报文写入磁盘独立文件"
          @update:model-value="dumpToFile = $event"
        />
      </div>

      <!-- 4. Dump Directory Input -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
          <Folder class="w-3.5 h-3.5 text-brand-600" />
          <span>转储文件存储目录</span>
        </div>
        <ConfigInput
          :model-value="dumpDir"
          label="保存路径"
          placeholder="dumps"
          @update:model-value="dumpDir = String($event || 'dumps')"
        />
      </div>
    </div>
  </div>
</template>
