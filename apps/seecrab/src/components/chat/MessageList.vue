<template>
  <div ref="listRef" class="message-list scrollbar-thin">
    <div class="messages-container">
      <template v-for="msg in chatStore.messages" :key="msg.id">
        <UserMessage v-if="msg.role === 'user'" :message="msg" />
        <BotReply v-else-if="msg.reply" :reply="msg.reply" />
      </template>
      <BotReply v-if="chatStore.currentReply" :reply="chatStore.currentReply" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useChatStore } from '@/stores/chat'
import UserMessage from './UserMessage.vue'
import BotReply from './BotReply.vue'

const chatStore = useChatStore()
const listRef = ref<HTMLElement>()

// Auto-scroll to bottom on new messages
watch(
  () => chatStore.messages.length,
  () => nextTick(() => {
    if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
  })
)
</script>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 24px 0;
}
.messages-container {
  max-width: var(--chat-max-width);
  margin: 0 auto;
  padding-bottom: 16px;
}
</style>
