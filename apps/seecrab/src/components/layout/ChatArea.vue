<!-- apps/seecrab/src/components/layout/ChatArea.vue -->
<template>
  <main class="chat-area">
    <div class="chat-header">
      <span class="material-symbols-rounded header-icon">chat_bubble</span>
      <span class="header-title">{{ currentTitle }}</span>
    </div>
    <WelcomePage v-if="showWelcome" @prefill="onPrefill" />
    <MessageList v-else />
    <ChatInput ref="chatInputRef" />
  </main>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'
import WelcomePage from '@/components/welcome/WelcomePage.vue'
import MessageList from '@/components/chat/MessageList.vue'
import ChatInput from '@/components/chat/ChatInput.vue'

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const chatInputRef = ref<InstanceType<typeof ChatInput>>()

const showWelcome = computed(() =>
  chatStore.messages.length === 0 && !chatStore.currentReply
)

const currentTitle = computed(() => {
  if (!sessionStore.activeSessionId) return '新对话'
  const session = sessionStore.sessions.find(s => s.id === sessionStore.activeSessionId)
  return session?.title || '新对话'
})

function onPrefill(text: string) {
  chatInputRef.value?.prefill(text)
}
</script>

<style scoped>
.chat-area {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-deep);
  position: relative;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.header-icon {
  font-size: 18px;
  color: var(--text-muted);
}

.header-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-bright);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
