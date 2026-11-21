import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'
import { suggestMapping } from './Mapping'

type Result = { data: unknown; error: null }

function query(result: Result) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'update', 'insert', 'maybeSingle']) q[m] = () => q
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}

const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }

function client(tables: Record<string, unknown>) {
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query({ data: tables[t] ?? [], error: null })),
    rpc: vi.fn(),
  }
}

beforeEach(() => vi.stubEnv('VITE_API_BASE_URL', 'https://api.test'))
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('column mapping suggestions', () => {
  it('maps common header names and detects party vote columns', () => {
    const s = suggestMapping([
      'PU Code', 'State', 'LGA', 'Ward', 'Registered Voters', 'Accredited Voters', 'Valid Votes',
      'Rejected Votes', 'Total Votes Cast', 'Votes PA', 'Votes PB', 'Latitude', 'Longitude', 'Notes',
    ])
    expect(s.fields).toMatchObject({ pu_code: 'PU Code', lga: 'LGA', total_cast: 'Total Votes Cast', latitude: 'Latitude' })
    expect(s.parties.filter((p) => p.include).map((p) => p.party_code)).toEqual(['PA', 'PB'])
    expect(s.parties.find((p) => p.source_column === 'Notes')?.include).toBe(false)
  })

  it('maps the converted CCIJ/VEZA real-data file (no rejected or total-cast column)', () => {
    const s = suggestMapping([
      'PU Code', 'State', 'LGA', 'Ward', 'Polling Unit', 'Registered Voters', 'Accredited Voters', 'Valid Votes',
      'Votes APC', 'Votes PDP', 'Votes LP', 'Votes NNPP', 'Latitude', 'Longitude', 'Source Status', 'Source Document',
    ])
    expect(s.fields.valid_votes).toBe('Valid Votes')
    expect(s.fields.total_cast).toBeUndefined()
    expect(s.parties.filter((p) => p.include).map((p) => p.party_code)).toEqual(['APC', 'PDP', 'LP', 'NNPP'])
  })
})

describe('projects', () => {
  it('only admins see the create-project form', async () => {
    setSupabaseForTests(
      client({ memberships: { role: 'analyst' }, organisations: { name: 'Org' }, projects: [] }) as never,
    )
    window.history.pushState({}, '', '/orgs/o1/projects')
    render(<App />)
    expect(await screen.findByText(/No projects yet/)).toBeTruthy()
    expect(screen.queryByRole('form', { name: 'Create project' })).toBeNull()
  })
})

describe('upload flow', () => {
  function setup() {
    setSupabaseForTests(
      client({
        projects: { org_id: 'o1', election_name: 'SYNTHETIC Election' },
        datasets: [],
        memberships: { role: 'analyst' },
        dataset_versions: { row_count: 3 },
      }) as never,
    )
    window.history.pushState({}, '', '/projects/p1/upload')
  }

  class FakeXHR {
    static last: FakeXHR
    status = 200
    upload = { onprogress: null as null | ((e: unknown) => void) }
    onload: null | (() => void) = null
    onerror: null | (() => void) = null
    url = ''
    open(_m: string, url: string) {
      this.url = url
      FakeXHR.last = this
    }
    setRequestHeader() {}
    send() {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 1 })
      setTimeout(() => this.onload?.(), 0)
    }
  }

  it('registers, uploads, finalizes and waits for ingestion', async () => {
    setup()
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    vi.stubGlobal('crypto', { randomUUID: () => 'key-1' })
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push(`${init.method} ${url}`)
        const json = (b: unknown) => ({ ok: true, json: async () => b })
        if (url.endsWith('/uploads'))
          return json({ dataset_version_id: 'v1', upload_url: 'https://storage.test/up', dataset_id: 'd1', version_number: 1 })
        if (url.endsWith('/finalize'))
          return json({ sha256: 'a'.repeat(64), duplicate_of_version_id: null, ingestion_job: { id: 'j1', status: 'succeeded' } })
        return json({ id: 'j1', status: 'succeeded' })
      }),
    )
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Results' } })
    fireEvent.change(screen.getByLabelText('Source details'), { target: { value: 'SYNTHETIC' } })
    const file = new File(['a,b\n1,2\n'], 'SYNTHETIC.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByLabelText('CSV file (max 25 MB)'), { target: { files: [file] } })
    fireEvent.submit(screen.getByRole('form', { name: 'Upload results' }))
    expect(await screen.findByRole('link', { name: 'Match columns' })).toBeTruthy()
    expect(calls.filter((c) => !c.endsWith('/health'))).toEqual(['POST https://api.test/uploads', 'POST https://api.test/dataset-versions/v1/finalize'])
    expect(FakeXHR.last.url).toBe('https://storage.test/up')
    expect(screen.getByText('a'.repeat(64))).toBeTruthy()
  })

  it('explains ingestion failures in plain language', async () => {
    setup()
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    vi.stubGlobal('crypto', { randomUUID: () => 'key-2' })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () =>
          url.endsWith('/uploads')
            ? { dataset_version_id: 'v1', upload_url: 'https://storage.test/up' }
            : { sha256: 'b'.repeat(64), ingestion_job: { id: 'j', status: 'failed', error_code: 'HEADER_NOT_FOUND' } },
      })),
    )
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'R' } })
    fireEvent.change(screen.getByLabelText('Source details'), { target: { value: 'x' } })
    fireEvent.change(screen.getByLabelText('CSV file (max 25 MB)'), { target: { files: [new File(['1,2'], 'x.csv')] } })
    fireEvent.submit(screen.getByRole('form', { name: 'Upload results' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/The first row must hold the column names/)
  })

  it('rejects non-CSV and oversized files before uploading', async () => {
    setup()
    render(<App />)
    const input = await screen.findByLabelText('CSV file (max 25 MB)')
    fireEvent.change(input, { target: { files: [new File(['x'], 'results.xlsx')] } })
    expect((await screen.findByRole('alert')).textContent).toBe('Choose a .csv file.')
    const big = new File(['x'], 'big.csv')
    Object.defineProperty(big, 'size', { value: 26 * 1024 * 1024 })
    fireEvent.change(input, { target: { files: [big] } })
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('The file is larger than 25 MB.'))
    expect((screen.getByRole('button', { name: 'Upload' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
