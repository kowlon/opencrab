<template>
  <div class="plan-checklist">
    <div v-for="step in steps" :key="step.index" class="plan-step" :class="step.status">
      <span class="icon material-symbols-rounded">
        {{ step.status === 'completed' ? 'check_circle' : step.status === 'running' ? 'pending' : step.status === 'failed' ? 'cancel' : 'radio_button_unchecked' }}
      </span>
      <span class="step-title">{{ step.title }}</span>
      <span v-if="step.status === 'running'" class="in-progress">(进行中)</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PlanStep } from '@/types'
defineProps<{ steps: PlanStep[] }>()
</script>

<style scoped>
.plan-checklist { margin: 8px 0; padding: 12px; background: var(--bg-tertiary); border-radius: 8px; }
.plan-step { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px; }
.icon { font-size: 16px; }
.completed .icon { color: var(--success); }
.running .icon { color: var(--accent); animation: pulse 1.5s infinite; }
.failed .icon { color: var(--error); }
.pending { color: var(--text-muted); }
.in-progress { color: var(--accent); font-size: 11px; }
</style>
