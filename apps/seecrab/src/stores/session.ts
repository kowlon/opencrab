// apps/seecrab/src/stores/session.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { httpClient } from '@/api/http-client'
import type { Session } from '@/types'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref<Session[]>([])
  const activeSessionId = ref<string | null>(null)

  async function loadSessions() {
    const { sessions: list } = await httpClient.listSessions()
    sessions.value = list
  }

  async function createSession() {
    const { session_id } = await httpClient.createSession()
    sessions.value.unshift({
      id: session_id,
      title: '',
      lastMessage: '',
      updatedAt: Date.now(),
      messageCount: 0,
    })
    activeSessionId.value = session_id
    return session_id
  }

  function selectSession(id: string) {
    activeSessionId.value = id
  }

  function updateSessionTitle(id: string, title: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) {
      session.title = title
    }
  }

  function incrementStepCount(id: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) {
      session.messageCount += 1
      session.updatedAt = Date.now()
    }
  }

  function updateLastMessage(id: string, lastMessage: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) {
      session.lastMessage = lastMessage.length > 60 ? lastMessage.substring(0, 60) + '...' : lastMessage
      session.updatedAt = Date.now()
    }
  }

  return {
    sessions, activeSessionId,
    loadSessions, createSession, selectSession,
    updateSessionTitle, incrementStepCount, updateLastMessage,
  }
})
