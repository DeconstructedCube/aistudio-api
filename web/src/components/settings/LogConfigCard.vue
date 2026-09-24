<script setup lang="ts">
import { ref, watch } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SystemConfig } from '@/types/system.ts'
import Button from '@/components/ui/Button.vue'
import {
  Activity,
  Sliders,
  Save,
  CheckCircle2,
  Bug,
  Info,
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
const debugEnvActive = ref(Boolean(props.initialConfig.debug_env_active))
const saving = ref(false)

watch(
  () => props.initialConfig,
  (cfg) => {
    logLevel.value = cfg.log_level || 'INFO'
    dumpRequests.value = Boolean(cfg.dump_requests)
    debugEnvActive.value = Boolean(cfg.debug_env_active)
  },
  { deep: true },
)

const logLevels = [
  { value: 'DEBUG', label: 'DEBUG (详细调试输出)', desc: '输出最详尽的底层网络与调度跟踪信息' },
  { value: 'INFO', label: 'INFO (标准运行信息)', desc: '推荐生产使用，输出关键请求与账号切换' },
  { value: 'WARNING', label: 'WARNING (仅警告与错误)', desc: '仅输出异常告警与故障重试' },
  { value: 'ERROR', label: 'ERROR (仅严重错误)', desc: '仅记录系统无法自动恢复的严重故障' },
]

async function handleSave() {
  saving.value = true
  try {
    const res = await systemApi.updateLoggingConfig({
      level: logLevel.value,
      dump_requests: dumpRequests.value,
    })
    logLevel.value = res.log_level
    dumpRequests.value = res.dump_requests
    debugEnvActive.value = res.debug_env_active
    toast.success('日志与调试配置已即时更新并持久化至 config.yaml')
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

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Log Level Configuration -->
      <div class="space-y-2">
        <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider flex items-center gap-1.5">
          <Sliders class="w-3.5 h-3.5 text-gray-500" />
          <span>日志输出级别 (Log Level)</span>
        </label>
        <select
          v-model="logLevel"
          class="w-full px-3.5 py-2.5 text-xs bg-gray-50/80 border border-gray-200 rounded-xl text-gray-900 outline-none focus:ring-2 focus:ring-brand-500 focus:bg-white font-medium transition-all"
        >
          <option
            v-for="item in logLevels"
            :key="item.value"
            :value="item.value"
          >
            {{ item.label }}
          </option>
        </select>
        <p class="text-[11px] text-gray-400">
          修改后无需重启服务，后端各模块即时生效。
        </p>
      </div>

      <!-- Request Dump Toggle -->
      <div class="space-y-2">
        <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider flex items-center gap-1.5">
          <Bug class="w-3.5 h-3.5 text-gray-500" />
          <span>请求转储 (Dump Requests)</span>
        </label>
        <div class="flex items-center justify-between p-3 bg-gray-50/80 border border-gray-200 rounded-xl">
          <div class="pr-3">
            <div class="text-xs font-medium text-gray-900 flex items-center gap-1.5">
              <span>转储每次请求与响应详情</span>
              <span
                v-if="dumpRequests"
                class="text-[10px] text-emerald-600 font-semibold flex items-center gap-0.5"
              >
                <CheckCircle2 class="w-3 h-3" /> 已开启
              </span>
            </div>
            <p class="text-[11px] text-gray-400 mt-0.5 leading-snug">
              完整打印每次 API 请求的 Headers、Query、Body 及响应状态与耗时
            </p>
          </div>
          <button
            type="button"
            :class="[
              dumpRequests ? 'bg-brand-600' : 'bg-gray-300',
              'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-hidden',
            ]"
            @click="dumpRequests = !dumpRequests"
          >
            <span
              :class="[
                dumpRequests ? 'translate-x-5' : 'translate-x-0',
                'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out',
              ]"
            />
          </button>
        </div>
        <p class="text-[11px] text-gray-400">
          亦可通过启动环境变量 <code>DEBUG=true</code> 快速开启。
        </p>
      </div>
    </div>
  </div>
</template>
