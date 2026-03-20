// apps/seecrab/src/api/sse-client.ts
import { useChatStore } from '@/stores/chat'

export class SSEClient {
  private abortController: AbortController | null = null

  async sendMessage(
    message: string,
    conversationId?: string,
    options?: { thinking_mode?: string; thinking_depth?: string },
  ): Promise<void> {
    console.log('[BP-DEBUG][SSE] sendMessage called, msg:', message, 'convId:', conversationId)
    this.abort()
    this.abortController = new AbortController()
    const store = useChatStore()

    try {
      const body = JSON.stringify({
        message,
        conversation_id: conversationId,
        ...options,
      })
      console.log('[BP-DEBUG][SSE] POST /api/seecrab/chat body:', body)
      const resp = await fetch('/api/seecrab/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        signal: this.abortController.signal,
      })

      console.log('[BP-DEBUG][SSE] Response status:', resp.status, resp.statusText)
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          console.log('[BP-DEBUG][SSE] Stream ended (done)')
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (!line.startsWith('data: ')) continue
            const json_str = line.slice(6).trim()
            if (!json_str) continue
            try {
              const event = JSON.parse(json_str)
              console.log('[BP-DEBUG][SSE] Event received:', event.type, event)
              store.dispatchEvent(event)
            } catch (e) {
              console.warn('[SSE] Parse error:', e)
            }
          }
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') return
      console.error('[BP-DEBUG][SSE] Connection error:', err)
      store.dispatchEvent({ type: 'error', message: err.message, code: 'connection' })
    }
  }

  async cancelTask(conversationId: string): Promise<void> {
    try {
      await fetch('/api/chat/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation_id: conversationId,
          reason: '用户点击停止按钮',
        }),
      })
    } catch (e) {
      console.warn('[SSE] Cancel API failed:', e)
    }
    this.abort()
  }

  abort(): void {
    this.abortController?.abort()
    this.abortController = null
  }
}

export const sseClient = new SSEClient()
