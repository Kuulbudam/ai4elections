import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

type Result = { data: unknown; error: null; count?: number }

function query(result: Result, calls: string[][], table: string) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle', 'insert']) {
    q[m] = (...args: unknown[]) => {
      calls.push([table, m, ...args.map((a) => (typeof a === 'object' ? JSON.stringify(a) : String(a)))])
      return q
    }
  }
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}

const ME = 'u-analyst'
const session = { user: { id: ME, email: 'analyst@example.test' }, access_token: 'tok' }

const finding = {
  id: 'f1', project_id: 'p1', dataset_version_id: 'v1', analytical_run_id: 'r1', category: 'statistical_anomaly',
  indicator: 'turnout_rate', rule_id: null, pu_code: 'SY-01-02-03-00004', state: 'Synthia', lga: 'Alder', ward: 'Birch',
  severity: 'very_high', confidence: 'high', title: 'Turnout unusually above peers at polling unit SY-01-02-03-00004',
  explanation: 'Turnout is 91.0% (455 accredited of 500 registered).', limitations: 'A statistical outlier is a reason to check the result sheet.',
  peer_group: { level: 'ward', key: 'Synthia|Alder|Birch', sample_size: 24, fallback_used: false },
  statistics: { value: '0.910000', median: '0.350000', mad: '0.030000', robust_z: '12.5900', threshold: '3.5', direction: 'above', leading_party: null, not_scored_reason: null },
  exclusions: [{ raw_row_id: 'x1', pu_code: 'SY-01-02-03-00009', reason: 'excluded_by_validation', rules: ['V006'] }],
  evidence: [{ raw_row_id: 'w1', source_row_number: 42, cells: { 'PU Code': 'SY-01-02-03-00004', 'Accredited Voters': '455', 'Registered Voters': '500' }, flagged_columns: ['Accredited Voters'] }],
  review_status: 'needs_more_evidence', note_count: 1, claim_count: 1,
  provenance: { dataset_sha256: 'a'.repeat(64), schema_version: '1.0.0', engine_version: '1.0.0', method_version: '1.0.0', rule_version: '1.1.0' },
  created_at: '2026-11-19T06:00:00Z',
}  // prettier-ignore

function client(role: string, claimAuthor: string, calls: string[][] = []) {
  const tables: Record<string, Result> = {
    memberships: { data: { role }, error: null },
    projects: { data: { org_id: 'o1', election_name: 'SYNTHETIC Election' }, error: null },
    admin_units: { data: [{ state: 'Synthia', lga: 'Alder', ward: 'Birch' }], error: null },
    finding_reviews: { data: [{ id: 'rv1', decision: 'needs_more_evidence', reason: 'Need the original sheet.', reviewer_id: 'u-admin', created_at: '2026-11-19T07:00:00Z' }], error: null },
    investigation_notes: { data: [{ id: 'n1', body: 'Called the ward officer.', author_id: 'u-res', created_at: '2026-11-19T07:05:00Z' }], error: null },
    claims: { data: [{ id: 'c1', statement: 'Observers reported a late count.', status: 'unverified_allegation', created_by: claimAuthor, created_at: '2026-11-19T07:10:00Z', verified_by: null, verified_at: null, verification_reason: null }], error: null },
    claim_evidence: { data: [{ id: 'e1', claim_id: 'c1', kind: 'link', description: 'Observer statement', url: 'https://example.org/s', file_sha256: null, added_by: 'u-res', created_at: '2026-11-19T07:11:00Z' }], error: null },
    audit_events: { data: [{ id: 'a1', seq: 7, action: 'finding_reviews.insert', actor_id: 'u-admin', created_at: '2026-11-19T07:00:00Z', entity_type: 'finding_reviews' }], error: null },
    profiles: { data: [{ id: 'u-admin', email: 'admin@example.test', display_name: 'Ada Admin' }, { id: 'u-res', email: 'res@example.test', display_name: null }], error: null },
  }  // prettier-ignore
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query(tables[t] ?? { data: [], error: null }, calls, t)),
    rpc: vi.fn(),
  }
}

