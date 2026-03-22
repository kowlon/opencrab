<script setup lang="ts">
defineProps<{
  bp: {
    instanceId: string
    bpId: string
    bpName: string
    runMode: string
    subtasks: { id: string; name: string }[]
  }
  disabled: boolean
}>()

const emit = defineEmits<{
  start: []
}>()
</script>

<template>
  <div class="bp-instance-created-block">
    <div class="bp-header">
      <span class="material-symbols-rounded header-icon">checklist</span>
      <span class="title">已创建最佳实践「{{ bp.bpName }}」</span>
      <span class="mode-tag">{{ bp.runMode === 'auto' ? '自动模式' : '手动模式' }}</span>
    </div>
    <div class="subtask-preview">
      <span v-for="(s, i) in bp.subtasks" :key="s.id" class="step">
        <span v-if="i > 0" class="arrow">→</span>
        {{ i + 1 }}. {{ s.name }}
      </span>
    </div>
    <div class="bp-actions">
      <button class="action-btn primary" :disabled="disabled" @click="emit('start')">
        <span class="material-symbols-rounded">play_arrow</span>
        开始执行
      </button>
    </div>
  </div>
</template>

<style scoped>
.bp-instance-created-block {
  background: var(--bg-surface, #171d2a);
  border: 1px solid var(--border-subtle, #252d40);
  border-left: 3px solid var(--accent-color, #4a6cf7);
  border-radius: var(--radius-md, 10px);
  padding: 14px 16px;
  margin: 10px 0;
  animation: fadeIn 0.35s var(--ease-out) both;
}
.bp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.header-icon {
  font-size: 18px;
  color: var(--accent-color, #4a6cf7);
}
.title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-bright, #e8edf5);
}
.mode-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 8px;
  background: var(--accent-dim, rgba(74, 108, 247, 0.12));
  color: var(--accent-color, #4a6cf7);
  font-weight: 500;
}
.subtask-preview {
  font-size: 13px;
  color: var(--text-secondary, #8494a7);
  line-height: 1.6;
  margin-bottom: 12px;
  padding-left: 26px;
}
.arrow {
  margin: 0 4px;
  color: var(--text-muted, #556174);
}
.bp-actions {
  display: flex;
  gap: 10px;
  padding-left: 26px;
}
.action-btn {
  padding: 8px 18px;
  border-radius: var(--radius-sm, 6px);
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s;
}
.action-btn .material-symbols-rounded {
  font-size: 16px;
}
.action-btn.primary {
  background: var(--accent-dim, rgba(74, 108, 247, 0.12));
  border: 1px solid var(--accent-color, #4a6cf7);
  color: var(--accent-color, #4a6cf7);
}
.action-btn.primary:hover {
  background: rgba(74, 108, 247, 0.2);
}
.action-btn:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}
</style>
