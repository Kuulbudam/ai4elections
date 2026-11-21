import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'
import { enqueue, flush, pending } from '../lib/outbox'

type Result = { data: unknown; error: null; count?: number }

function query(result: Result) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle', 'insert', 'update', 'is']) q[m] = () => q
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}

const session = { user: { id: 'u-f', email: 'agent@example.test' }, access_token: 'tok' }
function client(role: string) {
  const base: Record<string, Result> = {
    memberships: { data: { role }, error: null },
    projects: { data: { id: 'p1', org_id: 'o1', election_name: 'Sample Election', election_year: 2027 }, error: null },
    notifications: { data: [], error: null, count: 0 },
  }
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query(base[t] ?? { data: [], error: null })),
    rpc: vi.fn(),
  }
}

const unit = { pu_code: '31/05/13/130', state: 'Plateau', lga: 'Jos North', ward: 'Jenta' }
const json = (b: unknown, status = 200) => ({ ok: status < 400, status, json: async () => b })

function stubApi(calls: string[], opts: { offline?: boolean; refuse?: boolean } = {}) {
  vi.stubGlobal('crypto', { randomUUID: () => 'c1', subtle: globalThis.crypto?.subtle })
  const sent: unknown[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}${init?.body ? ` ${init.body}` : ''}`)
      if (url.includes('/field/assignments')) return json({ items: [unit] })
      if (url.endsWith('/field-reports') && init?.method === 'POST') {
        if (opts.offline) throw new TypeError('Failed to fetch')
        if (opts.refuse) return json({ code: 'FORBIDDEN', message: 'You can only report for your own polling unit.' }, 403)
        const body = JSON.parse(String(init.body))
        const r = { ...body, id: `r${sent.length}`, agent_id: 'u-f', severity: body.payload.severity ?? null, received_at: body.captured_at, escalation_id: body.payload.severity === 'critical' ? 'e1' : null }
        sent.push(r)
        return json(r, 201)
      }
      if (url.includes('/field-reports?')) return json({ items: sent })
      return json({ status: 'ok', version: '0.1.0' })
    }),
  )
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

async function openIncident() {
  setSupabaseForTests(client('field_agent') as never)
  window.history.pushState({}, '', '/projects/p1/field')
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Incident' }))
  fireEvent.change(screen.getByLabelText('What kind?'), { target: { value: 'ballot_snatching' } })
  fireEvent.change(screen.getByLabelText('How serious?'), { target: { value: 'critical' } })
  fireEvent.change(screen.getByLabelText('What happened?'), { target: { value: 'Thugs took the box at 2pm.' } })
  fireEvent.click(screen.getAllByLabelText('Yes')[0])
  fireEvent.submit(screen.getByRole('form', { name: 'Incident' }))
}

describe('field app', () => {
  it('an agent with one polling unit reports a critical incident and sees it went to the coordinator', async () => {
    const calls: string[] = []
    stubApi(calls)
    await openIncident()
    expect(await screen.findByText(/sent to your coordinator/)).toBeTruthy()
    const post = calls.find((c) => c.startsWith('POST https://api.test/field-reports'))!
    expect(post).toContain('"pu_code":"31/05/13/130"')
    expect(post).toContain('"category":"ballot_snatching"')
    expect(pending()).toHaveLength(0)
  })

  it('with no signal the report waits on the phone and goes out later', async () => {
    const calls: string[] = []
    stubApi(calls, { offline: true })
    await openIncident()
    expect(await screen.findByText(/1 waiting to send/)).toBeTruthy()
    expect(pending()).toHaveLength(1)
    stubApi(calls)
    expect(await flush()).toEqual({ sent: 1, waiting: 0 })
    expect(pending()).toHaveLength(0)
  })

  it('a refused report is kept with the reason and can be removed', async () => {
    stubApi([], { refuse: true })
    await openIncident()
    expect(await screen.findByText(/You can only report for your own polling unit/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(pending()).toHaveLength(0))
  })

  it('sends each report once, even when flushed twice', async () => {
    const calls: string[] = []
    stubApi(calls)
    setSupabaseForTests(client('field_agent') as never)
    enqueue({ client_id: 'abc', project_id: 'p1', kind: 'arrival', pu_code: unit.pu_code, captured_at: new Date().toISOString(), location: null, payload: {} })
    await flush()
    await flush()
    expect(calls.filter((c) => c.startsWith('POST')).length).toBe(1)
    expect(calls.find((c) => c.startsWith('POST'))).toContain('"client_id":"abc"')
  })
})

describe('dashboard for field agents', () => {
  it('shows a report button instead of the analyst to-do list', async () => {
    stubApi([])
    const c = client('field_agent')
    c.from = vi.fn((t: string) =>
      query(
        t === 'memberships'
          ? { data: [{ org_id: 'o1', role: 'field_agent', organisations: { id: 'o1', name: 'Plateau Observers' } }], error: null }
          : t === 'projects'
            ? { data: [{ id: 'p1', org_id: 'o1', election_name: 'Sample Election', election_year: 2027 }], error: null }
            : { data: [], error: null, count: 0 },
      ),
    )
    setSupabaseForTests(c as never)
    window.history.pushState({}, '', '/')
    render(<App />)
    expect(await screen.findByText('Report from your polling unit')).toBeTruthy()
    expect((await screen.findByRole('link', { name: 'Sample Election' })).getAttribute('href')).toBe('/projects/p1/field')
  })
})
