<template>
  <div class="reply-header">
    <div class="avatar">
      <span class="material-symbols-rounded avatar-icon">smart_toy</span>
    </div>
    <span class="agent-name">{{ reply.agentName }}</span>
    <div v-if="reply.timer.ttft.value != null || reply.timer.total.state !== 'idle'" class="timers">
      <span v-if="reply.timer.ttft.value != null" class="timer-item">
        TTFT <span class="timer-value">{{ reply.timer.ttft.value }}s</span>
      </span>
      <span v-if="hasTotal" class="timer-sep">|</span>
      <span v-if="hasTotal" class="timer-item">
        Total
        <span v-if="reply.timer.total.state === 'running'" class="timer-value running">
          {{ elapsed.toFixed(1) }}s
        </span>
        <span v-else class="timer-value">{{ reply.timer.total.value }}s</span>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onUnmounted, watch } from 'vue'
import type { ReplyState } from '@/types'

const props = defineProps<{ reply: ReplyState }>()
const elapsed = ref(0)
let rafId = 0
let startTime = 0

const hasTotal = computed(() =>
  props.reply.timer.total.state === 'running' || props.reply.timer.total.value != null
)

function tick() {
  elapsed.value = (performance.now() - startTime) / 1000
  rafId = requestAnimationFrame(tick)
}

watch(() => props.reply.timer.total.state, (state) => {
  if (state === 'running' && !rafId) {
    startTime = performance.now()
    rafId = requestAnimationFrame(tick)
  }
  if (state === 'done' && rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
    if (props.reply.timer.total.value != null) {
      elapsed.value = props.reply.timer.total.value
    }
  }
}, { immediate: true })

onUnmounted(() => { if (rafId) cancelAnimationFrame(rafId) })
</script>

<style scoped>
.reply-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, #6366f1, #38bdcc);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.avatar-icon {
  font-size: 20px;
  color: white;
  font-variation-settings: 'FILL' 1;
}
.agent-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-bright);
  letter-spacing: -0.01em;
}
.timers {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  font-size: 12px;
  font-family: 'JetBrains Mono', monospace;
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}
.timer-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}
.timer-value {
  color: var(--success);
  font-weight: 600;
}
.timer-value.running { animation: pulse 1.5s infinite; }
.timer-sep {
  color: var(--text-ghost);
}
</style>
