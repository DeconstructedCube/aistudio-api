<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SystemConfig } from '@/types'
import Button from '@/components/ui/Button.vue'
import ApiKeyManagerCard from '@/components/settings/ApiKeyManagerCard.vue'
import {
  Server,
  FileCode,
  Save,
  CheckCircle2,
  AlertCircle,
  Cpu,
  Globe,
  Lock,
} from 'lucide-vue-next'

const toast = useToastStore()

const config = ref<SystemConfig | null>(null)
const yamlContent = ref('')
const loading = ref(false)
const saving = ref(false)

async function loadConfig() {
  loading.value = true
  try {
    const res = await systemApi.getConfig()
    config.value = res
    yamlContent.value = res.yaml_content
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '获取系统配置失败'
    toast.error(msg)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadConfig()
})

async function handleSaveYaml() {
  saving.value = true
  try {
    await systemApi.updateConfigYaml(yamlContent.value)
    toast.success('模型规则配置已保存并完成热重载')
    await loadConfig()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存失败'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <!-- Top Title -->
    <div>
      <h2 class="text-xl font-bold text-gray-900 tracking-tight">
        系统与模型规则配置
      </h2>
      <p class="text-xs text-gray-500 mt-0.5">
        查看服务端运行时环境参数，以及管理模型默认工具和安全过滤规则 (config.yaml)
      </p>
    </div>

    <!-- Server Runtime Status Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
        <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          <Server class="w-4 h-4 text-blue-600" />
          <span>服务端口 / 浏览器</span>
        </div>
        <div class="text-base font-bold text-gray-900 font-mono">
          :{{ config?.port || 8080 }} <span class="text-xs text-gray-400 font-normal">/ CDP :{{ config?.browser_port || 9222 }}</span>
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          无头模式: {{ config?.browser_headless ? '开启 (Headless)' : '禁用' }}
        </div>
      </div>

      <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
        <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          <Cpu class="w-4 h-4 text-emerald-600" />
          <span>并发与缓存控制</span>
        </div>
        <div class="text-base font-bold text-gray-900 font-mono">
          {{ config?.max_concurrency || 3 }} 并发上限
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          快照 TTL: {{ config?.snapshot_cache_ttl || 3600 }} 秒
        </div>
      </div>

      <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
        <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          <Globe class="w-4 h-4 text-amber-600" />
          <span>网络代理状态</span>
        </div>
        <div class="text-base font-bold text-gray-900">
          <span
            v-if="config?.proxy_configured"
            class="text-emerald-600 flex items-center gap-1"
          >
            <CheckCircle2 class="w-4 h-4" /> 已配置代理
          </span>
          <span
            v-else
            class="text-gray-400 text-sm"
          >直连 (未配代理)</span>
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          支持 HTTP / SOCKS5 出口代理
        </div>
      </div>

      <div class="bg-white border border-gray-200/80 rounded-2xl p-4 shadow-xs">
        <div class="flex items-center gap-2 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
          <Lock class="w-4 h-4 text-rose-600" />
          <span>API 鉴权保护</span>
        </div>
        <div class="text-base font-bold">
          <span
            v-if="config?.auth_enabled"
            class="text-emerald-600 flex items-center gap-1"
          >
            <CheckCircle2 class="w-4 h-4" /> 鉴权已启用
          </span>
          <span
            v-else
            class="text-amber-600 flex items-center gap-1"
          >
            <AlertCircle class="w-4 h-4" /> 未开启鉴权
          </span>
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          AISTUDIO_WEB_PASSWORD 控制台保护
        </div>
      </div>
    </div>

    <!-- API Key Management Card -->
    <ApiKeyManagerCard />

    <!-- YAML Config Editor -->
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
  </div>
</template>
