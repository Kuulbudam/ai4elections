import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

type Result = { data: unknown; error: null; count?: number }
function query(result: Result) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle', 'insert', 'update', 'is']) q[m] = () => q
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}
const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }
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
const written = {
  id: 's1', project_id: 'p1', created_at: '2026-11-21T19:00:00Z', created_by_name: 'Ana Analyst', language: 'en', model: 'llama',
  facts: { findings: 24 }, summary: 'There are 24 findings. JOS NORTH has the most, with 9.', grounded: true, note: null, stale: false,
}  // prettier-ignore

function stub(calls: string[], existing: unknown | null) {
  vi.stubGlobal('crypto', { randomUUID: () => 'k1' })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url} ${init?.body ?? ''}`)
      const json = (b: unknown, status = 200) => ({ ok: status < 400, status, json: async () => b })
      if (url.includes('/findings-summary'))
        return init?.method === 'POST' ? json(written, 201) : existing ? json(existing) : json({ code: 'NOT_FOUND', message: 'No summary has been written yet.' }, 404)
      if (url.includes('/findings?')) return json({ items: [], next_cursor: null })
      return json({ status: 'ok', version: '0.1.0' })
    }),
  )
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  window.history.pushState({}, '', '/projects/p1/findings')
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('findings summary', () => {
  it('an analyst writes the summary; it is labelled as written by AI and checked', async () => {
    const calls: string[] = []
    stub(calls, null)
    setSupabaseForTests(client('analyst') as never)
    render(<App />)
    expect(await screen.findByText(/No summary yet/)).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Write summary' }))
    expect(await screen.findByText('There are 24 findings. JOS NORTH has the most, with 9.')).toBeTruthy()
    expect(screen.getByText(/Written by AI from the counts on this page/)).toBeTruthy()
    expect(screen.getByText(/Every number was checked against the findings/)).toBeTruthy()
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/projects/p1/findings-summary') && c.includes('"language":"en"'))).toBe(true))
  })

  it('viewers read it, see when it is out of date, and cannot write one', async () => {
    stub([], { ...written, stale: true, grounded: false, note: 'ungrounded' })
    setSupabaseForTests(client('viewer') as never)
    render(<App />)
    expect(await screen.findByText('The findings have changed since this was written.')).toBeTruthy()
    expect(screen.getByText(/Some numbers could not be checked/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Write/ })).toBeNull()
  })

  it('changing the language loads that summary', async () => {
    const calls: string[] = []
    stub(calls, written)
    setSupabaseForTests(client('analyst') as never)
    render(<App />)
    await screen.findByText('There are 24 findings. JOS NORTH has the most, with 9.')
    fireEvent.change(screen.getByLabelText('In'), { target: { value: 'ha' } })
    await waitFor(() => expect(calls.some((c) => c.includes('/findings-summary?language=ha'))).toBe(true))
  })
})
