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
  { value: 'DEBUG', label: 'DEBUG (详细调试输出)', description: '输出最详尽的底层网络与调度跟踪信息' },
  { value: 'INFO', label: 'INFO (标准运行信息)', description: '推荐生产使用，输出关键请求与账号切换' },
  { value: 'WARNING', label: 'WARNING (仅警告与错误)', description: '仅输出异常告警与故障重试' },
  { value: 'ERROR', label: 'ERROR (仅严重错误)', description: '仅记录系统无法自动恢复的严重故障' },
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
            <span>日志与调试配置</span>
            <span
              v-if="debugEnvActive"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-100 text-emerald-800"
            >
              <Bug class="w-3 h-3" /> DEBUG 环境变量生效中
            </span>
          </h3>
          <p class="text-xs text-gray-400 mt-0.5">
            控制终端日志输出过滤级别，以及开启单次请求完整报文转储 (Dump)
          </p>
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
        系统检测到已设置 <code>DEBUG=true</code> 环境变量，服务已激活请求报文自动转储。日志级别仍遵循下方独立设置。
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <!-- 1. Log Level Configuration -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <ConfigSelect
          :model-value="logLevel"
          label="日志输出级别 (Log Level)"
          description="运行时热重载"
          :options="logLevels"
          @update:model-value="logLevel = String($event || 'INFO')"
        />
        <p class="text-[11px] text-gray-400">
          控制控制台终端的最低输出详细程度，修改后无需重启服务即时生效。
        </p>
      </div>

      <!-- 2. Request Dump Toggle -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <div class="block text-xs font-semibold text-gray-700">
          控制台请求转储 (Dump Requests)
        </div>
        <ConfigSwitch
          :model-value="dumpRequests"
          label="终端打印每次请求与响应详情"
          description="在标准输出中转储完整的 Headers、Query、Body 文本与状态"
          @update:model-value="dumpRequests = $event"
        />
        <p class="text-[11px] text-gray-400">
          适用于联调抓包，亦可通过启动环境变量 <code>DEBUG=true</code> 激活。
        </p>
      </div>

      <!-- 3. Dump To File Switch -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
          <HardDrive class="w-3.5 h-3.5 text-brand-600" />
          <span>报文持久化落盘 (Dump to File)</span>
        </div>
        <ConfigSwitch
          :model-value="dumpToFile"
          label="转储报文为独立文件落盘"
          description="将每次请求/响应报文自动存为独立文件，便于事后复现与排查"
          @update:model-value="dumpToFile = $event"
        />
        <p class="text-[11px] text-gray-400">
          对应环境变量 <code>AISTUDIO_DUMP_FILE=true</code>。
        </p>
      </div>

      <!-- 4. Dump Directory Input -->
      <div class="p-4 bg-gray-50/60 border border-gray-200/70 rounded-2xl space-y-2">
        <div class="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
          <Folder class="w-3.5 h-3.5 text-brand-600" />
          <span>转储文件存储目录 (Dump Directory)</span>
        </div>
        <ConfigInput
          :model-value="dumpDir"
          label="保存路径"
          description="相对或绝对路径"
          placeholder="dumps"
          @update:model-value="dumpDir = String($event || 'dumps')"
        />
        <p class="text-[11px] text-gray-400 font-mono">
          默认落盘目录为项目根目录下的 dumps/
        </p>
      </div>
    </div>
  </div>
</template>
