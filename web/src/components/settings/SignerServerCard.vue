<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { systemApi } from '@/api/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import type { SignerServerItem } from '@/types/system.ts'
import {
  KeyRound,
  CheckCircle2,
  XCircle,
  Plus,
  Trash2,
  Edit2,
  Activity,
  Check,
  Loader2,
} from 'lucide-vue-next'

const emit = defineEmits<{
  (e: 'switched'): void
}>()

const toast = useToastStore()

const loading = ref(false)
const switching = ref<string | null>(null)
const testingId = ref<string | null>(null)
const testResults = ref<Record<string, { ok: boolean; ms?: number; err?: string }>>({})

const activeServerId = ref('local_spidermonkey')
const servers = ref<SignerServerItem[]>([])

const showModal = ref(false)
const isEditing = ref(false)
const saving = ref(false)

const form = ref<Partial<SignerServerItem>>({
  id: '',
  name: '',
  description: '',
  url: '',
  user_agent: '',
  headers: {},
})
const customHeadersText = ref('')

async function fetchServers() {
  loading.value = true
  try {
    const res = await systemApi.getSignerServers()
    activeServerId.value = res.active_server
    servers.value = res.servers
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '获取签名服务器列表失败'
    toast.error(msg)
  } finally {
    loading.value = false
  }
}

