<template>
  <div class="summary-output" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{ content: string }>()
const { render } = useMarkdown()
const rendered = computed(() => render(props.content))
</script>

<style scoped>
.summary-output {
  padding: 8px 0;
  font-size: 14px;
  line-height: 1.75;
  color: var(--text-primary);
  animation: fadeIn 0.3s var(--ease-out) both;
}
.summary-output :deep(p) { margin-bottom: 12px; }
.summary-output :deep(p:last-child) { margin-bottom: 0; }
.summary-output :deep(strong) { color: var(--text-bright); font-weight: 600; }
.summary-output :deep(em) { color: var(--text-secondary); }
.summary-output :deep(a) {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid var(--accent-dim);
  transition: border-color 0.15s;
}
.summary-output :deep(a:hover) { border-color: var(--accent); }
.summary-output :deep(pre) {
  background: var(--bg-deep);
  border: 1px solid var(--border);
  padding: 14px 16px;
  border-radius: var(--radius-md);
  overflow-x: auto;
  font-size: 12px;
  margin: 10px 0;
  line-height: 1.6;
}
.summary-output :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
}
.summary-output :deep(:not(pre) > code) {
  background: var(--bg-elevated);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--accent);
}
.summary-output :deep(ul), .summary-output :deep(ol) {
  padding-left: 20px;
  margin-bottom: 12px;
}
.summary-output :deep(li) { margin-bottom: 4px; }
.summary-output :deep(blockquote) {
  border-left: 3px solid var(--accent);
  padding-left: 14px;
  color: var(--text-secondary);
  margin: 10px 0;
}
.summary-output :deep(h1), .summary-output :deep(h2), .summary-output :deep(h3) {
  color: var(--text-bright);
  margin: 16px 0 8px;
  font-weight: 600;
}
.summary-output :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 16px 0;
}
</style>
