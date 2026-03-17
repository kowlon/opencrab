<template>
  <div v-if="summary" class="agent-summary-block" :class="{ expanded }">
    <div class="summary-header" @click="expanded = !expanded">
      <span class="material-symbols-rounded icon">smart_toy</span>
      <span class="label">{{ agentId }} 总结</span>
      <span v-if="!expanded" class="preview">{{ previewText }}</span>
      <span class="material-symbols-rounded toggle-icon">{{ expanded ? 'expand_less' : 'expand_more' }}</span>
    </div>
    <div v-if="expanded" class="summary-content" v-html="renderedText"></div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{ agentId: string; summary: string }>()
const { render } = useMarkdown()
const expanded = ref(false)

const previewText = computed(() => {
  const plain = props.summary.replace(/[#*_`>\[\]()!-]/g, '').replace(/\n+/g, ' ').trim()
  return plain.length > 80 ? plain.slice(0, 80) + '...' : plain
})
const renderedText = computed(() => render(props.summary))
</script>

<style scoped>
.agent-summary-block {
  margin-left: 24px;
  margin-bottom: 6px;
  padding: 8px 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-left: 2px solid var(--accent-dim);
  border-radius: var(--radius-md);
  font-size: 13px;
  animation: fadeIn 0.3s var(--ease-out) both;
  cursor: pointer;
  transition: background 0.15s var(--ease-out);
}
.agent-summary-block:hover {
  background: var(--bg-elevated);
}
.agent-summary-block.expanded {
  cursor: default;
}
.summary-header {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-ghost);
  font-size: 11px;
}
.summary-header .icon {
  font-size: 14px;
}
.summary-header .label {
  white-space: nowrap;
}
.preview {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
  font-size: 12px;
}
.toggle-icon {
  font-size: 16px;
  color: var(--text-ghost);
  transition: transform 0.15s;
}
.summary-content {
  margin-top: 8px;
  color: var(--text-secondary);
  line-height: 1.6;
}
.summary-content :deep(p) { margin-bottom: 6px; }
.summary-content :deep(p:last-child) { margin-bottom: 0; }
.summary-content :deep(strong) { color: var(--text-primary); }
.summary-content :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
  background: var(--bg-elevated);
  padding: 1px 4px;
  border-radius: 3px;
}
</style>
