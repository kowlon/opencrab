<template>
  <div class="input-viewer">
    <div class="viewer-header">
      <span class="material-symbols-rounded viewer-icon">input</span>
      <h4>输入</h4>
    </div>
    <pre class="json-content scrollbar-thin">{{ formatted }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ data: Record<string, unknown> }>()
const formatted = computed(() => JSON.stringify(props.data, null, 2))
</script>

<style scoped>
.input-viewer { margin-bottom: 12px; }
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
.json-content {
  background: var(--bg-deep);
  border: 1px solid var(--border-subtle);
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  overflow-x: auto;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
  max-height: 240px;
  overflow-y: auto;
}
</style>
