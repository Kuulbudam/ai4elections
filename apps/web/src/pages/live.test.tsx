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
const session = { user: { id: 'u-admin', email: 'admin@example.test' }, access_token: 'tok' }
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

const counts = { polling_units_with_results: 1, field_reports: 3, incidents: 1, critical_incidents: 1, escalations: 1, open_escalations: 1 }
const pack = { id: 'k1', project_id: 'p1', created_at: '2026-11-21T18:00:00Z', created_by_name: 'Ada Admin', manifest_sha256: 'a'.repeat(64), zip_sha256: 'b'.repeat(64), zip_bytes: 900, key_id: 'c'.repeat(16), counts }
const summary = {
  generated_at: '2026-11-21T17:59:00Z',
  last_report_at: '2026-11-21T17:58:00Z',
  coverage: { assigned_units: 4, reporting_units: 3, opened: 3, accreditation: 2, results: 1, closing: 0 },
  incidents: {
    total: 1, critical: 1, open_escalations: 1,
    by_category: [{ category: 'ballot_snatching', label: 'Ballot box snatched', count: 1 }],
    recent: [{ id: 'r1', pu_code: '31/05/13/130', state: 'Plateau', lga: 'Jos North', ward: 'Jenta', category: 'ballot_snatching', label: 'Ballot box snatched', severity: 'critical', description: 'Box taken at 2pm.', captured_at: '2026-11-21T13:00:00Z', escalation_id: 'e1', escalation_status: 'open' }],
  },
  results: { units: 1, with_failed_checks: 0, party_totals: [{ party: 'APC', votes: 120 }, { party: 'PDP', votes: 95 }], registered_voters: 700, accredited_voters: 230, valid_votes: 215, rejected_votes: 5, total_cast: 220 },
  areas: [{ state: 'Plateau', lga: 'Jos North', assigned: 4, reporting: 3, incidents: 1, critical: 1, results: 1 }],
}  // prettier-ignore

function stubApi(calls: string[]) {
  vi.stubGlobal('crypto', { randomUUID: () => 'key-1', subtle: globalThis.crypto?.subtle })
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}${init?.body ? ` ${String(init.body).slice(0, 80)}` : ''}`)
      const json = (b: unknown, status = 200) => ({ ok: status < 400, status, json: async () => b })
      if (url.endsWith('/projects/p1/live')) return json(summary)
      if (url.endsWith('/projects/p1/submission-packs')) return init?.method === 'POST' ? json(pack, 201) : json({ items: [] })
      if (url.endsWith('/submission-packs/k1/download')) return json({ filename: 'eaas-pack.zip', zip_sha256: 'b'.repeat(64), zip_base64: btoa('PK') })
      if (url.endsWith('/submission-packs/verify'))
        return json({
          valid: false, manifest_sha256: 'a'.repeat(64), pack_id: 'k1', generated_at: '2026-11-21T18:00:00Z', organisation: 'Plateau Observers', election: 'Sample Election', key_id: 'c'.repeat(16), counts,
          checks: [
            { id: 'signature', ok: true, title: 'Signature matches the manifest', detail: 'ok' },
            { id: 'files', ok: false, title: 'Every file matches the manifest', detail: 'Changed or missing: incidents.csv.' },
          ],
        })  // prettier-ignore
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

describe('live view', () => {
  it('shows coverage, votes, incidents and areas; an admin makes and downloads a signed pack', async () => {
    const calls: string[] = []
    stubApi(calls)
    setSupabaseForTests(client('admin') as never)
    window.history.pushState({}, '', '/projects/p1/live')
    render(<App />)
    expect(await screen.findByText('Polling units reporting')).toBeTruthy()
    expect(screen.getByText('of 4 with an observer')).toBeTruthy()
    expect(screen.getByText('Ballot box snatched')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Escalated' }).getAttribute('href')).toBe('/escalations/e1')
    expect(screen.getByRole('img', { name: 'Ranked by votes' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: 'Jos North' })).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Make signed pack' }))
    await waitFor(() => expect(calls.some((c) => c.startsWith('GET https://api.test/submission-packs/k1/download'))).toBe(true))
    expect(calls.some((c) => c.startsWith('POST https://api.test/projects/p1/submission-packs'))).toBe(true)
    expect(await screen.findByRole('list', { name: 'Packs made' })).toBeTruthy()
  })

  it('only admins can make a pack', async () => {
    stubApi([])
    setSupabaseForTests(client('viewer') as never)
    window.history.pushState({}, '', '/projects/p1/live')
    render(<App />)
    expect(await screen.findByText('Polling units reporting')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Make signed pack' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Check a pack' })).toBeTruthy()
  })
})

describe('check a pack', () => {
  it('uploads a zip and lists each check, saying plainly when it fails', async () => {
    const calls: string[] = []
    stubApi(calls)
    setSupabaseForTests(client('viewer') as never)
    window.history.pushState({}, '', '/verify')
    render(<App />)
    const input = (await screen.findByText('Choose a pack')).parentElement!.querySelector('input')!
    fireEvent.change(input, { target: { files: [new File(['PK'], 'pack.zip', { type: 'application/zip' })] } })
    expect(await screen.findByText('This pack did not pass')).toBeTruthy()
    expect(screen.getByText('Fail: Every file matches the manifest')).toBeTruthy()
    expect(screen.getByText('Pass: Signature matches the manifest')).toBeTruthy()
    expect(calls.some((c) => c.startsWith('POST https://api.test/submission-packs/verify'))).toBe(true)
  })
})
