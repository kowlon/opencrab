# SeeCrab 会话列表三个 Bug 修复 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three bugs in the SeeCrab webapp: (1) session title not persisted to backend, (2) delete errors silently swallowed, (3) thinking output not displayed.

**Architecture:** Add a PATCH endpoint for session metadata updates; make the frontend delete flow fail-visible; pass `thinking_mode` through the SSE client to the backend chat endpoint.

**Tech Stack:** Python/FastAPI (backend), TypeScript/Vue 3/Pinia (frontend)

---

### Task 1: Backend — Add PATCH endpoint for session metadata

**Files:**
- Modify: `src/openakita/api/routes/seecrab.py:307` (insert before `delete_session`)
- Modify: `src/openakita/api/schemas_seecrab.py:22` (add request schema)

**Step 1: Add the Pydantic schema for PATCH request**

In `src/openakita/api/schemas_seecrab.py`, add after `SeeCrabChatRequest` class (before `SeeCrabAnswerRequest`):

```python
class SeeCrabSessionUpdateRequest(BaseModel):
    """Update session metadata (title, etc.)."""

    title: str | None = Field(None, description="New session title")
```

**Step 2: Add the PATCH route**

In `src/openakita/api/routes/seecrab.py`, add before the `delete_session` route (before line 307):

```python
@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, body: SeeCrabSessionUpdateRequest, request: Request):
    """Update session metadata (title, etc.)."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    session = sm.get_session(
        channel="seecrab",
        chat_id=session_id,
        user_id="seecrab_user",
        create_if_missing=False,
    )
    if session is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if body.title is not None:
        session.set_metadata("title", body.title)
    sm.mark_dirty()
    return JSONResponse({"status": "ok"})
```

Add `SeeCrabSessionUpdateRequest` to the import at top of `seecrab.py`:

```python
from ..schemas_seecrab import SeeCrabAnswerRequest, SeeCrabChatRequest, SeeCrabSessionUpdateRequest
```

**Step 3: Verify manually**

Run: `ruff check src/openakita/api/routes/seecrab.py src/openakita/api/schemas_seecrab.py`
Expected: No errors

**Step 4: Commit**

```bash
git add src/openakita/api/routes/seecrab.py src/openakita/api/schemas_seecrab.py
git commit -m "feat(seecrab): add PATCH /sessions/{id} endpoint for title update"
```

---

### Task 2: Frontend — Add `updateSession` to HTTP client and wire `updateSessionTitle` to call backend

**Files:**
- Modify: `apps/seecrab/src/api/http-client.ts:17` (add updateSession method)
- Modify: `apps/seecrab/src/stores/session.ts:61-66` (make updateSessionTitle call backend)

**Step 1: Add `updateSession` to the HTTP client**

In `apps/seecrab/src/api/http-client.ts`, add after the `getSession` line:

```typescript
  updateSession: (id: string, data: { title?: string }) =>
    request<{ status: string }>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
```

**Step 2: Make `updateSessionTitle` persist to backend**

In `apps/seecrab/src/stores/session.ts`, replace the `updateSessionTitle` function:

```typescript
  function updateSessionTitle(id: string, title: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) {
      session.title = title
    }
    // Persist to backend (fire-and-forget, local update is source of truth for UI)
    httpClient.updateSession(id, { title }).catch(e => {
      console.warn('[Session] Failed to persist title:', e)
    })
  }
```

**Step 3: Verify lint**

Run: `cd apps/seecrab && npx vue-tsc --noEmit 2>&1 | head -20` (or equivalent type check)
Expected: No new errors

**Step 4: Commit**

```bash
git add apps/seecrab/src/api/http-client.ts apps/seecrab/src/stores/session.ts
git commit -m "fix(seecrab): persist session title to backend on update"
```

---

### Task 3: Frontend — Make delete fail-visible instead of silent

**Files:**
- Modify: `apps/seecrab/src/stores/session.ts:45-55` (deleteSession function)

**Step 1: Rewrite `deleteSession` to not swallow errors**

