<template>
  <div class="reply-header">
    <div class="avatar">🤖</div>
    <span class="agent-name">{{ reply.agentName }}</span>
    <span v-if="reply.timer.ttft.value != null" class="timer ttft">
      TTFT: {{ reply.timer.ttft.value }}s
    </span>
    <span v-if="reply.timer.total.state === 'running'" class="timer total pulse">
      {{ elapsed.toFixed(1) }}s
    </span>
    <span v-else-if="reply.timer.total.value != null" class="timer total">
      {{ reply.timer.total.value }}s
    </span>
  </div>
</template>

<script setup lang="ts">
import { ref, onUnmounted, watch } from 'vue'
import type { ReplyState } from '@/types'

const props = defineProps<{ reply: ReplyState }>()
const elapsed = ref(0)
let rafId = 0
let startTime = 0

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
.reply-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.avatar { font-size: 20px; }
.agent-name { font-weight: 600; font-size: 14px; }
.timer { font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.ttft { color: var(--accent); }
.pulse { animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>
