<template>
  <div class="user-message">
    <div class="message-content">{{ message.content }}</div>
    <div class="user-avatar">U</div>
  </div>
</template>

<script setup lang="ts">
import type { Message } from '@/types'
defineProps<{ message: Message }>()
</script>

<style scoped>
.user-message {
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 0;
  animation: fadeIn 0.3s var(--ease-out) both;
}
.message-content {
  background: linear-gradient(135deg, var(--accent), #2da0ad);
  color: white;
  border-radius: 16px 16px 4px 16px;
  padding: 10px 16px;
  max-width: 70%;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  box-shadow: 0 2px 12px rgba(56, 189, 204, 0.15);
}
.user-avatar {
  width: 26px; height: 26px;
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}
</style>
