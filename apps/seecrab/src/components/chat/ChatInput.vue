<template>
  <div class="chat-input-container">
    <div class="input-wrapper">
      <textarea
        ref="inputRef"
        v-model="inputText"
        placeholder="输入消息..."
        rows="1"
        @keydown.enter.exact.prevent="send"
        @input="autoResize"
      />
      <button class="send-btn" :disabled="!inputText.trim() || isStreaming" @click="send">
        <span class="material-symbols-rounded">send</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'
import { sseClient } from '@/api/sse-client'

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement>()
const isStreaming = ref(false)

function autoResize() {
  const el = inputRef.value
  if (el) { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px' }
}

async function send() {
  const msg = inputText.value.trim()
  if (!msg || isStreaming.value) return
  inputText.value = ''
  chatStore.addUserMessage(msg)
  isStreaming.value = true
  await sseClient.sendMessage(msg, sessionStore.activeSessionId ?? undefined)
  isStreaming.value = false
}

defineExpose({ prefill: (text: string) => { inputText.value = text } })
</script>

<style scoped>
.chat-input-container { padding: 16px; max-width: var(--chat-max-width); margin: 0 auto; width: 100%; }
.input-wrapper {
  display: flex; align-items: flex-end; gap: 8px;
  background: var(--bg-tertiary); border-radius: 12px; padding: 8px 12px;
  border: 1px solid var(--border);
}
textarea {
  flex: 1; background: none; border: none; color: var(--text-primary);
  font-size: 14px; resize: none; outline: none; max-height: 120px;
  font-family: inherit; line-height: 1.5;
}
.send-btn {
  background: var(--accent); border: none; color: white; border-radius: 8px;
  width: 32px; height: 32px; cursor: pointer; display: flex; align-items: center; justify-content: center;
}
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
