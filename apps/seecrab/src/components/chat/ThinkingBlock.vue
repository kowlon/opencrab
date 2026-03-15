<template>
  <div class="thinking-block" :class="{ collapsed: !expanded }">
    <button class="toggle" @click="expanded = !expanded">
      <span class="material-symbols-rounded">{{ expanded ? 'expand_less' : 'expand_more' }}</span>
      <span class="label">{{ done ? 'Thinking' : 'Thinking...' }}</span>
    </button>
    <div v-show="expanded" class="thinking-content">{{ content }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
defineProps<{ content: string; done: boolean }>()
const expanded = ref(false)
</script>

<style scoped>
.thinking-block { margin-bottom: 8px; border-radius: 8px; background: var(--bg-tertiary); overflow: hidden; }
.toggle {
  display: flex; align-items: center; gap: 4px; width: 100%;
  padding: 8px 12px; background: none; border: none;
  color: var(--text-muted); cursor: pointer; font-size: 12px;
}
.thinking-content {
  padding: 0 12px 12px; font-size: 13px; color: var(--text-secondary);
  white-space: pre-wrap; line-height: 1.5; max-height: 200px; overflow-y: auto;
}
</style>
