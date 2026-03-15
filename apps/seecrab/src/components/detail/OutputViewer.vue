<template>
  <div class="output-viewer">
    <div class="viewer-header">
      <span class="material-symbols-rounded viewer-icon">output</span>
      <h4>输出</h4>
    </div>
    <div class="output-content scrollbar-thin" v-html="rendered"></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'
const props = defineProps<{ content: string }>()
const { render } = useMarkdown()
const rendered = computed(() => render(props.content))
</script>

<style scoped>
.output-viewer { margin-bottom: 12px; }
.viewer-header {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
}
.viewer-icon { font-size: 13px; color: var(--text-ghost); }
.viewer-header h4 {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.output-content {
  background: var(--bg-deep);
  border: 1px solid var(--border-subtle);
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: 1.6;
  max-height: 400px;
  overflow-y: auto;
  color: var(--text-primary);
}
.output-content :deep(pre) {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  font-size: 11px;
  margin: 8px 0;
}
.output-content :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
}
.output-content :deep(:not(pre) > code) {
  background: var(--bg-elevated);
  padding: 1px 5px;
  border-radius: 3px;
  color: var(--accent);
}
</style>
