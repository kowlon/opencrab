<template>
  <div class="input-viewer">
    <h4>输入</h4>
    <pre class="json-content">{{ formatted }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ data: Record<string, unknown> }>()
const formatted = computed(() => JSON.stringify(props.data, null, 2))
</script>

<style scoped>
.input-viewer h4 { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }
.json-content {
  background: var(--bg-primary); padding: 12px; border-radius: 8px;
  font-size: 12px; overflow-x: auto; color: var(--text-secondary);
  white-space: pre-wrap; word-break: break-all;
}
</style>
