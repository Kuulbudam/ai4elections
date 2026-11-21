import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

type Result = { data: unknown; error: null; count?: number }

function query(result: Result, calls: string[][], table: string) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle', 'insert', 'update', 'is']) {
    q[m] = (...args: unknown[]) => {
      calls.push([table, m, ...args.map((a) => (typeof a === 'object' ? JSON.stringify(a) : String(a)))])
      return q
    }
  }
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}

const session = { user: { id: 'u-admin', email: 'admin@example.test' }, access_token: 'tok' }

function client(role: string, calls: string[][] = [], tables: Record<string, Result> = {}) {
  const base: Record<string, Result> = {
    memberships: { data: { role }, error: null },
    projects: { data: { id: 'p1', org_id: 'o1', election_name: 'Sample Election', election_year: 2027 }, error: null },
    escalations: { data: { project_id: 'p1' }, error: null },
    notifications: { data: [], error: null, count: 2 },
    ...tables,
  }
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query(base[t] ?? { data: [], error: null }, calls, t)),
    rpc: vi.fn(),
  }
}

const escalation = (over: Record<string, unknown> = {}) => ({
  id: 'e1', project_id: 'p1', source_kind: 'finding', source_id: 'f1', title: 'Turnout unusually above peers at SY-9',
  area: { state: 'Synthia', lga: 'Alder', ward: 'Birch', pu_code: 'SY-9' }, raised_by: 'u-an', raised_by_name: 'Ana Analyst',
  reason: 'Needs a senior decision.', created_at: '2026-11-21T09:00:00Z', current_level: 'admin', status: 'open',
  resolved_at: null, resolution: null, can_resolve: true, can_send_up: false,
  events: [{ id: 'ev1', kind: 'raised', level: 'admin', actor_id: 'u-an', actor_name: 'Ana Analyst', note: 'Needs a senior decision.', created_at: '2026-11-21T09:00:00Z' }],
  ...over,
})  // prettier-ignore

function stubApi(calls: string[], resolved = false) {
  vi.stubGlobal('crypto', { randomUUID: () => 'key-1', subtle: globalThis.crypto?.subtle })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}${init?.body ? ` ${init.body}` : ''}`)
      const json = (b: unknown, status = 200) => ({ ok: status < 400, status, json: async () => b })
      if (url.includes('/escalations?')) return json({ items: [escalation()] })
      if (url.endsWith('/escalations/e1/resolve')) {
        resolved = true
        return json(escalation({ status: 'resolved', can_resolve: false, resolution: 'Checked the sheet.' }))
      }
      if (url.endsWith('/escalations/e1'))
        return json(resolved ? escalation({ status: 'resolved', can_resolve: false, resolution: 'Checked the sheet.' }) : escalation())
      return json({ status: 'ok', version: '0.1.0' })
    }),
  )
}

beforeEach(() => vi.stubEnv('VITE_API_BASE_URL', 'https://api.test'))
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('escalations', () => {
  it('lists open escalations and marks the ones waiting for you', async () => {
    stubApi([])
    setSupabaseForTests(client('admin') as never)
    window.history.pushState({}, '', '/projects/p1/escalations')
    render(<App />)
    expect(await screen.findByRole('link', { name: 'Turnout unusually above peers at SY-9' })).toBeTruthy()
    expect(screen.getByText('your turn')).toBeTruthy()
    expect(screen.getByText('Synthia / Alder / Birch / SY-9')).toBeTruthy()
  })

  it('an admin resolves with a note and the page shows it resolved', async () => {
    const calls: string[] = []
    stubApi(calls)
    setSupabaseForTests(client('admin') as never)
    window.history.pushState({}, '', '/escalations/e1')
    render(<App />)
    expect(await screen.findByText(/Open · with Admins/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('How was it resolved?'), { target: { value: 'Checked the sheet.' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Resolve' }))
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/escalations/e1/resolve') && c.includes('Checked the sheet.'))).toBe(true))
    expect(await screen.findByText('Resolved', { selector: '.badge' })).toBeTruthy()
    expect(screen.queryByRole('form', { name: 'Resolve' })).toBeNull()
  })

  it('people who cannot act see who it is waiting for', async () => {
    stubApi([])
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => escalation({ can_resolve: false }) })))
    setSupabaseForTests(client('viewer') as never)
    window.history.pushState({}, '', '/escalations/e1')
    render(<App />)
    expect(await screen.findByText('Waiting for admins to act.')).toBeTruthy()
    expect(screen.queryByRole('form', { name: 'Resolve' })).toBeNull()
  })
})

describe('inbox', () => {
  it('shows unread count, opens a notification and marks it read', async () => {
    const calls: string[][] = []
    stubApi([])
    setSupabaseForTests(
      client('admin', calls, {
        notifications: {
          data: [
            { id: 'n1', kind: 'escalation_raised', title: 'Escalated to you: Turnout at SY-9', body: 'Needs a senior decision.', link: '/escalations/e1', created_at: '2026-11-21T09:00:00Z', read_at: null },
          ],
          error: null,
          count: 1,
        },
      }) as never,
    )
    window.history.pushState({}, '', '/inbox')
    render(<App />)
    expect(await screen.findByRole('link', { name: 'Inbox, 1 unread' })).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: /Escalated to you: Turnout at SY-9/ }))
    await waitFor(() => expect(calls.some((c) => c[0] === 'notifications' && c[1] === 'update')).toBe(true))
    expect(await screen.findByText(/Open · with Admins/)).toBeTruthy()
  })
})