function stubApi(calls: string[]) {
  vi.stubGlobal('crypto', { randomUUID: () => 'key-1', subtle: globalThis.crypto?.subtle })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}${init?.body ? ` ${init.body}` : ''}`)
      const json = (b: unknown, status = 200) => ({ ok: status < 400, status, json: async () => b })
      if (url.includes('/findings?')) return json({ items: [finding], next_cursor: null })
      if (url.endsWith('/findings/f1')) return json(finding)
      if (url.endsWith('/reviews')) return json({ id: 'rv2' }, 201)
      if (url.endsWith('/verify')) return json({ id: 'c1', status: 'verified_fact' })
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

describe('findings dashboard', () => {
  it('lists findings from the API and sends filters', async () => {
    const calls: string[] = []
    stubApi(calls)
    setSupabaseForTests(client('viewer', 'u-res') as never)
    window.history.pushState({}, '', '/projects/p1/findings')
    render(<App />)
    expect(await screen.findByRole('link', { name: finding.title })).toBeTruthy()
    expect(screen.getByText('91.0% vs 35.0%')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('How unusual'), { target: { value: 'very_high' } })
    fireEvent.change(screen.getByLabelText('Review status'), { target: { value: 'escalated' } })
    await waitFor(() => expect(calls.some((c) => c.includes('severity=very_high') && c.includes('review_status=escalated'))).toBe(true))
    expect(calls.every((c) => !c.includes('/findings?') || c.includes('project_id=p1'))).toBe(true)
  })
})

describe('finding details', () => {
  it('shows evidence cells, history and keeps allegations labelled; analysts can review and verify others’ claims', async () => {
    const calls: string[] = []
    stubApi(calls)
    setSupabaseForTests(client('analyst', 'u-res') as never)
    window.history.pushState({}, '', '/findings/f1')
    render(<App />)
    expect(await screen.findByRole('heading', { name: finding.title })).toBeTruthy()
    expect(screen.getByText('Accredited Voters').closest('tr')?.className).toBe('flagged')
    expect(screen.getByText('Unverified allegation')).toBeTruthy()
    expect(screen.getAllByText(/Ada Admin/).length).toBeGreaterThan(0)
    expect(screen.getByText('Review decision recorded')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/fraud|rigg/i)

    fireEvent.change(await screen.findByLabelText('Decision'), { target: { value: 'escalated' } })
    fireEvent.change(screen.getByLabelText('Reason (required)'), { target: { value: 'The sheet total disagrees with the row.' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Record a review decision' }))
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/findings/f1/reviews') && c.includes('escalated'))).toBe(true))

    fireEvent.change(screen.getByLabelText('Verify as fact: reason (required)'), { target: { value: 'Matches the observer report.' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Verify claim' }))
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/claims/c1/verify'))).toBe(true))
  })

  it('viewers see everything but cannot act; authors cannot verify their own claim', async () => {
    stubApi([])
    setSupabaseForTests(client('viewer', ME) as never)
    window.history.pushState({}, '', '/findings/f1')
    render(<App />)
    expect(await screen.findByRole('heading', { name: finding.title })).toBeTruthy()
    await screen.findByText('Called the ward officer.')
    expect(screen.queryByRole('form', { name: 'Record a review decision' })).toBeNull()
    expect(screen.queryByRole('form', { name: 'Add a note' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Record an allegation' })).toBeNull()
    expect(screen.queryByRole('form', { name: 'Verify claim' })).toBeNull()
  })

  it('the author of a claim is told someone else must verify it', async () => {
    stubApi([])
    setSupabaseForTests(client('analyst', ME) as never)
    window.history.pushState({}, '', '/findings/f1')
    render(<App />)
    expect(await screen.findByText('You recorded this claim, so someone else must verify it.')).toBeTruthy()
    expect(screen.queryByRole('form', { name: 'Verify claim' })).toBeNull()
  })
})
