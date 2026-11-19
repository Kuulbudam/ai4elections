import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

function query(result: unknown) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle', 'not', 'is']) q[m] = () => q
  q.then = (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve)
  return q
}
const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }
const f = (id: string, category: string, severity: string, extra = {}) => ({
  finding_id: id, category, indicator: category === 'statistical_anomaly' ? 'turnout_rate' : null, rule_id: category === 'statistical_anomaly' ? null : 'V003',
  pu_code: `PU-${id}`, lga: 'Alder', ward: 'Birch', severity, confidence: 'high', title: `Finding ${id}`,
  statistics: category === 'statistical_anomaly' ? { value: '0.910000', median: '0.350000', robust_z: '12.5900' } : null,
  peer_group: category === 'statistical_anomaly' ? { n: 24, fallback_used: false } : null,
  evidence: { source_row_number: 42 }, review_status: 'unreviewed', review_history: [], ...extra,
})  // prettier-ignore
const report = {
  id: 'r1', org_id: 'o1', project_id: 'p1', title: 'Findings report: Plateau', content_hash: 'c'.repeat(64), audit_event_id: 'e1', audit_event_hash: 'd'.repeat(64),
  generated_at: '2026-11-19T12:00:00Z', generated_by: 'u1',
  body: {
    project: { election_name: 'Plateau 2023', election_year: 2023, country: 'Nigeria', election_level: 'presidential' },
    dataset: { name: 'Results', version: 1, file: 'plateau.csv', sha256: 'a'.repeat(64), rows: 4989, source_type: 'observer_collation', source_description: 'CCIJ/VEZA', uploaded_at: '2026-11-19T10:00:00Z' },
    analysis: { run_id: 'run1', engine_version: '1.0.0', method_version: '1.0.0', rule_versions: { ruleset: '1.1.0' }, parameters: { min_peers: 10, threshold: 3.5 }, run_content_hash: 'b'.repeat(64), completed_at: '2026-11-19T11:00:00Z' },
    methodology: 'Units are compared with their ward.',
    validation: { rows: 4989, row_status: { eligible: 3543 }, issues_by_rule: { V003: 3308 }, rows_by_rule: { V003: 1016 }, ruleset_version: '1.1.0' },
    findings: [f('1', 'statistical_anomaly', 'very_high', { review_status: 'escalated', review_history: [{ decision: 'escalated', reason: 'Sheet needs a check.', reviewer: 'Ada', at: '2026-11-19T11:30:00Z' }] }), f('2', 'data_quality_issue', 'medium')],
    claims: [{ claim_id: 'c1', statement: 'Count started late.', label: 'UNVERIFIED ALLEGATION', status: 'unverified_allegation', recorded_by: 'Res', verified_by: null, verification_reason: null, evidence: [] }],
    limitations: 'Statistical anomalies are not evidence of fraud.',
  },
}  // prettier-ignore
function client(role: string) {
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query({ data: t === 'memberships' ? { role } : t === 'profiles' ? { display_name: 'Ana Analyst' } : report, error: null })),
    rpc: vi.fn(),
  }
}
beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ status: 'ok' }) })))
  window.history.pushState({}, '', '/reports/r1')
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('report view', () => {
  it('shows provenance, hashes, findings, reviews and labelled claims; analysts can export', async () => {
    setSupabaseForTests(client('analyst') as never)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Findings report: Plateau' })).toBeTruthy()
    expect(screen.getByText('c'.repeat(64))).toBeTruthy()
    expect(screen.getByText('a'.repeat(64))).toBeTruthy()
    expect(screen.getByText('UNVERIFIED ALLEGATION')).toBeTruthy()
    expect(screen.getByText(/Sheet needs a check/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'PU-1' }).getAttribute('href')).toBe('/findings/1')
    expect(screen.getByRole('button', { name: 'Print / save as PDF' })).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Download CSV' })).toBeTruthy()
    expect(screen.getAllByText(/not evidence of fraud/).length).toBeGreaterThan(0)
  })

  it('viewers can read and print but not export', async () => {
    setSupabaseForTests(client('viewer') as never)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Findings report: Plateau' })).toBeTruthy()
    await screen.findByText(/Ana Analyst/)
    expect(screen.queryByRole('button', { name: 'Download CSV' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Print / save as PDF' })).toBeTruthy()
  })
})
