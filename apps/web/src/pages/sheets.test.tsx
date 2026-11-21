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

const figures = {
  pu_code: '31/04/01/003',
  state: 'Plateau',
  lga: 'Jos North',
  ward: 'Ward 1',
  polling_unit_name: 'LGEA School',
  registered_voters: 1305,
  accredited_voters: 329,
  rejected_votes: 6,
  valid_votes: 319,
  total_cast: 325,
  party_votes: [
    { party: 'APC', votes: 200 },
    { party: 'PDP', votes: 104 },
  ],
}
const read = {
  id: 's1',
  project_id: 'p1',
  dataset_version_id: 'v1',
  status: 'read',
  model: 'vision-test',
  reading: { status: 'read', figures, confidence: { pu_code: 'high', valid_votes: 'low', 'party:APC': 'high', 'party:PDP': 'medium' }, legible: true, notes: 'Slight glare.' },
  confirmed: null,
  checks: null,
  comparison: null,
  finding_ids: [],
  image_sha256: 'a'.repeat(64),
  image_url: 'https://storage.test/read/sheets/x.jpg?token=t',
  created_at: '2026-11-20T10:00:00Z',
  created_by: 'u1',
}
const confirmed = {
  ...read,
  status: 'confirmed',
  confirmed: { ...figures, party_votes: [{ party: 'APC', votes: 215 }, { party: 'PDP', votes: 104 }] },
  confirmed_at: '2026-11-20T10:05:00Z',
  confirmed_by: 'u1',
  checks: [
    { id: 'sheet_party_sum', ok: false, title: 'Party scores add up to total valid votes', detail: 'Party scores add up to 319; ...', severity: 'high' },
    { id: 'sheet_over_registered', ok: true, title: 'Accredited voters do not exceed registered voters', detail: '329 accredited', severity: null },
    { id: 'sheet_vs_data', ok: null, title: 'Sheet matches the uploaded results', detail: 'Not checked', severity: null },
  ],
  comparison: {
    matched: true,
    pu_code: '31/04/01/003',
    raw_row_id: 'r1',
    source_row_number: 4,
    rows: [{ field: 'party:APC', label: 'APC votes', sheet: 215, data: 200, match: false }],
  },
  finding_ids: ['f1'],
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
      if (url.includes('/sheets?project_id=')) return json({ items: [read, confirmed] })
      if (url.endsWith('/sheets') && init?.method === 'POST') return json(read, 201)
      if (url.endsWith('/sheets/s1/confirm')) return json(confirmed)
      if (url.endsWith('/sheets/s1')) return json(read)
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

describe('result sheets', () => {
  it('lists sheets with their outcome and uploads a photo', async () => {
    stub()
    setSupabaseForTests(client('researcher') as never)
    window.history.pushState({}, '', '/projects/p1/sheets')
    render(<App />)
    expect(await screen.findByRole('heading', { name: /Result sheets/ })).toBeTruthy()
    expect(await screen.findByText('Waiting for you to confirm')).toBeTruthy()
    expect(screen.getByText('1 problem found')).toBeTruthy()
    expect(screen.getByText('1 to confirm')).toBeTruthy()
    const input = (await screen.findByLabelText(/Take a photo or choose one/)) as HTMLInputElement
    const file = new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3])], 'sheet.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/sheets ') && c.includes('"dataset_version_id":"v1"') && c.includes('"image_base64":"/9j/4AECAw=="'))).toBe(true))
    expect(await screen.findByRole('form', { name: "Confirm the sheet's figures" })).toBeTruthy()
  })

  it('shows the photo beside the AI reading, marks unsure figures, and confirms corrections', async () => {
    stub()
    setSupabaseForTests(client('analyst') as never)
    window.history.pushState({}, '', '/projects/p1/sheets/s1')
    render(<App />)
    const form = await screen.findByRole('form', { name: "Confirm the sheet's figures" })
    expect((screen.getByRole('img', { name: /Photo of the polling-unit result sheet/ }) as HTMLImageElement).src).toContain('token=t')
    expect(screen.getByText('Note from the reader: Slight glare.')).toBeTruthy()
    expect(within(form).getAllByText('Check this one')).toHaveLength(2) // valid votes (low) and PDP (medium)
    expect(within(form).getByText(/Party votes add up to 304\. Total valid votes: 319/)).toBeTruthy()
    fireEvent.change(within(form).getByLabelText('APC votes'), { target: { value: '215' } })
    expect(within(form).getByText(/Party votes add up to 319\. Total valid votes: 319/)).toBeTruthy()
    fireEvent.click(within(form).getByRole('button', { name: 'Confirm and check' }))
    await waitFor(() => expect(calls.some((c) => c.startsWith('POST https://api.test/sheets/s1/confirm') && c.includes('{"party":"APC","votes":215}'))).toBe(true))
    expect(await screen.findByRole('list', { name: 'Checks on this sheet' })).toBeTruthy()
    expect(screen.getByText('✗ Problem')).toBeTruthy()
    expect(screen.getByText('– Not checked')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'finding 1' }).getAttribute('href')).toBe('/findings/f1')
    expect(screen.getByText('APC votes')).toBeTruthy()
  })

  it('viewers can see sheets but not upload or confirm', async () => {
    stub()
    setSupabaseForTests(client('viewer') as never)
    window.history.pushState({}, '', '/projects/p1/sheets/s1')
    render(<App />)
    expect(await screen.findByText(/Waiting for a team member to confirm/)).toBeTruthy()
    expect(screen.queryByRole('form', { name: "Confirm the sheet's figures" })).toBeNull()
  })
})
