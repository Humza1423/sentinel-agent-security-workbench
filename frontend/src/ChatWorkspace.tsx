import { useState } from 'react'
import BudgetPanel from './BudgetPanel'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  ArrowRight,
  ChatCircle,
  CircleNotch,
  ShieldCheck,
} from '@phosphor-icons/react'
import {
  api,
  newSession,
  restoreSession,
  type EventPage,
  type Run,
  type Session,
} from './chatApi'

const examples = [
  'Show recent transactions',
  'Show my latest transfer',
  'Show spending',
  'Exclude groceries',
]
const eventTitles: Record<string, string> = {
  'tool.requested': 'Assistant requested a tool',
  'authorization.decided': 'Permission checked',
  'repository.read_completed': 'Bank records returned',
  'context.prepared': 'Tool result prepared',
  'context.dispatched': 'Context sent to the model',
  'assistant.output': 'Response delivered',
  'model.responded': 'Model proposed its next step',
}
const errorMessages: Record<string, string> = {
  provider_quota_exhausted:
    'OpenAI API credits or quota are exhausted. Check API billing before retrying.',
  provider_rate_limited:
    'OpenAI is limiting requests temporarily. Please wait before retrying.',
  provider_authentication_failed: 'OpenAI rejected the server API credentials.',
  provider_access_denied:
    'The API project does not have permission for this request.',
  provider_model_unavailable:
    'The configured model is unavailable to this API project.',
  provider_timeout:
    'OpenAI took too long to respond. This turn did not complete.',
  provider_connection_failed: 'The server could not reach OpenAI.',
  provider_request_rejected:
    'OpenAI rejected the request configuration. Inspect the run evidence.',
  provider_not_dispatched:
    'The request exceeded a local input limit or could not be prepared. No provider request was sent.',
}

