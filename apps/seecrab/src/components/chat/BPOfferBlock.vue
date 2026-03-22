<script setup lang="ts">
import type { BPOfferInfo } from '@/types'

defineProps<{
  offer: BPOfferInfo
  disabled: boolean
}>()

const emit = defineEmits<{
  accept: [bpId: string]
  decline: []
}>()
</script>

<template>
  <div class="bp-offer-block">
    <div class="offer-msg">
      <span class="material-symbols-rounded offer-icon">lightbulb</span>
      <p>
        检测到您的需求匹配最佳实践「{{ offer.bpName }}」，该任务包含
        {{ offer.subtasks.length }} 个子任务：<span
          v-for="(s, i) in offer.subtasks"
          :key="s.id"
        ><span v-if="i > 0"> → </span>{{ s.name }}</span>。是否使用最佳实践流程？
      </p>
    </div>
    <div class="offer-actions">
      <button class="offer-btn" :disabled="disabled" @click="emit('decline')">
        自由模式
      </button>
      <button
        class="offer-btn primary"
        :disabled="disabled"
        @click="emit('accept', offer.bpId)"
      >
        最佳实践模式
      </button>
    </div>
  </div>
</template>

<style scoped>
.bp-offer-block {
  padding: 14px 16px;
  background: var(--bg-surface, #171d2a);
  border: 1px solid var(--border-accent, rgba(56, 189, 204, 0.2));
  border-radius: var(--radius-md, 10px);
  margin: 10px 0;
  animation: fadeIn 0.3s var(--ease-out) both;
}
.offer-msg {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}
.offer-icon {
  font-size: 18px;
  color: var(--accent, #38bdcc);
  flex-shrink: 0;
  margin-top: 1px;
}
.offer-msg p {
  font-size: 14px;
  color: var(--text-bright, #e8edf5);
  line-height: 1.5;
  margin: 0;
}
.offer-actions {
  display: flex;
  gap: 8px;
  padding-left: 26px;
}
.offer-btn {
  padding: 7px 16px;
  background: var(--bg-elevated, #1e2536);
  border: 1px solid var(--border, rgba(56, 189, 204, 0.08));
  border-radius: var(--radius-sm, 6px);
  color: var(--text-primary, #c4cdd9);
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  transition: all 0.15s;
}
.offer-btn:hover {
  background: var(--accent-dim, rgba(56, 189, 204, 0.12));
  border-color: var(--accent, #38bdcc);
  color: var(--accent, #38bdcc);
}
.offer-btn.primary {
  background: var(--accent-dim, rgba(56, 189, 204, 0.12));
  border-color: var(--accent, #38bdcc);
  color: var(--accent, #38bdcc);
}
.offer-btn:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}
</style>
