import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

type Result = { data: unknown; error: null; count?: number }

function query(result: Result, calls: string[][]) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle']) {
    q[m] = (...args: unknown[]) => {
      calls.push([m, ...args.map(String)])
      return q
    }
  }
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}

const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }
const ind = (flagged: number, vh: number) => ({
  eligible: 11990, scored: 11970, flagged, lga_fallback: 12,
  not_scored: { excluded_by_validation: 33, insufficient_peers: 8, zero_mad: 12 },
  flagged_by_severity: { medium: flagged - vh - 3, high: 3, very_high: vh },
})  // prettier-ignore

function client(role: string, latestRun: string | null, calls: string[][] = []) {
  const tables: Record<string, Result> = {
    memberships: { data: { role }, error: null },
    dataset_versions: {
      data: { org_id: 'o1', project_id: 'p1', original_filename: 'SYNTHETIC.csv', version_number: 1, validated_at: '2026-11-19T06:00:00Z', latest_analysis_run_id: latestRun },
      error: null,
    },
    analytical_runs: {
      data: {
        id: 'run1', engine_version: '1.0.0', method_version: '1.0.0', parameters: { min_peers: 10, threshold: 3.5 },
        content_hash: 'a'.repeat(64), completed_at: '2026-11-19T06:01:00Z',
        summary: { units: 12023, indicators: { turnout_rate: ind(49, 15), leading_party_share: ind(91, 23) }, statistical_findings: 140, data_quality_findings: 79, data_quality_by_rule: {} },
        not_scored: [{ indicator: 'turnout_rate', pu_code: 'SY-04-01-01-00001', source_row_number: 9001, reason: 'zero_mad' }],
      },
      error: null,
    },
    findings: {
      data: [{ id: 'f1', indicator: 'turnout_rate', pu_code: 'SY-01-02-03-00004', lga: 'Alder', ward: 'Birch', severity: 'very_high', confidence: 'high', peer_group: { level: 'ward', n: 24 }, statistics: { value: 0.91, median: 0.35, robust_z: 11.2 } }],
      error: null,
      count: 38,
    },
  }  // prettier-ignore
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query(tables[t] ?? { data: [], error: null }, calls)),
    rpc: vi.fn(),
  }
}

const dashboard = {
  dataset_version_id: 'v1', analytical_run_id: 'run1', lgas: ['Synthia / Alder', 'Synthia / Birch'],
  units: [[0, 0.35, 0.5], [0, 0.36, 0.52], [1, 0.91, 0.6], [1, null, null]],
  flagged: [{ finding_id: 'f1', pu_code: 'SY-01-02-03-00004', ward: 'Birch', lga: 1, indicator: 'turnout_rate', value: 0.91, median: 0.35, robust_z: 12.59, severity: 'very_high', review_status: 'unreviewed' }],
  kpi: { units: 12023, turnout_counted: 11990, share_counted: 11979, unusual_turnout: 49, unusual_share: 91, very_high: 38, data_quality: 79, to_review: 55 },
  severity: { turnout_rate: { medium: 31, high: 3, very_high: 15 } }, data_quality_by_rule: { V003: 12, V004: 9 },
}  // prettier-ignore

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  window.history.pushState({}, '', '/versions/v1/analysis')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => (url.includes('/dashboard') ? dashboard : { status: 'ok' }) })))
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('analysis page', () => {
  it('shows the run summary, the strongest findings first, and plain wording', async () => {
    const calls: string[][] = []
    setSupabaseForTests(client('viewer', 'run1', calls) as never)
    render(<App />)
    expect(await screen.findByText('SY-01-02-03-00004')).toBeTruthy()
    const stats = screen.getByRole('list', { name: 'Analysis summary' })
    expect(within(stats).getByText('49')).toBeTruthy()
    expect(within(stats).getByText('91')).toBeTruthy()
    expect(screen.getByText('91.0%')).toBeTruthy()
    expect(screen.getByText('+11.20')).toBeTruthy()
    expect(screen.getByText(/not a conclusion about what happened/)).toBeTruthy()
    expect(calls).toContainEqual(['eq', 'severity', 'very_high'])
    expect(calls).toContainEqual(['eq', 'category', 'statistical_anomaly'])
    expect(document.body.textContent).not.toMatch(/fraud|rigg/i)
    expect(screen.queryByRole('button', { name: /Run analysis/ })).toBeNull() // viewers cannot run
  })

  it('draws the dashboard charts and cross-filters by area', async () => {
    setSupabaseForTests(client('viewer', 'run1') as never)
    render(<App />)
    expect(await screen.findByRole('img', { name: /Turnout: distribution across 3 polling units/ })).toBeTruthy()
    expect(screen.getByRole('group', { name: /against Peer group median for 1 unusual units/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Synthia \/ Birch: 1 unusual units/ }))
    expect(await screen.findByRole('img', { name: /distribution across 1 polling units/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: 'Leading-party share' }))
    expect(await screen.findByText('No unusual units for this indicator.')).toBeTruthy()
  })

  it('offers to run analysis when none exists', async () => {
    setSupabaseForTests(client('researcher', null) as never)
    render(<App />)
    expect(await screen.findByText('This version has not been analysed yet.')).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Run analysis' })).toBeTruthy()
  })
})
