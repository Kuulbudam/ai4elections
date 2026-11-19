import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

function query(result: unknown) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle']) q[m] = () => q
  q.then = (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve)
  return q
}
const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }
function client(role: string) {
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query({ data: t === 'memberships' ? { role } : { org_id: 'o1', election_name: 'Plateau 2023' }, error: null })),
    rpc: vi.fn(),
  }
}
function stub(enabled: boolean, calls: string[] = []) {
  vi.stubGlobal('crypto', { randomUUID: () => 'k1' })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url} ${init?.body ?? ''}`)
      const json = (b: unknown) => ({ ok: true, status: 200, json: async () => b })
      if (url.endsWith('/assistant/status')) return json({ enabled, model: enabled ? 'llama' : null })
      if (url.endsWith('/assistant/ask'))
        return json({
          id: 'q1',
          answer: 'JOS NORTH has the most flags (12).\n- 31/04/01/003: 91.0% vs 35.0%\n<script>alert(1)</script>',
          citations: [{ kind: 'finding', label: 'Turnout unusually above peers at 31/04/01/003', href: '/findings/f1' }],
          lookups: [{ name: 'search_findings', ok: true }],
          grounded: true,
          note: null,
          model: 'llama',
        })
      return json({ status: 'ok' })
    }),
  )
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  window.history.pushState({}, '', '/projects/p1/assistant')
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('data assistant page', () => {
  it('asks, shows the answer as text with source links and the AI label', async () => {
    const calls: string[] = []
    stub(true, calls)
    setSupabaseForTests(client('analyst') as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Which LGAs have the most unusual turnout?' }))
    expect(await screen.findByText('JOS NORTH has the most flags (12).')).toBeTruthy()
    expect(screen.getByText('31/04/01/003: 91.0% vs 35.0%').tagName).toBe('LI')
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy() // shown as text, never run
    expect(screen.getByRole('link', { name: /Turnout unusually above peers/ }).getAttribute('href')).toBe('/findings/f1')
    expect(screen.getByText(/AI-drafted \(llama\); not a finding/)).toBeTruthy()
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/assistant/ask') && c.includes('"project_id":"p1"'))).toBe(true))
  })

  it('does not blank the page when the browser returns a Promise from scrollIntoView (newer Chrome)', async () => {
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function () {
      return Promise.resolve() as never
    }
    try {
      stub(true)
      setSupabaseForTests(client('analyst') as never)
      render(<App />)
      fireEvent.click(await screen.findByRole('button', { name: 'Which LGAs have the most unusual turnout?' }))
      expect(await screen.findByText('JOS NORTH has the most flags (12).')).toBeTruthy()
      fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'And the second most?' } })
      fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
      await waitFor(() => expect(screen.getAllByRole('article', { name: 'Answer' })).toHaveLength(2))
      expect(screen.getByRole('heading', { name: /Ask the data/ })).toBeTruthy()
      expect(screen.queryByText('This page hit a problem')).toBeNull()
    } finally {
      Element.prototype.scrollIntoView = original
    }
  })

  it('tells researchers it is for admins and analysts, and says when it is not set up', async () => {
    stub(true)
    setSupabaseForTests(client('researcher') as never)
    render(<App />)
    expect(await screen.findByText('The data assistant is available to admins and analysts.')).toBeTruthy()
    expect(screen.queryByRole('form', { name: 'Ask a question' })).toBeNull()
    cleanup()
    stub(false)
    setSupabaseForTests(client('admin') as never)
    render(<App />)
    expect(await screen.findByText(/not set up yet/)).toBeTruthy()
  })
})
