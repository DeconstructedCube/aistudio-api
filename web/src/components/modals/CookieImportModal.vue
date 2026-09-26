<script setup lang="ts">
import { ref } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import { Sparkles, FileText, Upload, FolderOpen, CheckCircle2 } from 'lucide-vue-next'
defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
}>()

const accountsStore = useAccountsStore()

const activeTab = ref<'text' | 'file'>('text')

// 文本导入状态
const cookieInput = ref('')
const namePrefix = ref('')
const singleEmail = ref('')
const autoProbe = ref(true)

// 文件导入状态
const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFileName = ref('')
const selectedFileSize = ref('')
const selectedFileContent = ref('')
const localFilePath = ref('')
const detectedCount = ref<number | null>(null)

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  selectedFileName.value = file.name
  selectedFileSize.value = `${(file.size / 1024).toFixed(1)} KB`

  const reader = new FileReader()
  reader.onload = (e) => {
    const text = (e.target?.result as string) || ''
    selectedFileContent.value = text
    try {
      const parsed = JSON.parse(text)
      const list = Array.isArray(parsed) ? parsed : (parsed.accounts || parsed.profiles || [])
      detectedCount.value = Array.isArray(list) ? list.length : 0
    } catch {
      detectedCount.value = null
    }
  }
  reader.readAsText(file)
}

function clearSelectedFile() {
  selectedFileName.value = ''
  selectedFileSize.value = ''
  selectedFileContent.value = ''
  detectedCount.value = null
  if (fileInputRef.value) {
    fileInputRef.value.value = ''
  }
}
async function handleSubmit() {
  if (activeTab.value === 'file') {
    const content = selectedFileContent.value.trim()
    const path = localFilePath.value.trim()
    if (!content && !path) return

    const ok = await accountsStore.importBundle({
      content: content || undefined,
      file_path: path || undefined,
    })
    if (ok) {
      clearSelectedFile()
      localFilePath.value = ''
      emit('update:modelValue', false)
    }
    return
  }

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
      <!-- Tab Switcher -->
      <div class="flex items-center p-1 bg-gray-100 rounded-xl">
        <button
          type="button"
          class="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all"
          :class="activeTab === 'text' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'"
          @click="activeTab = 'text'"
        >
          <FileText class="w-3.5 h-3.5" />
          <span>文本粘贴导入</span>
        </button>
        <button
          type="button"
          class="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold rounded-lg transition-all"
          :class="activeTab === 'file' ? 'bg-white text-brand-600 shadow-sm' : 'text-gray-500 hover:text-gray-900'"
          @click="activeTab = 'file'"
        >
          <Upload class="w-3.5 h-3.5" />
          <span>文件批量导入</span>
        </button>
      </div>

      <!-- Mode: File Import -->
      <div
        v-if="activeTab === 'file'"
        class="space-y-4"
      >
        <!-- File Picker Drag & Drop Box -->
        <div
          class="border-2 border-dashed border-gray-200 hover:border-brand-400 bg-gray-50/70 hover:bg-brand-50/30 rounded-2xl p-5 text-center transition-all cursor-pointer relative"
          @click="fileInputRef?.click()"
        >
          <input
            ref="fileInputRef"
            type="file"
            accept=".json,application/json"
            class="hidden"
            @change="onFileChange"
          >
          <div
            v-if="!selectedFileName"
            class="flex flex-col items-center gap-2"
          >
            <div class="w-10 h-10 rounded-full bg-brand-100 text-brand-600 flex items-center justify-center">
              <Upload class="w-5 h-5" />
            </div>
            <div>
              <p class="text-xs font-semibold text-gray-800">
                点击选择凭据包文件 (.json)
              </p>
              <p class="text-[11px] text-gray-400 mt-0.5">
                支持 AIStudio 提取器导出的 aistudio_accounts.json
              </p>
            </div>
          </div>

          <div
            v-else
            class="flex items-center justify-between bg-white p-3 rounded-xl border border-brand-200"
          >
            <div class="flex items-center gap-2.5 text-left">
              <div class="w-8 h-8 rounded-lg bg-emerald-100 text-emerald-600 flex items-center justify-center">
                <CheckCircle2 class="w-4 h-4" />
              </div>
              <div>
                <p class="text-xs font-semibold text-gray-800">
                  {{ selectedFileName }}
                </p>
                <p class="text-[11px] text-gray-400">
                  大小: {{ selectedFileSize }}
                  <span
                    v-if="detectedCount !== null"
                    class="ml-1 text-brand-600 font-medium"
                  >· 识别到 {{ detectedCount }} 个账号</span>
                </p>
              </div>
            </div>
            <button
              type="button"
              class="text-xs text-gray-400 hover:text-rose-500 px-2 py-1"
              @click.stop="clearSelectedFile"
            >
              移除
            </button>
          </div>
        </div>

        <!-- Alternative: Local File Path on Server -->
        <div class="space-y-1.5">
          <label class="block text-xs font-semibold text-gray-700 uppercase tracking-wider flex items-center justify-between">
            <span class="flex items-center gap-1">
              <FolderOpen class="w-3.5 h-3.5 text-gray-500" />
              <span>或直接填入本地文件绝对路径</span>
            </span>
            <span class="text-[11px] text-gray-400 font-normal">免去手机上传</span>
          </label>
          <input
            v-model="localFilePath"
            type="text"
            placeholder="/sdcard/Download/aistudio_accounts.json"
            class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 transition-all"
          >
        </div>
      </div>

      <!-- Mode: Text Paste -->
      <div
        v-else
        class="space-y-4"
      >
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
              <span>递归探测多身份登录账号 (u/0, u/1, u/2...)</span>
            </div>
            <div class="text-brand-700/80 mt-0.5">
              针对包含多登录身份的凭据，自动探测全部有效子账号并建立独立配置。
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
        :disabled="activeTab === 'file' ? (!selectedFileContent && !localFilePath.trim()) : !cookieInput.trim()"
        @click="handleSubmit"
      >
        <template #default>
          <span v-if="activeTab === 'file'">确认批量导入</span>
          <span v-else-if="autoProbe">探测并导入</span>
          <span v-else>导入 Cookie</span>
        </template>
      </Button>
    </template>
  </Modal>
</template>
