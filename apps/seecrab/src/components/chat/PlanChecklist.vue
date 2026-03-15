<template>
  <div class="plan-checklist">
    <div class="plan-header">
      <span class="material-symbols-rounded header-icon">checklist</span>
      <span class="header-title">执行计划</span>
      <span class="step-count">{{ completedCount }}/{{ steps.length }}</span>
    </div>
    <div v-for="step in steps" :key="step.index" class="plan-step" :class="step.status">
      <span class="icon material-symbols-rounded">
        {{ step.status === 'completed' ? 'check' : step.status === 'running' ? 'pending' : step.status === 'failed' ? 'cancel' : 'radio_button_unchecked' }}
      </span>
      <span class="step-num">{{ step.index }}.</span>
      <span class="step-title">{{ step.title }}</span>
      <span v-if="step.status === 'running'" class="in-progress">进行中</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PlanStep } from '@/types'
const props = defineProps<{ steps: PlanStep[] }>()
const completedCount = computed(() => props.steps.filter(s => s.status === 'completed').length)
</script>

<style scoped>
.plan-checklist {
  margin: 8px 0;
  padding: 12px 14px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  animation: fadeIn 0.3s var(--ease-out) both;
}
.plan-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-subtle);
}
.header-icon {
  font-size: 16px;
  color: var(--accent);
}
.header-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-bright);
  flex: 1;
}
.step-count {
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  color: var(--text-ghost);
  font-variant-numeric: tabular-nums;
}
.plan-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  font-size: 13px;
  color: var(--text-secondary);
  transition: color 0.15s;
}
.icon { font-size: 16px; flex-shrink: 0; }
.step-num {
  color: var(--text-ghost);
  min-width: 18px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}
.step-title { flex: 1; }
.completed { color: var(--text-primary); }
.completed .icon { color: var(--success); }
.completed .step-title { text-decoration: line-through; text-decoration-color: var(--text-ghost); }
.running { color: var(--text-bright); }
.running .icon { color: var(--accent); animation: pulse 1.5s infinite; }
.failed .icon { color: var(--error); }
.pending { color: var(--text-muted); }
.in-progress {
  color: var(--accent);
  font-size: 11px;
  font-weight: 500;
  background: var(--accent-dim);
  padding: 1px 6px;
  border-radius: 4px;
}
</style>
