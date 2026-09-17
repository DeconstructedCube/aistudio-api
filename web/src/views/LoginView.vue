<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth.ts'
import { useToastStore } from '@/stores/toast.ts'
import Button from '@/components/ui/Button.vue'
import ToastContainer from '@/components/ui/ToastContainer.vue'
import { Lock, Eye, EyeOff, ShieldCheck, AlertCircle } from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const toast = useToastStore()

const inputToken = ref('')
const showPassword = ref(false)
const loading = ref(false)
const errorMsg = ref('')

onMounted(async () => {
  // 如果已存在 token，且通过验证，自动跳转
  if (authStore.token) {
    inputToken.value = authStore.token
    loading.value = true
    const ok = await authStore.login(authStore.token)
    loading.value = false
    if (ok) {
      const redirect = (route.query.redirect as string) || '/'
      router.replace(redirect)
    }
  }
})

async function handleLogin() {
  const token = inputToken.value.trim()
  if (!token) {
    errorMsg.value = '请输入控制台访问密码'
    return
  }

  errorMsg.value = ''
  loading.value = true

  try {
    const ok = await authStore.login(token)
    if (ok) {
      toast.success('登录成功')
      const redirect = (route.query.redirect as string) || '/'
      router.replace(redirect)
    } else {
      errorMsg.value = '密码错误或无权访问'
      toast.error('密码校验失败，请检查后重试')
    }
  } catch {
    errorMsg.value = '网络请求异常，请检查服务状态'
    toast.error('登录异常')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-gray-100/90 flex flex-col justify-center items-center p-4 sm:p-6">
    <ToastContainer />

    <div class="w-full max-w-md bg-white rounded-3xl shadow-xl border border-gray-100 p-8 sm:p-10 transition-all">
      <!-- Header / Logo -->
      <div class="text-center mb-8">
        <div class="w-14 h-14 bg-brand-500 text-white rounded-2xl flex items-center justify-center font-bold text-2xl mx-auto mb-4 shadow-md shadow-brand-500/20">
          AI
        </div>
        <h1 class="text-2xl font-extrabold text-gray-900 tracking-tight">
          AI Studio Proxy
        </h1>
        <p class="text-xs text-gray-400 mt-1.5">
          请输入服务端配置的控制台密码 (AISTUDIO_WEB_PASSWORD)
        </p>
      </div>

      <!-- Error Alert -->
      <div
        v-if="errorMsg"
        class="mb-6 p-3.5 bg-rose-50 border border-rose-200/80 rounded-xl text-xs font-medium text-rose-800 flex items-start gap-2.5 animate-shake"
      >
        <AlertCircle class="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
        <span>{{ errorMsg }}</span>
      </div>

      <!-- Form -->
      <form
        class="space-y-5"
        @submit.prevent="handleLogin"
      >
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider">
            控制台访问密码 (Web Password)
          </label>
          <div class="relative">
            <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-gray-400">
              <Lock class="w-4 h-4" />
            </div>
            <input
              v-model="inputToken"
              :type="showPassword ? 'text' : 'password'"
              placeholder="输入 AISTUDIO_WEB_PASSWORD"
              autocomplete="current-password"
              autofocus
              class="w-full pl-10 pr-10 py-3 bg-gray-50 border border-gray-200 rounded-xl text-sm outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all font-mono"
            >
            <button
              type="button"
              class="absolute inset-y-0 right-0 pr-3.5 flex items-center text-gray-400 hover:text-gray-600 transition-colors"
              @click="showPassword = !showPassword"
            >
              <EyeOff
                v-if="showPassword"
                class="w-4 h-4"
              />
              <Eye
                v-else
                class="w-4 h-4"
              />
            </button>
          </div>
        </div>

        <Button
          type="submit"
          variant="primary"
          size="lg"
          class="w-full justify-center text-sm font-semibold shadow-md shadow-brand-500/10"
          :loading="loading"
          :disabled="!inputToken.trim()"
        >
          <ShieldCheck class="w-4 h-4" />
          <span>验证并登录控制台</span>
        </Button>
      </form>

      <!-- Footer Info -->
      <div class="mt-8 text-center text-xs text-gray-400 border-t border-gray-100 pt-6">
        <p>控制台访问密码由服务端环境变量 AISTUDIO_WEB_PASSWORD 设置</p>
      </div>
    </div>
  </div>
</template>