export default function ChatWorkspace({ openDemo }: { openDemo: () => void }) {
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  const availability = useQuery({
    queryKey: ['live-availability'],
    queryFn: () =>
      api<{ live_chat: { available: boolean; reason: string | null } }>(
        '/api/v2/usage',
      ),
    retry: false,
  })
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [showTarget, setShowTarget] = useState(false)
  const [lastAttempt, setLastAttempt] = useState<{
    text: string
    requestId: string
    sessionId: string
  } | null>(null)
  const session = useQuery({
    queryKey: ['chat-session'],
    queryFn: restoreSession,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
  const reset = useMutation({
    mutationFn: (mode: string) => newSession(mode),
    onSuccess: (data) => {
      client.setQueryData(['chat-session'], data)
      setSelectedRun(null)
      setMessage('')
      setLastAttempt(null)
      send.reset()
    },
  })
  const send = useMutation({
    mutationFn: async ({
      text,
      requestId,
      sessionId,
    }: {
      text: string
      requestId: string
      sessionId: string
    }) => {
      const result = await api<Run>(`/api/v2/sessions/${sessionId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ request_id: requestId, message: text }),
      })
      return result
    },
    retry: false,
    onSuccess: (result, input) => {
      if (
        client.getQueryData<Session>(['chat-session'])?.session_id !==
        input.sessionId
      )
        return
      const turn = {
        request_id: input.requestId,
        user_message: input.text,
        run_id: result.run_id,
        status: result.status,
        result,
      }
      client.setQueryData<Session>(
        ['chat-session'],
        (old) =>
          old && {
            ...old,
            turns: old.turns.some((t) => t.request_id === input.requestId)
              ? old.turns.map((t) =>
                  t.request_id === input.requestId ? turn : t,
                )
              : [...old.turns, turn],
          },
      )
      setSelectedRun(result.run_id)
      setMessage('')
      setLastAttempt(null)
    },
  })
  const data = session.data
  const runId =
    selectedRun ??
    (data?.turns.length ? data.turns[data.turns.length - 1].run_id : undefined)
  const events = useInfiniteQuery({
    queryKey: ['chat-events', runId],
    enabled: !!runId,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api<EventPage>(
        `/api/v2/runs/${runId}/events?after_sequence=${pageParam}&limit=50`,
      ),
    getNextPageParam: (page) =>
      page.has_more ? page.next_after_sequence : undefined,
  })
  const busy = send.isPending || reset.isPending || session.isFetching
  function submit(text: string) {
    if (data && text.trim() && !busy) {
      const attempt =
        send.error &&
        lastAttempt?.text === text.trim() &&
        lastAttempt.sessionId === data.session_id
          ? lastAttempt
          : {
              text: text.trim(),
              requestId: crypto.randomUUID(),
              sessionId: data.session_id,
            }
      setLastAttempt(attempt)
      send.mutate(attempt)
    }
  }
  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#chat">
          <span className="brand-mark">S</span>
          <span className="brand-name">Sentinel</span>
          <span className="brand-divider" />
          <span className="brand-product">Agent Security Workbench</span>
        </a>
        <div className="top-meta">
          <span className="status-dot" />
          Synthetic bank ·{' '}
          {data?.mode === 'live_execution'
            ? 'Live OpenAI · Metered'
            : 'Offline fixture · $0 API cost'}
        </div>
      </header>
      <main className="workspace chat-workspace" id="chat">
        <div className="chat-heading">
          <div>
            <span className="eyebrow">
              UNDERSTAND THE AGENT. FOLLOW THE EVIDENCE.
            </span>
            <h1>
              Your banking assistant,
              <br />
              <span>under the microscope.</span>
            </h1>
            <p>
              Ask a banking question. See the tools, permissions and data behind
              its answer.
            </p>
          </div>
          <button className="export-button" onClick={openDemo}>
            Scripted security demo <ArrowRight size={16} />
          </button>
        </div>
        <div className="chat-toolbar">
          <span className="lab-label">SYNTHETIC LAB TARGET</span>
          <button
            className="export-button"
            disabled={busy || !availability.data?.live_chat?.available}
            onClick={() => reset.mutate('live_execution')}
            title={
              availability.data?.live_chat?.reason ??
              'Start a new metered OpenAI conversation'
            }
          >
            Start live chat · ≤ US$0.024 / turn
          </button>
          <span>
            {data?.customer_id ?? 'Connecting…'} · Customer identity managed by
            the server
          </span>
          <button
            disabled={busy || !data}
            onClick={() => setShowTarget(!showTarget)}
            className="export-button"
          >
            Assess assistant <ShieldCheck size={17} />
          </button>
        </div>
        {showTarget && (
          <section
            className="target-preview"
            aria-label="Selected assessment target"
          >
            <b>Assessment target captured</b>
            <p>
              This is the assistant selected for future attacks. Assessment
              execution will become available with the attack workflow.
            </p>
            <p>
              Profile: {data?.target.effective_tool_profile} · Digest:{' '}
              <code>{data?.target.target_digest}</code>
            </p>
            <details>
              <summary>Inspect actual tool capabilities</summary>
              <pre>{JSON.stringify(data?.target, null, 2)}</pre>
            </details>
          </section>
        )}
        <div className="chat-grid">
          <section className="chat-pane" aria-label="Banking conversation">
            <div className="pane-heading">
              <h2>
                <ChatCircle size={21} /> Banking chat
              </h2>
              <button
                className="export-button"
                disabled={busy}
                onClick={() => reset.mutate(data?.mode ?? 'fixture_execution')}
              >
                New conversation
              </button>
            </div>
            <p className="fixture-notice">
              {data?.mode === 'live_execution'
                ? 'Live OpenAI · Ask naturally, including follow-ups. Answers use the synthetic bank dated October 23, 2026. This lab profile intentionally exposes an unsafe lookup for later assessments.'
                : 'Offline fixture · Limited keyword examples with real tools. Start live chat for natural language and typos.'}
            </p>
            {data?.mode === 'live_execution' && (
              <button
                className="export-button"
                disabled={busy}
                onClick={() => reset.mutate('fixture_execution')}
              >
                Switch to free fixture
              </button>
            )}
            {availability.data?.live_chat?.reason && (
              <p className="fixture-notice">
                Live chat unavailable: {availability.data.live_chat.reason}
              </p>
            )}
            {reset.error && <p role="alert">{reset.error.message}</p>}
            <div className="chat-transcript" aria-live="polite">
              {!data?.turns.length && (
                <div className="chat-welcome">
                  <span className="flow-icon">
                    <ChatCircle size={25} />
                  </span>
                  <h3>Start with your synthetic account.</h3>
                  <p>
                    Explore transactions or spending, then select an answer to
                    inspect how it was produced.
                  </p>
                </div>
              )}
              {data?.turns.map((turn) => (
                <div className="chat-turn" key={turn.request_id}>
                  <div className="user-bubble">
                    <small>YOU</small>
                    <p>{turn.user_message}</p>
                  </div>
                  <button
                    className={`assistant-bubble ${runId === turn.run_id ? 'selected' : ''}`}
                    aria-pressed={runId === turn.run_id}
                    onClick={() => setSelectedRun(turn.run_id)}
                  >
                    <small>SENTINEL · {turn.status}</small>
                    <p>
                      {turn.result?.response?.display_text ??
                        errorMessages[turn.result?.reason_code ?? ''] ??
                        `This turn ended as ${turn.status}. ${turn.result?.reason_code ?? 'Inspect available evidence.'}`}
                    </p>
                    <span>
                      Inspect this answer <ArrowRight size={13} />
                    </span>
                    {!!turn.result?.dropped_turns && (
                      <small>
                        {turn.result.dropped_turns} earlier turns outside model
                        context
                      </small>
                    )}
                  </button>
                </div>
              ))}
              {send.isPending && (
                <p role="status">
                  <CircleNotch className="spinner" /> Running tools and
                  recording evidence…
                </p>
              )}
            </div>
            <div className="example-questions">
              {examples.map((text) => (
                <button
                  key={text}
                  disabled={busy || !data}
                  onClick={() => submit(text)}
                >
                  {text}
                </button>
              ))}
            </div>
            <form
              className="chat-composer"
              onSubmit={(event) => {
                event.preventDefault()
                submit(message)
              }}
            >
              <label className="sr-only" htmlFor="chat-message">
                Banking question
              </label>
              <textarea
                id="chat-message"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                placeholder="Ask about your synthetic transactions…"
                maxLength={4000}
                disabled={busy || !data}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    submit(message)
                  }
                }}
              />
              <button
                className="run-button"
                disabled={busy || !data || !message.trim()}
                type="submit"
              >
                Send <ArrowRight size={16} />
              </button>
            </form>
            {(session.error || reset.error || send.error) && (
              <div className="inline-alert" role="alert">
                {(session.error ?? reset.error ?? send.error)?.message}
                <button disabled={busy} onClick={() => session.refetch()}>
                  Reload conversation
                </button>
              </div>
            )}
          </section>
          <aside className="security-inspector" aria-label="Security inspector">
            <div className="pane-heading">
              <h2>
                <ShieldCheck size={21} /> Security inspector
              </h2>
              <span className="badge">OBSERVED EVENTS</span>
            </div>
            <p className="inspector-note">
              This records what ran. Security verdicts arrive with the
              assessment workflow.
            </p>
            {!runId && (
              <div className="inspector-empty">
                <ShieldCheck size={31} />
                <h3>Every answer leaves a trail.</h3>
                <p>
                  Send a question to inspect model calls, permission decisions
                  and bank reads.
                </p>
              </div>
            )}
            {events.isFetching && <p role="status">Loading evidence…</p>}
            {events.error && <p role="alert">{events.error.message}</p>}
            <ol className="observed-events">
              {events.data?.pages
                .flatMap((page) => page.events)
                .map((event) => (
                  <li key={event.event_id} id={`event-${event.event_id}`}>
                    <span className="event-sequence">
                      {event.sequence.toString().padStart(2, '0')}
                    </span>
                    <div>
                      <b>
                        {eventTitles[event.name] ??
                          event.name.replaceAll('.', ' · ')}
                      </b>
                      <small>
                        {event.actor} · {event.name}
                      </small>
                      <details>
                        <summary>Evidence details</summary>
                        <code>{event.event_id}</code>
                        {event.parent_event_id && (
                          <a href={`#event-${event.parent_event_id}`}>
                            Parent event
                          </a>
                        )}
                        <pre>
                          {JSON.stringify(
                            {
                              payload: event.payload,
                              provenance: event.provenance,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </div>
                  </li>
                ))}
            </ol>
            {events.hasNextPage && (
              <button
                className="export-button"
                disabled={events.isFetchingNextPage}
                onClick={() => events.fetchNextPage()}
              >
                Load more evidence
              </button>
            )}
          </aside>
        </div>
        <BudgetPanel />
        <footer className="footer-note">
          <span>Sentinel · Local engineering workbench</span>
          <span>Selected lab target · Synthetic data only</span>
        </footer>
      </main>
    </div>
  )
}
