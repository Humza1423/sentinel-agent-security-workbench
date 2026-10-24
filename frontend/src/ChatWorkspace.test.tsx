import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ChatWorkspace from './ChatWorkspace'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('Banking workspace', () => {
  it('explains a quota failure without presenting a banking answer', async () => {
    const session = {
      session_id: 'quota-session',
      mode: 'live_execution',
      customer_id: 'C-100',
      target: { tools: [] },
      turns: [
        {
          request_id: 'quota-request',
          user_message: 'Latest transfer',
          run_id: 'quota-run',
          status: 'error',
          result: {
            status: 'error',
            response: null,
            reason_code: 'provider_quota_exhausted',
          },
        },
      ],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) => ({
        ok: true,
        json: async () =>
          path === '/api/v2/sessions'
            ? session
            : { events: [], live_chat: { available: true } },
      })),
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ChatWorkspace openDemo={() => {}} />
      </QueryClientProvider>,
    )
    expect(
      await screen.findByText(/OpenAI API credits or quota are exhausted/),
    ).toBeTruthy()
    expect(screen.queryByText('Actual bank answer')).toBeNull()
  })
  it('explicitly starts live mode and labels the restored mode accurately', async () => {
    const session = {
      session_id: 'fixture',
      mode: 'fixture_execution',
      customer_id: 'C-100',
      target: { tools: [] },
      turns: [],
    }
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/usage')
        return {
          ok: true,
          json: async () => ({ live_chat: { available: true } }),
        }
      const live =
        init?.body && JSON.parse(String(init.body)).mode === 'live_execution'
      return {
        ok: true,
        json: async () =>
          live
            ? { ...session, session_id: 'live', mode: 'live_execution' }
            : session,
      }
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ChatWorkspace openDemo={() => {}} />
      </QueryClientProvider>,
    )
    const button = await screen.findByRole('button', {
      name: /Start live chat/,
    })
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    )
    fireEvent.click(button)
    expect(await screen.findByText(/Live OpenAI · Metered/)).toBeTruthy()
    expect(
      fetchMock.mock.calls.some(
        (c) => c[1]?.body === JSON.stringify({ mode: 'live_execution' }),
      ),
    ).toBe(true)
  })
  it('runs chat and displays evidence from the returned run', async () => {
    const session = {
      session_id: 'session-1',
      customer_id: 'C-100',
      target: {
        effective_tool_profile: 'vulnerable_lookup_v1',
        target_digest: 'digest',
        tools: [],
      },
      turns: [],
    }
    const fetchMock = vi.fn(async (path: string) => {
      if (path === '/api/v2/sessions')
        return { ok: true, json: async () => session }
      if (path.endsWith('/messages'))
        return {
          ok: true,
          json: async () => ({
            run_id: 'observed-run',
            status: 'answered',
            response: {
              display_text: 'Actual bank answer',
              transaction_ids: [],
            },
            dropped_turns: 0,
          }),
        }
      return {
        ok: true,
        json: async () => ({
          events: [
            {
              event_id: 'event-one',
              name: 'repository.read_completed',
              actor: 'repository',
              sequence: 1,
              payload: { record_owners: ['C-100'] },
              provenance: {},
            },
          ],
          has_more: false,
        }),
      }
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ChatWorkspace openDemo={() => {}} />
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(
        (
          screen.getByRole('button', {
            name: 'Show spending',
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Show spending' }))
    expect(await screen.findByText('Actual bank answer')).toBeTruthy()
    expect(await screen.findByText('Bank records returned')).toBeTruthy()
    expect(
      fetchMock.mock.calls.some((call) =>
        call[0].includes('/observed-run/events'),
      ),
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Assess assistant' }))
    expect(screen.getByText('Assessment target captured')).toBeTruthy()
    expect(
      screen.getByText(/Assessment execution will become available/),
    ).toBeTruthy()
  })

  it('shows service failures without substituting a demo answer', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('Service offline')
      }),
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ChatWorkspace openDemo={() => {}} />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryByText('Actual bank answer')).toBeNull()
  })

  it('ignores an old reply after the selected session changes', async () => {
    const session = {
      session_id: 'old',
      customer_id: 'C-100',
      target: { tools: [] },
      turns: [],
    }
    let finish: (value: unknown) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        path.endsWith('/messages')
          ? new Promise((resolve) => {
              finish = resolve
            })
          : { ok: true, json: async () => session },
      ),
    )
    const client = new QueryClient()
    render(
      <QueryClientProvider client={client}>
        <ChatWorkspace openDemo={() => {}} />
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(
        (
          screen.getByRole('button', {
            name: 'Show spending',
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Show spending' }))
    await waitFor(() => expect(screen.getByRole('status')).toBeTruthy())
    await act(async () => {
      client.setQueryData(['chat-session'], { ...session, session_id: 'new' })
      finish({
        ok: true,
        json: async () => ({
          run_id: 'old-run',
          status: 'answered',
          response: { display_text: 'Old answer' },
        }),
      })
    })
    await waitFor(() => expect(screen.queryByText('Old answer')).toBeNull())
    expect(
      (client.getQueryData(['chat-session']) as { turns: unknown[] }).turns,
    ).toEqual([])
  })

  it('updates a restored running turn when the same request retry completes', async () => {
    const session = {
      session_id: 'retry-session',
      customer_id: 'C-100',
      target: { tools: [] },
      turns: [] as unknown[],
    }
    const requestIds: string[] = []
    let finishReload: (value: unknown) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path.endsWith('/messages')) {
          const body = JSON.parse(init?.body as string)
          requestIds.push(body.request_id)
          if (requestIds.length === 1) {
            session.turns = [
              {
                request_id: body.request_id,
                user_message: 'Show spending',
                run_id: 'retry-run',
                status: 'running',
                result: null,
              },
            ]
            throw new Error('Response lost')
          }
          return {
            ok: true,
            json: async () => ({
              run_id: 'retry-run',
              status: 'answered',
              response: { display_text: 'Recovered answer' },
            }),
          }
        }
        if (path.includes('/events'))
          return {
            ok: true,
            json: async () => ({ events: [], has_more: false }),
          }
        if (path === '/api/v2/sessions/retry-session')
          return new Promise((resolve) => {
            finishReload = resolve
          })
        return {
          ok: true,
          json: async () => ({ ...session, turns: [...session.turns] }),
        }
      }),
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ChatWorkspace openDemo={() => {}} />
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(
        (
          screen.getByRole('button', {
            name: 'Show spending',
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Show spending' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Reload conversation' }))
    await waitFor(() =>
      expect(
        (
          screen.getByRole('button', {
            name: 'New conversation',
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true),
    )
    expect(
      (
        screen.getByRole('button', {
          name: 'Show spending',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true)
    await act(async () =>
      finishReload({
        ok: true,
        json: async () => ({ ...session, turns: [...session.turns] }),
      }),
    )
    await screen.findByText(/This turn ended as running/)
    fireEvent.click(screen.getByRole('button', { name: 'Show spending' }))
    expect(await screen.findByText('Recovered answer')).toBeTruthy()
    expect(requestIds[0]).toBe(requestIds[1])
  })
})
