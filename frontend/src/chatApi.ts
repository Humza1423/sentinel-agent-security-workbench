export type Run = {
  run_id: string
  status: string
  response: { display_text: string; transaction_ids: string[] } | null
  reason_code: string | null
  retained_turns: number
  dropped_turns: number
}
export type Session = {
  mode?: 'fixture_execution' | 'live_execution'
  session_id: string
  customer_id: string
  target: {
    target_digest: string
    effective_tool_profile: string
    tools: { name: string }[]
  }
  turns: {
    request_id: string
    user_message: string
    run_id: string
    status: string
    result: Run | null
  }[]
}
export type RunEvent = {
  event_id: string
  name: string
  actor: string
  sequence: number
  parent_event_id: string | null
  payload: Record<string, unknown>
  provenance: Record<string, string>
}
export type EventPage = {
  events: RunEvent[]
  has_more: boolean
  next_after_sequence: number
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `Service returned ${response.status}`)
  }
  return response.json()
}
export async function newSession(mode = 'fixture_execution'): Promise<Session> {
  const session = await api<Session>('/api/v2/sessions', {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
  localStorage.setItem('sentinel-session', session.session_id)
  return session
}
export async function restoreSession(): Promise<Session> {
  const saved = localStorage.getItem('sentinel-session')
  if (!saved) return newSession()
  const response = await fetch(`/api/v2/sessions/${saved}`, {
    credentials: 'include',
  })
  if (response.status === 404) return newSession()
  if (!response.ok)
    throw new Error('Could not restore conversation. Please retry.')
  return response.json()
}
