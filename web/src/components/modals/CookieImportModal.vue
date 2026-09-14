<script setup lang="ts">
import { ref } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import { Sparkles } from 'lucide-vue-next'

defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
}>()

const accountsStore = useAccountsStore()

const cookieInput = ref('')
const namePrefix = ref('')
const singleEmail = ref('')
const autoProbe = ref(true)

async function handleSubmit() {
  const raw = cookieInput.value.trim()
  if (!raw) return

  const ok = autoProbe.value
    ? await accountsStore.probeAndImport({
        cookies: raw,
        name_prefix: namePrefix.value.trim() || undefined,
      })
    : await accountsStore.importCookies({
        cookies: raw,
        name: namePrefix.value.trim() || undefined,
        email: singleEmail.value.trim() || undefined,
      })

  if (ok) {
    cookieInput.value = ''
    namePrefix.value = ''
    singleEmail.value = ''
    emit('update:modelValue', false)
  }
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    max-width="lg"
    title="导入 Google 账号 Cookies"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <div class="space-y-1.5">
        <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider flex items-center justify-between">
          <span>Cookie 数据</span>
          <span class="text-[11px] text-gray-400 font-normal">支持 JSON 数组、Netscape 或 KV 文本</span>
        </label>
        <textarea
          v-model="cookieInput"
          rows="6"
          placeholder="[ { &quot;name&quot;: &quot;SAPISID&quot;, &quot;value&quot;: &quot;...&quot; }, ... ] 或&#10;SID=xxx; SSID=xxx; HSID=xxx; SAPISID=xxx; ..."
          class="w-full p-3 font-mono text-xs bg-gray-50 border border-gray-200 rounded-xl outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
        />
      </div>

      <!-- Auto Probe Toggle -->
      <label class="flex items-start gap-3 p-3 bg-brand-50/50 rounded-xl border border-brand-200/60 cursor-pointer select-none">
        <input
          v-model="autoProbe"
          type="checkbox"
          class="mt-0.5 rounded text-brand-600 focus:ring-brand-500 focus:ring-offset-0"
        >
        <div class="flex-1 text-xs">
          <div class="font-semibold text-brand-900 flex items-center gap-1.5">
            <Sparkles class="w-3.5 h-3.5 text-brand-600" />
            <span>自动无限向下探活多登录账号 (u/0, u/1, u/2... 一键批量分化导入)</span>
          </div>
          <div class="text-brand-700/80 mt-0.5">
            单份包含多个 Google 登录账号的 Cookie，将自动探测所有有效子账号并自动分化建档。
          </div>
        </div>
      </label>

      <!-- Account Name / Email Inputs -->
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
            账号名称前缀 <span class="text-gray-400 font-normal">(可选)</span>
          </label>
          <input
            v-model="namePrefix"
            type="text"
            placeholder="Google Account"
            class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
          >
        </div>

        <div
          v-if="!autoProbe"
          class="space-y-1.5"
        >
          <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
            邮箱地址 <span class="text-gray-400 font-normal">(可选)</span>
          </label>
          <input
            v-model="singleEmail"
            type="email"
            placeholder="user@gmail.com"
            class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
          >
        </div>
      </div>
    </div>

    <template #footer>
      <Button
        variant="ghost"
        size="sm"
        :disabled="accountsStore.importing"
        @click="emit('update:modelValue', false)"
      >
        取消
      </Button>
      <Button
        variant="primary"
        size="sm"
        :loading="accountsStore.importing"
        :disabled="!cookieInput.trim()"
        @click="handleSubmit"
      >
        <template #default>
          <span v-if="autoProbe">⚡ 探活并批量导入</span>
          <span v-else>导入 Cookie</span>
        </template>
      </Button>
    </template>
  </Modal>
</template>
