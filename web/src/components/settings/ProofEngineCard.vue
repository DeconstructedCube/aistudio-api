<script setup lang="ts">
import { ref, watch } from 'vue'
import { systemApi } from '@/api/system.ts'
import { accountsApi } from '@/api/accounts.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { ProofEngineConfig } from '@/types/system.ts'
import Button from '@/components/ui/Button.vue'
import {
  Zap,
  ShieldCheck,
  Save,
  Globe,
  Clock,
  Sparkles,
  ArrowRightLeft,
  CheckCircle2,
  RefreshCw,
} from 'lucide-vue-next'

const props = defineProps<{
  initialEngine?: ProofEngineConfig
}>()

const emit = defineEmits<{
  saved: []
}>()

const toast = useToastStore()

const provider = ref<'browser' | 'remote'>(props.initialEngine?.provider || 'browser')
const remoteUrl = ref(props.initialEngine?.remote_url || 'https://waa.spinsnow.fun')
const autoFallback = ref(props.initialEngine?.auto_fallback !== false)
const timeoutSeconds = ref(props.initialEngine?.timeout_seconds || 15)
const saving = ref(false)
const rotating = ref(false)

async function handleManualRotate() {
  rotating.value = true
  try {
    const res = await accountsApi.rotateCookies()
    toast.success(res.message || 'Cookie 轮转刷新成功，已自动同步到同组所有账号')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : 'Cookie 轮转刷新失败'
    toast.error(msg)
  } finally {
    rotating.value = false
  }
}

watch(
  () => props.initialEngine,
  (eng) => {
    if (eng) {
      provider.value = eng.provider || 'browser'
      remoteUrl.value = eng.remote_url || 'https://waa.spinsnow.fun'
      autoFallback.value = eng.auto_fallback !== false
      timeoutSeconds.value = eng.timeout_seconds || 15
    }
  },
  { deep: true },
)

