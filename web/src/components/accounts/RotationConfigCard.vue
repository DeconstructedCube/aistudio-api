<script setup lang="ts">
import { ref, watch } from 'vue'
import { useSystemStore } from '@/stores/system.ts'
import type { RotationMode } from '@/types'
import Button from '@/components/ui/Button.vue'
import { Sliders, ArrowRightLeft, Check } from 'lucide-vue-next'

const systemStore = useSystemStore()

const selectedMode = ref<RotationMode>(systemStore.rotationMode)
const cooldownInput = ref<number>(systemStore.cooldownSeconds)

watch(
  () => systemStore.rotationMode,
  (val) => {
    selectedMode.value = val
  }
)

watch(
  () => systemStore.cooldownSeconds,
  (val) => {
    cooldownInput.value = val
  }
)

const modeOptions: Array<{ value: RotationMode; label: string; desc: string }> = [
  {
    value: 'sticky',
    label: '保持固定 (Sticky)',
    desc: '优先复用当前活跃账号，直到遇到 429 限流才自动切换',
  },
  {
    value: 'round_robin',
    label: '顺序轮询 (Round-Robin)',
    desc: '按账号顺序依次轮流调度，跳过处于 429 冷却中的账号',
  },
  {
    value: 'lru',
    label: '最近最少使用 (LRU)',
    desc: '优先选取空闲时间最长的账号，使请求在各账号间均匀分布',
  },
  {
    value: 'least_rl',
    label: '最小限流优先 (Least-RL)',
    desc: '优先选取 429 限流次数最少的健康账号，最大化服务稳定性与吞吐',
  },
]

async function handleSave() {
  await systemStore.saveRotation(selectedMode.value, cooldownInput.value)
}

async function handleForceNext() {
  await systemStore.forceNextAccount()
}
</script>

<template>
  <div class="bg-white border border-gray-200/80 rounded-2xl p-5 sm:p-6 shadow-xs space-y-5">
    <div class="flex items-center justify-between border-b border-gray-100 pb-4">
      <div class="flex items-center gap-2">
        <Sliders class="w-4 h-4 text-brand-600" />
        <h3 class="font-semibold text-gray-900 text-sm">
          多账号轮询策略设置
        </h3>
      </div>

      <div class="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          :loading="systemStore.switchingNext"
          @click="handleForceNext"
        >
          <ArrowRightLeft class="w-3.5 h-3.5" />
          <span>强制切换至下一账号</span>
        </Button>
      </div>
    </div>

    <!-- Mode Selector Grid -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
      <div
        v-for="opt in modeOptions"
        :key="opt.value"
        class="p-4 rounded-xl border-2 transition-all cursor-pointer flex flex-col justify-between"
        :class="[
          selectedMode === opt.value
            ? 'border-brand-500 bg-brand-50/40 text-brand-950 shadow-xs'
            : 'border-gray-100 hover:border-gray-200 bg-gray-50/40 text-gray-700',
        ]"
        @click="selectedMode = opt.value"
      >
        <div>
          <div class="flex items-center justify-between font-bold text-xs">
            <span>{{ opt.label }}</span>
            <span
              v-if="selectedMode === opt.value"
              class="w-4 h-4 rounded-full bg-brand-500 text-white flex items-center justify-center"
            >
              <Check class="w-2.5 h-2.5 stroke-[3]" />
            </span>
          </div>
          <p class="text-[11px] text-gray-500 mt-1.5 leading-relaxed">
            {{ opt.desc }}
          </p>
        </div>
      </div>
    </div>

    <!-- Cooldown & Actions -->
    <div class="pt-2 flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-t border-gray-100">
      <div class="flex items-center gap-3">
        <label class="text-xs font-semibold text-gray-700 whitespace-nowrap">
          429 限流冷却时间 (秒)
        </label>
        <input
          v-model.number="cooldownInput"
          type="number"
          min="0"
          max="86400"
          class="w-24 px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono outline-none focus:bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
        >
        <span class="text-[11px] text-gray-400">
          (美西时间 0:00 每日重置限额，同时单模型 429 触发局部冷却)
        </span>
      </div>

      <div class="flex items-center gap-2 self-end sm:self-auto">
        <Button
          variant="primary"
          size="sm"
          :loading="systemStore.savingRotation"
          @click="handleSave"
        >
          <span>保存策略设置</span>
        </Button>
      </div>
    </div>
  </div>
</template>
