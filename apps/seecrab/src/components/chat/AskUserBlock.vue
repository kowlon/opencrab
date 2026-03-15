<template>
  <div class="ask-user">
    <div class="ask-header">
      <span class="material-symbols-rounded ask-icon">help</span>
      <p class="question">{{ ask.question }}</p>
    </div>
    <div class="options">
      <button v-for="opt in ask.options" :key="opt.value" class="option-btn" @click="submitAnswer(opt.value)">
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'
import { httpClient } from '@/api/http-client'
import type { AskUserState } from '@/types'

defineProps<{ ask: AskUserState }>()
const sessionStore = useSessionStore()

async function submitAnswer(value: string) {
  if (sessionStore.activeSessionId) {
    await httpClient.submitAnswer(sessionStore.activeSessionId, value)
  }
}
</script>

<style scoped>
.ask-user {
  margin: 10px 0;
  padding: 14px 16px;
  background: var(--bg-surface);
  border: 1px solid var(--border-accent);
  border-radius: var(--radius-md);
  animation: fadeIn 0.3s var(--ease-out) both;
}
.ask-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}
.ask-icon {
  font-size: 18px;
  color: var(--accent);
  flex-shrink: 0;
  margin-top: 1px;
}
.question {
  font-size: 14px;
  color: var(--text-bright);
  line-height: 1.5;
}
.options {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding-left: 26px;
}
.option-btn {
  padding: 7px 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  transition: all 0.15s var(--ease-out);
}
.option-btn:hover {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: var(--accent);
}
</style>