async function handleSave() {
  saving.value = true
  try {
    const res = await systemApi.updateProofEngineConfig({
      provider: provider.value,
      remote_url: remoteUrl.value.trim(),
      auto_fallback: autoFallback.value,
      timeout_seconds: Number(timeoutSeconds.value) || 15,
    })
    toast.success(res.message || '签名引擎配置已保存并热生效')
    emit('saved')
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存签名引擎配置失败'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-6">
    <!-- Header -->
    <div class="flex items-start justify-between gap-4">
      <div class="space-y-1">
        <div class="flex items-center gap-2">
          <div class="p-1.5 bg-brand-50 rounded-lg text-brand-600">
            <Sparkles class="w-5 h-5 text-indigo-600" />
          </div>
          <h3 class="text-base font-bold text-gray-900">
            WAA / BotGuard 签名引擎配置
          </h3>
          <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200/60">
            <ArrowRightLeft class="w-3 h-3" />
            支持热切换·无需重启
          </span>
        </div>
        <p class="text-xs text-gray-500">
          支持在极速纯 Node.js 逆向签名 API 与本地真实 Chrome 浏览器环境之间无缝热切换，并支持异常自动降级。
        </p>
      </div>

      <Button
        variant="primary"
        size="sm"
        :loading="saving"
        class="shrink-0"
        @click="handleSave"
      >
        <Save class="w-4 h-4 mr-1.5" />
        保存并热生效
      </Button>
    </div>

    <!-- Engine Mode Selection Grid -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Option 1: Remote WAA API -->
      <div
        class="relative border-2 rounded-xl p-4 cursor-pointer transition-all flex flex-col justify-between"
        :class="[
          provider === 'remote'
            ? 'border-indigo-600 bg-indigo-50/30 shadow-xs'
            : 'border-gray-200 hover:border-gray-300 bg-white',
        ]"
        @click="provider = 'remote'"
      >
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <div
                class="p-2 rounded-lg"
                :class="provider === 'remote' ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'"
              >
                <Zap class="w-5 h-5" />
              </div>
              <div>
                <h4 class="text-sm font-bold text-gray-900">
                  🚀 逆向免浏览器 API (Remote WAA)
                </h4>
                <span class="text-[11px] text-indigo-600 font-medium">极速签发 · 纯 Node.js 计算</span>
              </div>
            </div>
            <div
              class="w-5 h-5 rounded-full border flex items-center justify-center transition-colors"
              :class="provider === 'remote' ? 'border-indigo-600 bg-indigo-600 text-white' : 'border-gray-300'"
            >
              <CheckCircle2
                v-if="provider === 'remote'"
                class="w-4 h-4"
              />
            </div>
          </div>
          <p class="text-xs text-gray-500 leading-relaxed">
            由远端纯 Node.js 逆向算法服务毫秒级生成 <code>!proof</code> 签名。无需本地 Chromium 浏览器参与运算，大幅降低 CPU/内存开销与等待延迟。
          </p>
        </div>

        <div class="mt-3 pt-3 border-t border-gray-100 flex items-center justify-between text-[11px] text-gray-500">
          <span>延迟：~30ms (热态)</span>
          <span class="text-indigo-600 font-medium">推荐轻量/多账号场景</span>
        </div>
      </div>

      <!-- Option 2: Local Chrome Browser -->
      <div
        class="relative border-2 rounded-xl p-4 cursor-pointer transition-all flex flex-col justify-between"
        :class="[
          provider === 'browser'
            ? 'border-emerald-600 bg-emerald-50/30 shadow-xs'
            : 'border-gray-200 hover:border-gray-300 bg-white',
        ]"
        @click="provider = 'browser'"
      >
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <div
                class="p-2 rounded-lg"
                :class="provider === 'browser' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-600'"
              >
                <ShieldCheck class="w-5 h-5" />
              </div>
              <div>
                <h4 class="text-sm font-bold text-gray-900">
                  🛡️ 本地真实 Chrome 浏览器 (CDP)
                </h4>
                <span class="text-[11px] text-emerald-600 font-medium">100% 官方沙箱 · 最高防御</span>
              </div>
            </div>
            <div
              class="w-5 h-5 rounded-full border flex items-center justify-center transition-colors"
              :class="provider === 'browser' ? 'border-emerald-600 bg-emerald-600 text-white' : 'border-gray-300'"
            >
              <CheckCircle2
                v-if="provider === 'browser'"
                class="w-4 h-4"
              />
            </div>
          </div>
          <p class="text-xs text-gray-500 leading-relaxed">
            本地 CloakBrowser 沙箱环境常驻运行，通过 CDP 在真实 Google 页面上下文内执行官方 BotGuard 脚本。100% 官方真实环境模拟，最高抗风控能力。
          </p>
        </div>

        <div class="mt-3 pt-3 border-t border-gray-100 flex items-center justify-between text-[11px] text-gray-500">
          <span>延迟：~200ms (浏览器执行)</span>
          <span class="text-emerald-600 font-medium">坚实兜底/最高稳定性</span>
        </div>
      </div>
    </div>

    <!-- Additional Remote Engine Settings (Auto-fallback & URL) -->
    <div class="bg-gray-50/80 border border-gray-200/60 rounded-xl p-4 space-y-4">
      <!-- Auto Fallback Checkbox -->
      <div class="flex items-start gap-3">
        <div class="pt-0.5">
          <input
            id="autoFallbackCheck"
            v-model="autoFallback"
            type="checkbox"
            class="w-4 h-4 text-brand-600 rounded border-gray-300 focus:ring-brand-500 cursor-pointer"
          >
        </div>
        <label
          for="autoFallbackCheck"
          class="cursor-pointer space-y-0.5"
        >
          <div class="text-xs font-bold text-gray-900 flex items-center gap-1.5">
            <span>逆向 API 异常时自动降级到本地 Chrome 浏览器 (Auto-fallback)</span>
            <span class="text-[10px] bg-emerald-100 text-emerald-800 px-1.5 py-0.2 rounded font-semibold">强烈推荐开启</span>
          </div>
          <p class="text-[11px] text-gray-500">
            当选择“逆向免浏览器 API”时，若接口遇到网络波动、500 报错或响应超时，系统将自动无缝降级使用本地 Chrome 浏览器生成快照，确保对外 API 请求零中断。
          </p>
        </label>
      </div>

      <!-- Remote URL & Timeout -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-gray-200/60">
        <div class="sm:col-span-2 space-y-1">
          <label class="block text-[11px] font-semibold text-gray-700 flex items-center gap-1">
            <Globe class="w-3.5 h-3.5 text-gray-500" />
            <span>逆向签名服务地址 (Remote Proof URL)</span>
          </label>
          <input
            v-model="remoteUrl"
            type="text"
            placeholder="https://waa.spinsnow.fun"
            class="w-full text-xs font-mono px-3 py-2 bg-white border border-gray-300 rounded-lg shadow-2xs outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          >
        </div>

        <div class="space-y-1">
          <label class="block text-[11px] font-semibold text-gray-700 flex items-center gap-1">
            <Clock class="w-3.5 h-3.5 text-gray-500" />
            <span>超时时间 (秒)</span>
          </label>
          <input
            v-model.number="timeoutSeconds"
            type="number"
            min="3"
            max="60"
            class="w-full text-xs font-mono px-3 py-2 bg-white border border-gray-300 rounded-lg shadow-2xs outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          >
        </div>
      </div>

      <!-- Dual-layer Cookie Keep-Alive Section -->
      <div class="pt-3 border-t border-gray-200/60 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="space-y-0.5">
          <div class="text-xs font-bold text-gray-900 flex items-center gap-1.5">
            <RefreshCw class="w-3.5 h-3.5 text-blue-600" />
            <span>纯 HTTP RotateCookies 双层凭据轮转</span>
            <span class="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.2 rounded font-semibold border border-blue-200/60">后台自动启用</span>
          </div>
          <p class="text-[11px] text-gray-500">
            优先通过 Google 原生 HTTP 接口（~100ms）轮转 <code>__Secure-1PSIDTS</code> 短效凭据，失败时自动降级唤醒本地浏览器保活，零页面开销。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          :loading="rotating"
          class="shrink-0 text-xs self-start sm:self-auto"
          @click="handleManualRotate"
        >
          <RefreshCw class="w-3.5 h-3.5 mr-1 text-blue-600" />
          立即轮转刷新
        </Button>
      </div>
    </div>
  </div>
</template>
