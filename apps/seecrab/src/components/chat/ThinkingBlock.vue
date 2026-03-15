<template>
  <div class="thinking-block">
    <button class="toggle" @click="expanded = !expanded">
      <span class="material-symbols-rounded toggle-icon" :class="{ spin: !done }">
        {{ done ? 'psychology' : 'progress_activity' }}
      </span>
      <span class="label">{{ done ? '思考完成' : '思考中...' }}</span>
      <span class="material-symbols-rounded chevron">{{ expanded ? 'expand_less' : 'expand_more' }}</span>
    </button>
    <transition name="expand">
      <div v-show="expanded" class="thinking-content scrollbar-thin">{{ content }}</div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
defineProps<{ content: string; done: boolean }>()
const expanded = ref(false)
</script>

<style scoped>
.thinking-block {
  margin-bottom: 8px;
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  overflow: hidden;
  animation: fadeIn 0.3s var(--ease-out) both;
}
.toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 9px 12px;
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
  font-family: inherit;
  transition: color 0.15s;
}
.toggle:hover { color: var(--text-primary); }
.toggle-icon { font-size: 16px; color: var(--accent); }
.toggle-icon.spin { animation: spin 1.2s linear infinite; }
.label { flex: 1; text-align: left; font-weight: 500; }
.chevron { font-size: 18px; color: var(--text-ghost); }

.thinking-content {
  padding: 0 12px 12px;
  font-size: 12px;
  color: var(--text-muted);
  white-space: pre-wrap;
  line-height: 1.6;
  max-height: 200px;
  overflow-y: auto;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: -0.01em;
}

/* Expand transition */
.expand-enter-active { transition: all 0.2s var(--ease-out); }
.expand-leave-active { transition: all 0.15s ease-in; }
.expand-enter-from, .expand-leave-to {
  opacity: 0;
  max-height: 0;
  padding-bottom: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
