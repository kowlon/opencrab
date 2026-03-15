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
import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import UserMessage from './UserMessage.vue'
import BotReply from './BotReply.vue'

const chatStore = useChatStore()
const listRef = ref<HTMLElement>()
</script>

<style scoped>
.message-list { flex: 1; overflow-y: auto; padding: 16px; }
.messages-container { max-width: var(--chat-max-width); margin: 0 auto; }
</style>
