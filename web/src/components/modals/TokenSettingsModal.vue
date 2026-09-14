<script setup lang="ts">
import { ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth.ts'
import { useToastStore } from '@/stores/toast.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import { KeyRound, Trash2 } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
}>()

const authStore = useAuthStore()
const toast = useToastStore()

const inputToken = ref(authStore.token)

watch(
  () => props.modelValue,
  (val) => {
    if (val) {
      inputToken.value = authStore.token
    }
  }
)

function handleSave() {
  authStore.updateToken(inputToken.value)
  toast.success('管理控制台访问密码已保存')
  emit('update:modelValue', false)
}

function handleClearCache() {
  if (confirm('确定要清除本地保存的 Token 和缓存吗？')) {
    authStore.logout()
    inputToken.value = ''
    toast.info('本地缓存已清除')
    emit('update:modelValue', false)
    window.location.reload()
  }
}
</script>

<template>
  <Modal
    :model-value="modelValue"
    title="控制台管理密码设置"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <div class="flex items-start gap-3 p-3 bg-brand-50/60 rounded-xl border border-brand-100 text-xs text-brand-900 leading-relaxed">
        <KeyRound class="w-4 h-4 text-brand-600 shrink-0 mt-0.5" />
        <div>
          当服务端配置了 <code>AISTUDIO_WEB_PASSWORD</code> 环境变量时，控制台请求会自动附带该密码进行管理接口鉴权。
        </div>
      </div>

      <div class="space-y-1.5">
        <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
          控制台管理密码 (Web Password)
        </label>
        <input
          v-model="inputToken"
          type="password"
          placeholder="留空则不发送 Authorization 请求头"
          class="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-sm outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
        >
      </div>

      <div class="pt-2 border-t border-gray-100">
        <button
          type="button"
          class="w-full flex items-center justify-center gap-2 py-2 text-xs font-medium text-rose-600 hover:text-rose-700 hover:bg-rose-50 rounded-xl transition-colors cursor-pointer"
          @click="handleClearCache"
        >
          <Trash2 class="w-3.5 h-3.5" />
          <span>清除本地 Token 与缓存</span>
        </button>
      </div>
    </div>

    <template #footer>
      <Button
        variant="ghost"
        size="sm"
        @click="emit('update:modelValue', false)"
      >
        取消
      </Button>
      <Button
        variant="primary"
        size="sm"
        @click="handleSave"
      >
        保存配置
      </Button>
    </template>
  </Modal>
</template>
