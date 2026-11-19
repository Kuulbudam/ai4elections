import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
const area = (state: string, lga: string | null, units: number, rate: string, flags: number, inRef = true) => ({
  state, lga, ward: null, in_reference_list: inRef, units,
  turnout: { units: units - 1, registered_voters: 1000, accredited_voters: Math.round(Number(rate) * 1000), rate },
  vote_share: { units: units - 2, valid_votes: 500, party_totals: { PA: 300, PB: 200 }, leading_party: 'PA', leading_share: '0.600000' },
  findings: { unusual_turnout: flags, unusual_share: 0, very_high: 1, data_quality: 2, unreviewed: flags + 2 },
})  // prettier-ignore

function client() {
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn(() => query({ data: { project_id: 'p1', original_filename: 'SYNTHETIC.csv', version_number: 1 }, error: null })),
    rpc: vi.fn(),
  }
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  window.history.pushState({}, '', '/versions/v1/areas')
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('areas page', () => {
  it('shows state totals, drills down to LGAs and compares areas', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push(url)
        const json = (b: unknown) => ({ ok: true, status: 200, json: async () => b })
        if (url.includes('level=state'))
          return json({ dataset_version_id: 'v1', analytical_run_id: 'r1', level: 'state', reference_list_used: true, items: [area('Synthia', null, 3000, '0.351000', 12), area('Norvale', null, 2900, '0.402000', 7)] })
        if (url.includes('level=lga'))
          return json({ dataset_version_id: 'v1', analytical_run_id: 'r1', level: 'lga', state: 'Synthia', reference_list_used: true, items: [area('Synthia', 'Alder', 300, '0.300000', 1), area('Synthia', 'Birchx', 20, '0.310000', 0, false)] })
        return json({ status: 'ok' })
      }),
    )
    setSupabaseForTests(client() as never)
    render(<App />)
    expect(await screen.findByRole('button', { name: 'Synthia' })).toBeTruthy()
    const table = screen.getAllByRole('table')[0]
    expect(within(table).getByText('35.1%')).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Turnout by area' })).toBeTruthy()

    fireEvent.click(screen.getByLabelText('Compare Synthia'))
    fireEvent.click(screen.getByLabelText('Compare Norvale'))
    const cmp = await screen.findByRole('table', { name: 'Area comparison' })
    expect(within(cmp).getByText('PA share')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Synthia' }))
    await waitFor(() => expect(calls.some((c) => c.includes('level=lga') && c.includes('state=Synthia'))).toBe(true))
    expect((await screen.findAllByText('Birchx')).length).toBeGreaterThan(0)
    expect(screen.getByText(/not in reference list/)).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/fraud|rigg/i)
  })
})
