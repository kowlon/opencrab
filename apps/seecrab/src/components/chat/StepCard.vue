<template>
  <div class="step-card" :class="[card.status, card.cardType]">
    <span class="status-icon material-symbols-rounded">
      {{ card.status === 'completed' ? 'check_circle' : card.status === 'failed' ? 'error' : 'pending' }}
    </span>
    <span class="card-type-icon material-symbols-rounded">{{ cardTypeIcon }}</span>
    <span class="title">{{ card.title }}</span>
    <span v-if="card.duration != null" class="duration">{{ card.duration }}s</span>
    <span class="arrow material-symbols-rounded" @click.stop="uiStore.selectStep(card.stepId)">chevron_right</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'
import type { StepCard } from '@/types'

const props = defineProps<{ card: StepCard }>()
const uiStore = useUIStore()

const cardTypeIcon = computed(() => {
  const map: Record<string, string> = {
    search: 'search', code: 'code', file: 'description',
    analysis: 'analytics', browser: 'language', default: 'build',
  }
  return map[props.card.cardType] ?? 'build'
})
</script>

<style scoped>
.step-card {
  display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  background: var(--bg-tertiary); border-radius: 8px; cursor: pointer;
  margin-bottom: 4px; font-size: 13px; transition: background 0.15s;
}
.step-card:hover { background: var(--bg-hover); }
.status-icon { font-size: 16px; }
.completed .status-icon { color: var(--success); }
.running .status-icon { color: var(--accent); animation: pulse 1.5s infinite; }
.failed .status-icon { color: var(--error); }
.card-type-icon { font-size: 14px; color: var(--text-muted); }
.title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.duration { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.arrow { color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 4px; border-radius: 4px; }
.arrow:hover { background: var(--bg-hover); color: var(--accent); }
</style>
