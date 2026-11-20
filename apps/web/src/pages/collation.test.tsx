import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
function client(role: string) {
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) =>
      query({
        data:
          t === 'memberships'
            ? { role }
            : t === 'dataset_versions'
              ? [{ id: 'v1', original_filename: 'results.csv', version_number: 1 }]
              : t === 'projects'
                ? { org_id: 'o1', election_name: 'Plateau 2023', id: 'p1', election_year: 2023 }
                : [],
        error: null,
      }),
    ),
    rpc: vi.fn(),
  }
}


const area = (o: Record<string, unknown>) => ({ level: 'ward', state: 'Synthia', lga: 'Amber North', ward: null, row: 2, units: 10, duplicate_units: 0, figures: [], finding_id: null, ...o })
const check = {
  id: 'c1',
  project_id: 'p1',
  dataset_version_id: 'v1',
  filename: 'declared.csv',
  sha256: 'b'.repeat(64),
  summary: { areas: 3, match: 1, mismatch: 1, incomplete: 1, unmatched: 0, rows_skipped: 1, parties: ['PA'], ignored_columns: ['Source'] },
  skipped: [{ row: 9, reason: 'No figures on this row.' }],
  areas: [
    area({ ward: 'Amber Ward 1', status: 'match', row: 2, figures: [{ field: 'valid_votes', label: 'Total valid votes', declared: 10, data: 10, diff: 0, match: true, missing_units: 0 }] }),
    area({
      ward: 'Amber Ward 2',
      status: 'mismatch',
      row: 3,
      figures: [{ field: 'party:PA', label: 'PA votes', declared: 1215, data: 1095, diff: 120, match: false, missing_units: 0 }],
      finding_id: 'f9',
    }),
    area({ ward: 'Amber Ward 3', status: 'incomplete', row: 4, figures: [{ field: 'rejected_votes', label: 'Rejected votes', declared: 5, data: 3, diff: null, match: null, missing_units: 2 }] }),
  ],
  finding_ids: ['f9'],
  created_at: '2026-11-20T10:00:00Z',
  created_by: 'u1',
}

let calls: string[] = []
function stub() {
  calls = []
  vi.stubGlobal('crypto', { randomUUID: () => 'k1' })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url} ${init?.body ?? ''}`)
      const json = (b: unknown, status = 200) => ({ ok: status < 400, status, json: async () => b })
      if (url.includes('/collation-checks?project_id=')) return json({ items: [check] })
      if (url.endsWith('/collation-checks') && init?.method === 'POST') return json(check, 201)
      return json({ status: 'ok' })
    }),
  )
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('collation check', () => {
  it('shows differences first with a link to the finding, and what could not be checked', async () => {
    stub()
    setSupabaseForTests(client('viewer') as never)
    window.history.pushState({}, '', '/projects/p1/collation')
    render(<App />)
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row')
    expect(within(rows[1]).getByText('Synthia / Amber North / Amber Ward 2')).toBeTruthy()
    expect(within(rows[1]).getByText('Differs')).toBeTruthy()
    expect(within(rows[1]).getByText(/PA votes: declared 1,215, units add up to 1,095/)).toBeTruthy()
    expect(within(rows[1]).getByRole('link', { name: 'Open the finding' }).getAttribute('href')).toBe('/findings/f9')
    expect(within(rows[2]).getByText(/Rejected votes \(2 units missing it\)/)).toBeTruthy()
    expect(screen.getByText('1 row(s) of the file were not used')).toBeTruthy()
    expect(screen.queryByText('Check declared totals')).toBeNull() // viewers cannot run a check
  })

  it('analysts upload a CSV and see the result', async () => {
    stub()
    setSupabaseForTests(client('analyst') as never)
    window.history.pushState({}, '', '/projects/p1/collation')
    render(<App />)
    const input = (await screen.findByLabelText(/Choose the declared totals/)) as HTMLInputElement
    expect(screen.getByRole('link', { name: 'Download a sample file' }).getAttribute('href')).toBe('/samples/SYNTHETIC_declared_totals.csv')
    const file = new File(['State,LGA,Ward,Valid Votes\nSynthia,Amber North,Amber Ward 1,10\n'], 'declared.csv', { type: 'text/csv' })
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/collation-checks') && c.includes('"filename":"declared.csv"') && c.includes('Amber Ward 1,10'))).toBe(true))
    expect(await screen.findByRole('list', { name: 'Collation summary' })).toBeTruthy()
  })
})
