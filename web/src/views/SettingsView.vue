<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SystemConfig } from '@/types/system.ts'
import ApiKeyManagerCard from '@/components/settings/ApiKeyManagerCard.vue'
import LogConfigCard from '@/components/settings/LogConfigCard.vue'
import VisualConfigEditor from '@/components/config/VisualConfigEditor.vue'
import {
  Server,
  CheckCircle2,
  AlertCircle,
  Cpu,
  Globe,
  Lock,
} from 'lucide-vue-next'

const toast = useToastStore()

const config = ref<SystemConfig | null>(null)
const loading = ref(false)

async function loadConfig() {
  loading.value = true
  try {
    config.value = await systemApi.getConfig()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '获取系统配置失败'
    toast.error(msg)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadConfig()
})
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
          <span>快照与缓存控制</span>
        </div>
        <div class="text-base font-bold text-gray-900 font-mono">
          TTL: {{ config?.snapshot_cache_ttl || 3600 }}s
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          BotGuard 快照缓存有效期
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
          <span>控制台鉴权状态</span>
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
            <AlertCircle class="w-4 h-4" /> 免密开放模式
          </span>
        </div>
        <div class="text-[11px] text-gray-400 mt-1">
          {{ config?.auth_enabled ? '已受 AISTUDIO_WEB_PASSWORD 保护' : '未配置 AISTUDIO_WEB_PASSWORD' }}
        </div>
      </div>
    </div>

    <!-- Auth Warning Banner if Auth is disabled -->
    <div
      v-if="config && !config.auth_enabled"
      class="p-4 bg-amber-50/80 border border-amber-200 rounded-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-amber-900"
    >
      <div class="flex items-start gap-2.5">
        <AlertCircle class="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
        <div>
          <div class="font-bold text-amber-900">
            当前控制台处于免密开放模式
          </div>
          <div class="text-amber-700/90 mt-0.5 leading-relaxed">
            如需在局域网或公网环境下防止未授权访问与修改，可在系统环境变量或 <code>.env</code> 文件中添加 <code>AISTUDIO_WEB_PASSWORD=你的安全密码</code> 并重启服务。
          </div>
        </div>
      </div>
    </div>


    <!-- Logging & Debug Config Card -->
    <LogConfigCard
      v-if="config"
      :initial-config="config"
      @saved="loadConfig"
    />
    <!-- API Key Management Card -->
    <ApiKeyManagerCard />

    <!-- Visual & YAML Model Config Editor -->
    <VisualConfigEditor
      v-if="config"
      :initial-yaml="config.yaml_content"
      @saved="loadConfig"
    />
  </div>
</template>
