import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App, { ApiStatus } from './App'
import { friendlyError, setSupabaseForTests } from './lib/supabase'

type Result = { data: unknown; error: { message: string; code?: string } | null }

function query(result: Result) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'update', 'maybeSingle', 'is', 'limit', 'not']) q[m] = () => q
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}

function fakeClient(opts: { session?: unknown; tables?: Record<string, Result>; rpc?: Result } = {}) {
  const tables = opts.tables ?? {}
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session: opts.session ?? null } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signInWithPassword: vi.fn().mockResolvedValue({ error: null }),
      signInWithOAuth: vi.fn().mockResolvedValue({ error: null }),
      signUp: vi.fn().mockResolvedValue({ data: { session: null }, error: null }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query(tables[t] ?? { data: [], error: null })),
    rpc: vi.fn().mockResolvedValue(opts.rpc ?? { data: null, error: null }),
  }
}

const session = { user: { id: 'u-admin', email: 'admin@example.test' } }

beforeEach(() => window.history.pushState({}, '', '/'))
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('configuration and sign-in', () => {
  it('says plainly when Supabase is not configured', () => {
    setSupabaseForTests(null)
    render(<App />)
    expect(screen.getByRole('alert').textContent).toMatch(/not set up yet/i)
  })

  it('signs in with email and password', async () => {
    const sb = fakeClient()
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'a@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct horse' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() =>
      expect(sb.auth.signInWithPassword).toHaveBeenCalledWith({ email: 'a@example.test', password: 'correct horse' }),
    )
  })

  it('offers Google first and keeps email as the fallback', async () => {
    const sb = fakeClient()
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Continue with Google' }))
    await waitFor(() => expect(sb.auth.signInWithOAuth).toHaveBeenCalled())
    expect(sb.auth.signInWithOAuth.mock.calls[0][0].provider).toBe('google')
    expect(screen.getByLabelText('Email')).toBeTruthy()
  })

  it('points to email sign-in when Google is unavailable', async () => {
    const sb = fakeClient()
    sb.auth.signInWithOAuth.mockResolvedValue({ error: { message: 'Unsupported provider: provider is not enabled' } })
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Continue with Google' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Use your email and password below/)
  })

  it('shows a friendly message for wrong credentials', async () => {
    const sb = fakeClient()
    sb.auth.signInWithPassword.mockResolvedValue({ error: { message: 'Invalid login credentials' } })
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'a@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong-pass' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toBe('Email or password is incorrect.')
  })

  it('creates an account with a display name', async () => {
    const sb = fakeClient()
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Create account' }))
    fireEvent.change(screen.getByLabelText('Name (optional)'), { target: { value: 'Ada' } })
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough-pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))
    await waitFor(() => expect(sb.auth.signUp).toHaveBeenCalled())
    expect(sb.auth.signUp.mock.calls[0][0].options.data.display_name).toBe('Ada')
  })
})

describe('organisations', () => {
  it('shows a helpful empty state and creates an organisation', async () => {
    const sb = fakeClient({ session })
    setSupabaseForTests(sb as never)
    render(<App />)
    expect(await screen.findByText(/not in an organisation yet/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('New organisation name'), { target: { value: 'Synthetic Monitors' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create organisation' }))
    await waitFor(() => expect(sb.rpc).toHaveBeenCalledWith('create_organisation', { p_name: 'Synthetic Monitors' }))
  })

  it('lists organisations with the user role', async () => {
    const sb = fakeClient({
      session,
      tables: { memberships: { data: [{ org_id: 'o1', role: 'analyst', organisations: { name: 'Org One' } }], error: null } },
    })
    setSupabaseForTests(sb as never)
    render(<App />)
    expect(await screen.findByText('Org One')).toBeTruthy()
    expect(screen.getByText('analyst')).toBeTruthy()
  })
})

describe('members', () => {
  function membersClient(myRole: string, rpc?: Result) {
    return fakeClient({
      session,
      rpc,
      tables: {
        organisations: { data: { name: 'Org One' }, error: null },
        memberships: {
          data: [
            { user_id: 'u-admin', role: myRole },
            { user_id: 'u-2', role: 'viewer' },
          ],
          error: null,
        },
        profiles: {
          data: [
            { id: 'u-admin', email: 'admin@example.test', display_name: null },
            { id: 'u-2', email: 'v@example.test', display_name: 'Vee' },
          ],
          error: null,
        },
      },
    })
  }

  it('hides member management from non-admins', async () => {
    setSupabaseForTests(membersClient('researcher') as never)
    window.history.pushState({}, '', '/orgs/o1/members')
    render(<App />)
    expect(await screen.findByText('Org One: members')).toBeTruthy()
    expect(screen.queryByRole('form', { name: 'Add member' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull()
    expect(screen.getByText(/Only admins can add or remove members/)).toBeTruthy()
  })

  it('lets admins add members and shows server errors clearly', async () => {
    const sb = membersClient('admin', {
      data: null,
      error: { message: 'not_found: no registered user with that email; ask them to sign up first' },
    })
    setSupabaseForTests(sb as never)
    window.history.pushState({}, '', '/orgs/o1/members')
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Their email (they need an account first)'), { target: { value: 'new@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add member' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/ask them to sign up first/)
    expect(sb.rpc).toHaveBeenCalledWith('add_member', { p_org: 'o1', p_email: 'new@example.test', p_role: 'viewer' })
    expect(screen.getAllByRole('button', { name: 'Remove' })).toHaveLength(2)
  })
})

describe('API status and wording', () => {
  it('reports when the API is not configured', () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    render(<ApiStatus />)
    expect(screen.getByTestId('api-status').textContent).toMatch(/not set/i)
  })

  it('shows the real health response', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.test/')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', version: '0.1.0' }) })
    vi.stubGlobal('fetch', fetchMock)
    render(<ApiStatus />)
    expect(await screen.findByText(/Connected · v0\.1\.0/)).toBeTruthy()
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.test/health')
  })

  it('reports an unreachable API', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')))
    render(<ApiStatus />)
    expect(await screen.findByText(/Can’t reach the server/i)).toBeTruthy()
  })

  it('never uses verdict language and never leaks raw errors', async () => {
    setSupabaseForTests(fakeClient({ session }) as never)
    const { container } = render(<App />)
    await screen.findByText(/not in an organisation yet/)
    expect(container.textContent?.toLowerCase()).not.toMatch(/fraud|rigged/)
    expect(friendlyError({ message: 'duplicate key value violates unique constraint "x"' })).toBe(
      'Something went wrong. Please try again.',
    )
  })
})
