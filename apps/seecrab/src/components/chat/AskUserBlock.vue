<template>
  <div class="ask-user">
    <p class="question">{{ ask.question }}</p>
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
.ask-user { margin: 12px 0; padding: 16px; background: var(--bg-tertiary); border-radius: 12px; }
.question { font-size: 14px; margin-bottom: 12px; }
.options { display: flex; gap: 8px; flex-wrap: wrap; }
.option-btn {
  padding: 8px 16px; background: var(--bg-hover); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text-primary); cursor: pointer; font-size: 13px;
}
.option-btn:hover { background: var(--accent); border-color: var(--accent); }
</style>
