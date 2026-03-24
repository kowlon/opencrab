// apps/seecrab/src/api/sse-client.ts
import { useChatStore } from '@/stores/chat'

export class SSEClient {
  private abortController: AbortController | null = null
  private bpAbortController: AbortController | null = null

  async sendMessage(
    message: string,
    conversationId?: string,
    options?: { thinking_mode?: string; thinking_depth?: string },
  ): Promise<void> {
    console.log('[BP-DEBUG][SSE] sendMessage called, msg:', message, 'convId:', conversationId)
    this.abort()
    this.abortBP()
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

  abortBP(): void {
    if (this.bpAbortController) {
      this.bpAbortController.abort()
      this.bpAbortController = null
    }
  }

  async streamBP(url: string, body: Record<string, unknown>): Promise<void> {
    const store = useChatStore()
    this.abortBP()
    this.bpAbortController = new AbortController()

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: this.bpAbortController.signal,
      })

      if (!response.ok) {
        const errText = await response.text()
        store.dispatchEvent({ type: 'error', error: `BP request failed: ${errText}` })
        return
      }

      const reader = response.body?.getReader()
      if (!reader) return

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''

        for (const part of parts) {
          const lines = part.split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const event = JSON.parse(line.slice(6))
              store.dispatchEvent(event)
            } catch { /* skip malformed */ }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      console.error('[SSE] BP stream error:', err)
      store.dispatchEvent({ type: 'error', error: 'BP 连接断开' })
    } finally {
      this.bpAbortController = null
    }
  }
}

export const sseClient = new SSEClient()
