// apps/seecrab/src/api/http-client.ts
const BASE = '/api/seecrab'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export const httpClient = {
  listSessions: () => request<{ sessions: any[] }>('/sessions'),
  createSession: () => request<{ session_id: string }>('/sessions', { method: 'POST' }),
  deleteSession: (id: string) => request<{ status: string }>(`/sessions/${id}`, { method: 'DELETE' }),
  getSession: (id: string) => request<{ session_id: string; title: string; messages: any[] }>(`/sessions/${id}`),
  updateSession: (id: string, data: { title?: string }) =>
    request<{ status: string }>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  submitAnswer: (conversationId: string, answer: string) =>
    request('/answer', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, answer }),
    }),
  generateTitle: async (message: string, reply: string): Promise<{ title: string }> => {
    const resp = await fetch('/api/sessions/generate-title', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, reply }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
  // ── BP v1.1 endpoints ─────────────────────────────────────
  getBPStatus: async (sessionId: string) => {
    const resp = await fetch(`/api/bp/instances?session_id=${sessionId}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
  setBPRunMode: async (instanceId: string, runMode: 'manual' | 'auto') => {
    const resp = await fetch(`/api/bp/instances/${instanceId}/run-mode`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_mode: runMode }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
  editBPOutput: async (
    instanceId: string,
    subtaskId: string,
    changes: Record<string, unknown>,
  ) => {
    const resp = await fetch(`/api/bp/instances/${instanceId}/output`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subtask_id: subtaskId, changes }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
  getBPInstance: async (instanceId: string) => {
    const resp = await fetch(`/api/bp/instances/${instanceId}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
  getBPConfigs: async () => {
    const resp = await fetch('/api/bp/configs')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
  getBPConfig: async (bpId: string) => {
    const resp = await fetch(`/api/bp/configs/${bpId}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
  getBPInstanceStats: async (params?: { session_id?: string; bp_id?: string }) => {
    const qs = new URLSearchParams()
    if (params?.session_id) qs.set('session_id', params.session_id)
    if (params?.bp_id) qs.set('bp_id', params.bp_id)
    const query = qs.toString() ? `?${qs.toString()}` : ''
    const resp = await fetch(`/api/bp/instances/stats${query}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    return resp.json()
  },
}
