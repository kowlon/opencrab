<template>
  <div class="user-message">
    <div class="message-content">{{ message.content }}</div>
  </div>
</template>

<script setup lang="ts">
import type { Message } from '@/types'
defineProps<{ message: Message }>()
</script>

<style scoped>
.user-message {
  display: flex; justify-content: flex-end; padding: 8px 0;
}
.message-content {
  background: var(--accent); color: white; border-radius: 16px 16px 4px 16px;
  padding: 10px 16px; max-width: 70%; font-size: 14px; line-height: 1.6;
  word-break: break-word;
}
</style>
