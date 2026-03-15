<template>
  <div v-if="step" class="step-detail scrollbar-thin">
    <div class="detail-header">
      <span class="status-icon material-symbols-rounded" :class="step.status">
        {{ step.status === 'completed' ? 'check_circle' : step.status === 'failed' ? 'error' : 'pending' }}
      </span>
      <h3>{{ step.title }}</h3>
      <span v-if="step.duration != null" class="duration">{{ step.duration }}s</span>
    </div>
    <InputViewer v-if="step.input" :data="step.input" />
    <OutputViewer v-if="step.output" :content="step.output" />
    <div v-if="step.absorbedCalls.length" class="absorbed">
      <h4>子调用 ({{ step.absorbedCalls.length }})</h4>
      <div v-for="(call, i) in step.absorbedCalls" :key="i" class="absorbed-item">
        <span class="tool-name">{{ call.tool }}</span>
        <span v-if="call.duration" class="call-duration">{{ call.duration }}s</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '@/stores/chat'
import InputViewer from './InputViewer.vue'
import OutputViewer from './OutputViewer.vue'

const props = defineProps<{ stepId: string }>()
const chatStore = useChatStore()

const step = computed(() => {
  // Search in current reply and all messages
  if (chatStore.currentReply) {
    const found = chatStore.currentReply.stepCards.find(c => c.stepId === props.stepId)
    if (found) return found
  }
  for (const msg of chatStore.messages) {
    if (msg.reply) {
      const found = msg.reply.stepCards.find(c => c.stepId === props.stepId)
      if (found) return found
    }
  }
  return null
})
</script>

<style scoped>
.step-detail { padding: 16px; overflow-y: auto; flex: 1; }
.detail-header { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.detail-header h3 { flex: 1; font-size: 15px; }
.completed { color: var(--success); }
.running { color: var(--accent); }
.failed { color: var(--error); }
.duration { color: var(--text-muted); font-size: 13px; }
.absorbed { margin-top: 16px; }
.absorbed h4 { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
.absorbed-item {
  display: flex; justify-content: space-between; padding: 6px 8px;
  background: var(--bg-primary); border-radius: 4px; margin-bottom: 4px; font-size: 12px;
}
.tool-name { color: var(--text-primary); }
.call-duration { color: var(--text-muted); }
</style>
