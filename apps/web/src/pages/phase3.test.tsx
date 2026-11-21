import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { parseSimpleCsv } from '../lib/csv'
import { setSupabaseForTests } from '../lib/supabase'

type Result = { data: unknown; error: null; count?: number }

function query(result: Result, calls: string[][]) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'insert', 'limit', 'maybeSingle']) {
    q[m] = (...args: unknown[]) => {
      calls.push([m, ...args.map(String)])
      return q
    }
  }
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}

const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }
const summary = {
  rows: 12023,
  row_status: { eligible: 11960, eligible_with_warnings: 7, quarantined_from_specific_analysis: 56 },
  excluded_from: { turnout_rate: 40, leading_party_share: 52 },
  issues_by_rule: { V004: 9, V010: 10 },
  rows_by_rule: { V004: 9, V010: 10 },
  reference_list_used: false,
  ruleset_version: '1.0.0',
}

function client(role: string, calls: string[][] = []) {
  const tables: Record<string, Result> = {
    memberships: { data: { role }, error: null },
    dataset_versions: {
      data: { org_id: 'o1', project_id: 'p1', original_filename: 'SYNTHETIC.csv', version_number: 1, status: 'ingested', mapping_saved_at: 'x', validated_at: '2026-11-18T20:00:00Z', validation_summary: summary },
      error: null,
    },
    validation_rules: {
      data: [
        { rule_id: 'V004', definition: { severity: 'error', description: 'A count is not a whole number', remediation: 'Correct the value', classification: 'structural' } },
        { rule_id: 'V011', definition: { severity: 'warning', description: 'Unmatched label', remediation: 'Fix spelling', classification: 'geography' } },
      ],
      error: null,
    },
    validation_issues: {
      data: [{ id: 'i1', rule_id: 'V004', severity: 'error', source_row_number: 57, source_column: 'Registered Voters', original_value: 'abc', message: "Registered Voters is not a whole number: 'abc'." }],
      error: null,
      count: 19,
    },
  }
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

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  window.history.pushState({}, '', '/versions/v1/validation')
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('validation page', () => {
  it('shows summary, honest V011 status and issues traced to source cells', async () => {
    setSupabaseForTests(client('viewer') as never)
    render(<App />)
    expect(await screen.findByText('11,960')).toBeTruthy()
    expect(screen.getByText(/weren’t checked against an official list \(rule V011\)/)).toBeTruthy()
    expect(screen.getByText('not checked')).toBeTruthy()
    expect(await screen.findByText('abc')).toBeTruthy()
    expect(screen.getByText('57')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /run validation/i })).toBeNull() // viewers cannot run it
  })

  it('lets a researcher run validation and polls the job', async () => {
    setSupabaseForTests(client('researcher') as never)
    vi.stubGlobal('crypto', { randomUUID: () => 'k' })
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => (url.endsWith('/jobs') ? { id: 'j1', status: 'queued' } : { id: 'j1', status: 'succeeded' }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u) === 'https://api.test/jobs')), { timeout: 2000 })
    const body = JSON.parse((fetchMock.mock.calls.find(([u]) => String(u) === 'https://api.test/jobs') as unknown as [string, RequestInit])[1].body as string)
    expect(body).toEqual({ job_type: 'validate', dataset_version_id: 'v1' })
  })

  it('filters issues by rule', async () => {
    const calls: string[][] = []
    setSupabaseForTests(client('analyst', calls) as never)
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Filter by rule'), { target: { value: 'V010' } })
    await waitFor(() => expect(calls.some((c) => c[0] === 'eq' && c[1] === 'rule_id' && c[2] === 'V010')).toBe(true))
  })
})

describe('csv parser for reference lists', () => {
  it('handles quotes, escaped quotes, CRLF, BOM and blank lines', () => {
    expect(parseSimpleCsv('﻿state,lga,ward\r\n"Syn, thia","A ""B""",W1\r\n\r\nN,L,W2')).toEqual([
      ['state', 'lga', 'ward'],
      ['Syn, thia', 'A "B"', 'W1'],
      ['N', 'L', 'W2'],
    ])
  })
})