Replace the `deleteSession` function in `apps/seecrab/src/stores/session.ts`:

```typescript
  async function deleteSession(id: string) {
    // Call backend first; only remove locally on success
    try {
      await httpClient.deleteSession(id)
    } catch (e) {
      console.error('[Session] Failed to delete session on backend:', e)
      // Still remove locally — backend may have already deleted it (404)
      // but log the error for debugging
    }
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (activeSessionId.value === id) {
      activeSessionId.value = sessions.value.length > 0 ? sessions.value[0].id : null
    }
  }
```

> Note: We keep the "remove locally even on failure" behavior because a 404 means "already gone" which is fine. The key change is `console.error` instead of silent catch — makes debugging possible. If you want stricter behavior (don't remove on 5xx), see the alternative below but it adds UI complexity.

**Step 2: Verify lint**

Run: `cd apps/seecrab && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new errors

**Step 3: Commit**

```bash
git add apps/seecrab/src/stores/session.ts
git commit -m "fix(seecrab): log delete errors instead of silently swallowing"
```

---

### Task 4: Frontend — Pass `thinking_mode` through SSE client

**Files:**
- Modify: `apps/seecrab/src/api/sse-client.ts:7` (add thinking_mode param)
- Modify: `apps/seecrab/src/components/chat/ChatInput.vue:56` (pass thinking_mode)

**Step 1: Update `SSEClient.sendMessage` signature to accept options**

Replace the `sendMessage` method in `apps/seecrab/src/api/sse-client.ts`:

```typescript
  async sendMessage(
    message: string,
    conversationId?: string,
    options?: { thinking_mode?: string; thinking_depth?: string },
  ): Promise<void> {
    this.abort()
    this.abortController = new AbortController()
    const store = useChatStore()

    try {
      const resp = await fetch('/api/seecrab/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          conversation_id: conversationId,
          ...options,
        }),
        signal: this.abortController.signal,
      })

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

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
              store.dispatchEvent(event)
            } catch (e) {
              console.warn('[SSE] Parse error:', e)
            }
          }
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') return
      console.error('[SSE] Connection error:', err)
      store.dispatchEvent({ type: 'error', message: err.message, code: 'connection' })
    }
  }
```

**Step 2: Pass `thinking_mode: 'auto'` from ChatInput**

In `apps/seecrab/src/components/chat/ChatInput.vue`, change the `sseClient.sendMessage` call (line 56):

```typescript
    await sseClient.sendMessage(msg, sessionStore.activeSessionId ?? undefined, {
      thinking_mode: 'auto',
    })
```

This tells the backend to use its default thinking logic (which includes extended thinking when the model supports it). The `'auto'` value flows through `agent.py:4319` → `reasoning_engine.py:2847` where `use_thinking = None` → Brain uses its default (thinking enabled).

**Step 3: Verify lint**

Run: `cd apps/seecrab && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new errors

**Step 4: Commit**

```bash
git add apps/seecrab/src/api/sse-client.ts apps/seecrab/src/components/chat/ChatInput.vue
git commit -m "fix(seecrab): pass thinking_mode through SSE client to backend"
```

---

### Task 5: Verify all changes together

**Step 1: Run backend lint**

Run: `ruff check src/openakita/api/routes/seecrab.py src/openakita/api/schemas_seecrab.py`
Expected: No errors

**Step 2: Run frontend type check**

Run: `cd apps/seecrab && npx vue-tsc --noEmit 2>&1 | head -30`
Expected: No new errors

**Step 3: Run existing tests**

Run: `pytest tests/unit/ -x -v -k "session or seecrab" 2>&1 | tail -20`
Expected: All pass (or no matching tests, which is fine)

**Step 4: Manual verification checklist**

- [ ] Start the app, send a message → title appears in sidebar AND persists after page reload
- [ ] Delete a session → check browser console for any error logs
- [ ] Send a message with a model that supports thinking → ThinkingBlock appears with spinning indicator
- [ ] Reload page after thinking conversation → thinking is gone (expected: thinking is transient, not stored)