async function handleSwitch(serverId: string) {
  if (serverId === activeServerId.value) return
  switching.value = serverId
  try {
    const res = await systemApi.switchSignerServer(serverId)
    if (res.ok) {
      activeServerId.value = serverId
      servers.value = servers.value.map(s => ({
        ...s,
        is_active: s.id === serverId,
      }))
      toast.success(`已手动切换至签名服务器: ${res.active_server.name}`)
      emit('switched')
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '切换签名服务器失败'
    toast.error(msg)
  } finally {
    switching.value = null
  }
}

async function handleTest(server: SignerServerItem) {
  testingId.value = server.id
  try {
    const res = await systemApi.testSignerServer(server.url)
    if (res.ok) {
      testResults.value[server.id] = { ok: true, ms: res.elapsed_ms }
      toast.success(`${server.name}: 连通成功 (${res.elapsed_ms}ms)`)
    } else {
      const errMsg = res.error || `HTTP ${res.status_code || '异常'}`
      testResults.value[server.id] = { ok: false, ms: res.elapsed_ms, err: errMsg }
      toast.error(`${server.name}: 连通失败 - ${errMsg}`)
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '请求异常'
    testResults.value[server.id] = { ok: false, err: msg }
    toast.error(`${server.name}: 连通异常 - ${msg}`)
  } finally {
    testingId.value = null
  }
}

function openCreateModal() {
  isEditing.value = false
  form.value = {
    id: '',
    name: '',
    description: '',
    url: 'http://127.0.0.1:8081',
    user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0',
    headers: { 'X-User-Agent': 'grpc-web-javascript/0.1' },
  }
  customHeadersText.value = '{"X-User-Agent": "grpc-web-javascript/0.1"}'
  showModal.value = true
}

function openEditModal(server: SignerServerItem) {
  isEditing.value = true
  form.value = { ...server }
  customHeadersText.value = server.headers ? JSON.stringify(server.headers, null, 2) : '{}'
  showModal.value = true
}

async function handleSaveServer() {
  if (!form.value.name?.trim() || !form.value.url?.trim()) {
    toast.error('名称与服务地址为必填项')
    return
  }

  let parsedHeaders: Record<string, string> = {}
  if (customHeadersText.value.trim()) {
    try {
      parsedHeaders = JSON.parse(customHeadersText.value.trim())
    } catch {
      toast.error('自定义请求头必须是合法的 JSON 键值对')
      return
    }
  }

  saving.value = true
  try {
    const payload: Partial<SignerServerItem> = {
      id: form.value.id || undefined,
      name: form.value.name.trim(),
      description: form.value.description?.trim() || '',
      url: form.value.url.trim(),
      user_agent: form.value.user_agent?.trim() || '',
      headers: parsedHeaders,
    }
    await systemApi.upsertSignerServer(payload)
    toast.success('签名服务器配置已保存')
    showModal.value = false
    await fetchServers()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '保存失败'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}

async function handleDeleteServer(server: SignerServerItem) {
  if (server.id === activeServerId.value) {
    toast.error('无法删除当前处于激活状态的签名服务器')
    return
  }
  if (!confirm(`确认删除签名服务器 "${server.name}" 吗？`)) return

  try {
    await systemApi.deleteSignerServer(server.id)
    toast.success('已删除签名服务器')
    await fetchServers()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '删除失败'
    toast.error(msg)
  }
}

onMounted(() => {
  void fetchServers()
})
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 shadow-xs">
    <!-- Header -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-gray-100">
      <div class="space-y-1">
        <div class="flex items-center gap-2">
          <KeyRound class="w-5 h-5 text-indigo-600" />
          <h3 class="text-base font-bold text-gray-900 tracking-tight">
            WAA 证明签名服务与特征绑定
          </h3>
          <span class="px-2 py-0.5 text-[11px] font-semibold bg-gray-100 text-gray-700 rounded-md">
            纯手动切换
          </span>
        </div>
        <p class="text-xs text-gray-500">
          管理与切换 WAA 签名服务节点。各签名节点底层环境（SpiderMonkey / NodeVM 等）不同，已绑定各自特征 User-Agent，切换时外层请求头将自动精确对齐，防 403 鉴权拦截。
        </p>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg transition-colors cursor-pointer self-start sm:self-auto shrink-0"
        @click="openCreateModal"
      >
        <Plus class="w-3.5 h-3.5" />
        <span>添加服务器</span>
      </button>
    </div>

    <!-- Server List -->
    <div class="mt-4 space-y-3">
      <div
        v-for="server in servers"
        :key="server.id"
        class="p-4 rounded-xl border transition-all"
        :class="[
          server.is_active
            ? 'border-indigo-300 bg-indigo-50/30 ring-1 ring-indigo-200/50'
            : 'border-gray-200 bg-white hover:border-gray-300'
        ]"
      >
        <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <!-- Info -->
          <div class="space-y-1.5 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-sm font-semibold text-gray-900">
                {{ server.name }}
              </span>
              <span
                v-if="server.is_active"
                class="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium bg-emerald-100 text-emerald-800 rounded-md"
              >
                <Check class="w-3 h-3" />
                当前激活
              </span>
              <span class="text-xs font-mono text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">
                {{ server.url }}
              </span>
              <!-- Test Result Indicator -->
              <span
                v-if="testResults[server.id]"
                class="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded"
                :class="testResults[server.id].ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'"
              >
                <CheckCircle2
                  v-if="testResults[server.id].ok"
                  class="w-3 h-3"
                />
                <XCircle
                  v-else
                  class="w-3 h-3"
                />
                {{ testResults[server.id].ok ? `${testResults[server.id].ms}ms` : '失败' }}
              </span>
            </div>

            <p
              v-if="server.description"
              class="text-xs text-gray-500"
            >
              {{ server.description }}
            </p>

            <!-- Bound User-Agent & Features -->
            <div class="text-[11px] text-gray-600 bg-gray-50/80 p-2 rounded-lg border border-gray-100 space-y-1 font-mono break-all">
              <div>
                <span class="text-gray-400 select-none">User-Agent: </span>
                <span>{{ server.user_agent || '未设置 (默认跟随全局)' }}</span>
              </div>
              <div v-if="server.headers && Object.keys(server.headers).length > 0">
                <span class="text-gray-400 select-none">绑定头部: </span>
                <span>{{ Object.entries(server.headers).map(([k, v]) => `${k}: ${v}`).join('; ') }}</span>
              </div>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex items-center gap-2 shrink-0 self-end md:self-center">
            <button
              type="button"
              class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-gray-600 hover:text-gray-900 bg-gray-100 hover:bg-gray-200/80 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
              :disabled="testingId === server.id"
              @click="handleTest(server)"
            >
              <Loader2
                v-if="testingId === server.id"
                class="w-3.5 h-3.5 animate-spin"
              />
              <Activity
                v-else
                class="w-3.5 h-3.5 text-gray-500"
              />
              <span>测试连接</span>
            </button>

            <button
              v-if="!server.is_active"
              type="button"
              class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-xs transition-colors cursor-pointer disabled:opacity-50"
              :disabled="switching === server.id"
              @click="handleSwitch(server.id)"
            >
              <Loader2
                v-if="switching === server.id"
                class="w-3.5 h-3.5 animate-spin"
              />
              <span>手动切换</span>
            </button>

            <button
              type="button"
              class="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
              title="编辑"
              @click="openEditModal(server)"
            >
              <Edit2 class="w-3.5 h-3.5" />
            </button>

            <button
              v-if="!server.is_active"
              type="button"
              class="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors cursor-pointer"
              title="删除"
              @click="handleDeleteServer(server)"
            >
              <Trash2 class="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Edit/Add Modal -->
    <div
      v-if="showModal"
      class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs"
    >
      <div class="bg-white rounded-2xl max-w-lg w-full p-5 shadow-xl border border-gray-100 space-y-4">
        <div class="flex items-center justify-between pb-3 border-b border-gray-100">
          <h4 class="text-sm font-bold text-gray-900">
            {{ isEditing ? '编辑签名服务器' : '添加签名服务器' }}
          </h4>
          <button
            type="button"
            class="text-gray-400 hover:text-gray-600 cursor-pointer"
            @click="showModal = false"
          >
            ✕
          </button>
        </div>

        <div class="space-y-3 text-xs">
          <div>
            <label class="block font-medium text-gray-700 mb-1">服务器标识 (ID)</label>
            <input
              v-model="form.id"
              type="text"
              :disabled="isEditing"
              placeholder="如 local_spidermonkey 或 custom_nodevm"
              class="w-full px-3 py-2 border border-gray-200 rounded-lg font-mono focus:outline-hidden focus:border-indigo-500 disabled:bg-gray-50"
            >
          </div>

          <div>
            <label class="block font-medium text-gray-700 mb-1">名称 *</label>
            <input
              v-model="form.name"
              type="text"
              placeholder="如 本地 Go 签名服务"
              class="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-hidden focus:border-indigo-500"
            >
          </div>

          <div>
            <label class="block font-medium text-gray-700 mb-1">服务地址 (URL) *</label>
            <input
              v-model="form.url"
              type="text"
              placeholder="http://127.0.0.1:8081"
              class="w-full px-3 py-2 border border-gray-200 rounded-lg font-mono focus:outline-hidden focus:border-indigo-500"
            >
          </div>

          <div>
            <label class="block font-medium text-gray-700 mb-1">绑定的特征 User-Agent</label>
            <textarea
              v-model="form.user_agent"
              rows="2"
              placeholder="在此处填写该签名机底层模拟的真实 User-Agent"
              class="w-full px-3 py-2 border border-gray-200 rounded-lg font-mono text-[11px] focus:outline-hidden focus:border-indigo-500"
            />
            <p class="text-[11px] text-gray-400 mt-0.5">
              提示：SpiderMonkey 152 对应 Firefox 152 UA；NodeVM / Playwright 对应其模拟的 Chrome UA。
            </p>
          </div>

          <div>
            <label class="block font-medium text-gray-700 mb-1">自定义特征请求头 (JSON 格式)</label>
            <textarea
              v-model="customHeadersText"
              rows="3"
              :placeholder="JSON.stringify({ 'X-User-Agent': 'grpc-web-javascript/0.1' })"
              class="w-full px-3 py-2 border border-gray-200 rounded-lg font-mono text-[11px] focus:outline-hidden focus:border-indigo-500"
            />
          </div>

          <div>
            <label class="block font-medium text-gray-700 mb-1">描述说明</label>
            <input
              v-model="form.description"
              type="text"
              placeholder="选填，关于该签名节点的补充说明"
              class="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-hidden focus:border-indigo-500"
            >
          </div>
        </div>

        <div class="flex items-center justify-end gap-2 pt-3 border-t border-gray-100">
          <button
            type="button"
            class="px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-100 rounded-lg cursor-pointer"
            @click="showModal = false"
          >
            取消
          </button>
          <button
            type="button"
            class="px-4 py-1.5 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-xs cursor-pointer disabled:opacity-50"
            :disabled="saving"
            @click="handleSaveServer"
          >
            {{ saving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
