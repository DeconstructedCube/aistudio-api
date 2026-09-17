<script setup lang="ts">
import { Code2, Terminal, Copy, Check } from 'lucide-vue-next'
import { useClipboard } from '@/composables/useClipboard.ts'

const { copied, copy } = useClipboard()

const codeSnippets = [
  {
    title: 'cURL',
    lang: 'bash',
    code: `curl http://localhost:8080/v1beta/models/gemini-3.8-flash:generateContent \\
  -H "x-goog-api-key: your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}'`,
  },
  {
    title: 'Python SDK',
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
    contents="Hello"
)
print(response.text)`,
  },
]

function handleCopy(code: string, index: number) {
  void copy(code, index, '已复制调用示例到剪贴板')
}
</script>

<template>
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
            @click="handleCopy(snippet.code, idx)"
          >
            <Check
              v-if="copied === idx"
              class="w-3 h-3 text-emerald-400"
            />
            <Copy
              v-else
              class="w-3 h-3"
            />
            <span>{{ copied === idx ? '已复制' : '复制' }}</span>
          </button>
        </div>

        <pre class="mt-3 text-xs font-mono overflow-x-auto text-gray-200 leading-relaxed"><code>{{ snippet.code }}</code></pre>
      </div>
    </div>
  </div>
</template>
