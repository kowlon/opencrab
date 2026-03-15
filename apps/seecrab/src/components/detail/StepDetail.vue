<template>
  <div v-if="step" class="step-detail scrollbar-thin">
    <!-- Status Badge -->
    <div class="status-row">
      <span class="status-badge" :class="step.status">
        <span class="material-symbols-rounded badge-icon">
          {{ step.status === 'completed' ? 'check_circle' : step.status === 'failed' ? 'error' : 'pending' }}
        </span>
        {{ statusLabel }}
      </span>
    </div>

    <!-- Meta Info -->
    <div class="meta-grid">
      <div v-if="step.duration != null" class="meta-item">
        <span class="material-symbols-rounded meta-icon">timer</span>
        <span class="meta-label">耗时</span>
        <span class="meta-value">{{ step.duration }}s</span>
      </div>
      <div v-if="step.input" class="meta-item">
        <span class="material-symbols-rounded meta-icon">input</span>
        <span class="meta-label">输入 Tokens</span>
        <span class="meta-value">~{{ estimateTokens(JSON.stringify(step.input)) }}</span>
      </div>
      <div v-if="step.output" class="meta-item">
        <span class="material-symbols-rounded meta-icon">output</span>
        <span class="meta-label">输出 Tokens</span>
        <span class="meta-value">~{{ estimateTokens(step.output) }}</span>
      </div>
    </div>

    <!-- Input Section -->
    <InputViewer v-if="step.input" :data="step.input" />

    <!-- Output Section -->
    <OutputViewer v-if="step.output" :content="step.output" />

    <!-- Absorbed Calls -->
    <div v-if="step.absorbedCalls.length" class="absorbed">
      <div class="section-header">
        <span class="material-symbols-rounded section-icon">account_tree</span>
        <h4>子调用 ({{ step.absorbedCalls.length }})</h4>
      </div>
      <div v-for="(call, i) in step.absorbedCalls" :key="i" class="absorbed-item">
        <span class="material-symbols-rounded call-icon">terminal</span>
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

const statusLabel = computed(() => {
  if (!step.value) return ''
  const labels: Record<string, string> = {
    completed: '完成', running: '进行中', failed: '失败',
  }
  return labels[step.value.status] ?? step.value.status
})

function estimateTokens(text: string): number {
  return Math.round(text.length / 3.5)
}
</script>

<style scoped>
.step-detail {
  padding: 16px;
  overflow-y: auto;
  flex: 1;
}

/* ── Status Badge ── */
.status-row {
  display: flex;
  justify-content: flex-start;
  margin-bottom: 14px;
}
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}
.badge-icon { font-size: 14px; }
.status-badge.completed {
  background: var(--success-dim);
  color: var(--success);
}
.status-badge.running {
  background: var(--accent-dim);
  color: var(--accent);
  animation: pulse 1.5s infinite;
}
.status-badge.failed {
  background: var(--error-dim);
  color: var(--error);
}

/* ── Meta Grid ── */
.meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border-subtle);
}
.meta-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
}
.meta-icon {
  font-size: 14px;
  color: var(--text-ghost);
}
.meta-label {
  color: var(--text-muted);
}
.meta-value {
  color: var(--text-primary);
  font-family: 'JetBrains Mono', monospace;
  font-variant-numeric: tabular-nums;
}

/* ── Section Header ── */
.section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.section-icon {
  font-size: 14px;
  color: var(--text-ghost);
}
.section-header h4 {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

/* ── Absorbed Calls ── */
.absorbed { margin-top: 16px; }
.absorbed-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  margin-bottom: 4px;
  font-size: 12px;
  transition: background 0.15s;
}
.absorbed-item:hover { background: var(--bg-elevated); }
.call-icon { font-size: 13px; color: var(--text-ghost); }
.tool-name {
  flex: 1;
  color: var(--text-primary);
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}
.call-duration {
  color: var(--text-ghost);
  font-family: 'JetBrains Mono', monospace;
  font-variant-numeric: tabular-nums;
}
</style>
