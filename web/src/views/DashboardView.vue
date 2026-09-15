<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useAccountsStore } from '@/stores/accounts.ts'
import { useSystemStore } from '@/stores/system.ts'
import { useToastStore } from '@/stores/toast.ts'
import StatCard from '@/components/ui/StatCard.vue'
import ModelStatsTable from '@/components/dashboard/ModelStatsTable.vue'
import {
  UserCheck,
  Users,
  Activity,
  AlertTriangle,
  Code2,
  Terminal,
  Copy,
  Check,
} from 'lucide-vue-next'

const accountsStore = useAccountsStore()
const systemStore = useSystemStore()
const toast = useToastStore()

const copiedIndex = ref<number | null>(null)

const activeAccountDisplay = computed(() => {
  const acc = accountsStore.activeAccount
  if (!acc) return '暂无激活账号'
  const sub = acc.auth_user !== undefined ? ` (u/${acc.auth_user})` : ''
  return (acc.email || acc.name || acc.id) + sub
})

const totalAccountsCount = computed(() => accountsStore.accounts.length)

const availableAccountsCount = computed(() => {
  if (!systemStore.rotation?.accounts) return totalAccountsCount.value
  return Object.values(systemStore.rotation.accounts).filter(
    (a) => a.is_available !== false
  ).length
})

async function loadData() {
  await Promise.all([
    accountsStore.fetchAll(),
    systemStore.fetchStats(),
    systemStore.fetchRotation(),
  ])
}

onMounted(() => {
  loadData()
})

const codeSnippets = [
  {
    title: 'cURL 调用示例',
    lang: 'bash',
    code: `curl http://localhost:8080/v1beta/models/gemini-3.8-flash:generateContent \\
  -H "x-goog-api-key: your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello Gemini"}]}]}'`,
  },
  {
    title: 'Python SDK 调用示例',
    lang: 'python',
    code: `from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="Hello from aistudio-api"
)
print(response.text)`,
  },
]

function copyCode(code: string, index: number) {
  navigator.clipboard.writeText(code)
  copiedIndex.value = index
  toast.success('已复制调用示例到剪贴板')
  setTimeout(() => {
    if (copiedIndex.value === index) {
      copiedIndex.value = null
    }
  }, 2000)
}
</script>

<template>
  <div class="space-y-6">
    <!-- Stat Cards Grid -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard
        label="当前激活账号"
        :value="activeAccountDisplay"
        :color="accountsStore.activeAccount ? 'green' : 'default'"
      >
        <template #icon>
          <UserCheck class="w-4 h-4 text-emerald-600" />
        </template>
      </StatCard>

      <StatCard
        label="可用账号"
        :value="`${availableAccountsCount} / ${totalAccountsCount}`"
        color="default"
      >
        <template #icon>
          <Users class="w-4 h-4 text-brand-600" />
        </template>
      </StatCard>

      <StatCard
        label="总请求数"
        :value="systemStore.totalRequests"
        :sub-value="`成功 ${systemStore.totalSuccess}`"
        color="default"
      >
        <template #icon>
          <Activity class="w-4 h-4 text-blue-600" />
        </template>
      </StatCard>

      <StatCard
        label="429 配额耗尽次数"
        :value="systemStore.totalRateLimited"
        :color="systemStore.totalRateLimited > 0 ? 'amber' : 'default'"
      >
        <template #icon>
          <AlertTriangle class="w-4 h-4 text-amber-500" />
        </template>
      </StatCard>
    </div>

    <!-- Model Statistics Table -->
    <ModelStatsTable
      :stats="systemStore.stats?.models || {}"
      :loading="systemStore.loading"
    />

    <!-- Quick API Reference Snippets -->
    <div class="bg-white border border-gray-200/80 rounded-2xl p-6 shadow-xs space-y-4">
      <div class="flex items-center gap-2 border-b border-gray-100 pb-3">
        <Code2 class="w-4 h-4 text-brand-600" />
        <h3 class="font-semibold text-gray-900 text-sm">
          快速调用示例
        </h3>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div
          v-for="(snippet, idx) in codeSnippets"
          :key="idx"
          class="bg-gray-900 text-gray-100 rounded-xl p-4 flex flex-col justify-between overflow-hidden shadow-xs"
        >
          <div class="flex items-center justify-between pb-2 border-b border-gray-800 text-xs font-medium text-gray-400">
            <div class="flex items-center gap-1.5">
              <Terminal class="w-3.5 h-3.5 text-brand-400" />
              <span>{{ snippet.title }}</span>
            </div>
            <button
              type="button"
              class="flex items-center gap-1 text-[11px] text-gray-400 hover:text-white px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 transition-colors cursor-pointer"
              @click="copyCode(snippet.code, idx)"
            >
              <Check
                v-if="copiedIndex === idx"
                class="w-3 h-3 text-emerald-400"
              />
              <Copy
                v-else
                class="w-3 h-3"
              />
              <span>{{ copiedIndex === idx ? '已复制' : '复制' }}</span>
            </button>
          </div>

          <pre class="mt-3 text-xs font-mono overflow-x-auto text-gray-200 leading-relaxed"><code>{{ snippet.code }}</code></pre>
        </div>
      </div>
    </div>
  </div>
</template>
