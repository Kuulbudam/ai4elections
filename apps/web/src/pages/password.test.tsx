import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

type Result = { data: unknown; error: null }
function query(result: Result) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'update', 'maybeSingle', 'is', 'limit', 'not', 'range']) q[m] = () => q
  q.then = (resolve: (r: Result) => unknown) => Promise.resolve(result).then(resolve)
  return q
}
type Listener = (event: string, s: unknown) => void
function fakeClient(opts: { session?: unknown } = {}) {
  const listeners: Listener[] = []
  return {
    listeners,
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session: opts.session ?? null } }),
      onAuthStateChange: vi.fn((cb: Listener) => {
        listeners.push(cb)
        return { data: { subscription: { unsubscribe: vi.fn() } } }
      }),
      signInWithPassword: vi.fn().mockResolvedValue({ error: null }),
      signInWithOAuth: vi.fn().mockResolvedValue({ error: null }),
      signUp: vi.fn().mockResolvedValue({ data: { session: null }, error: null }),
      resend: vi.fn().mockResolvedValue({ error: null }),
      resetPasswordForEmail: vi.fn().mockResolvedValue({ error: null }),
      updateUser: vi.fn().mockResolvedValue({ data: {}, error: null }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn(() => query({ data: [], error: null })),
    rpc: vi.fn().mockResolvedValue({ data: null, error: null }),
  }
}
const session = { user: { id: 'u1', email: 'ada@example.test' }, access_token: 'tok' }

beforeEach(() => {
  window.history.pushState({}, '', '/')
  sessionStorage.clear()
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
})

describe('forgot password', () => {
  it('sends a reset link to /reset-password and never says whether the account exists', async () => {
    const sb = fakeClient()
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Forgot password' }))
    expect(screen.queryByLabelText('Password')).toBeNull()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: ' ada@example.test ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect((await screen.findByRole('status')).textContent).toMatch(/If ada@example.test has an account, we sent it a link/)
    expect(sb.auth.resetPasswordForEmail).toHaveBeenCalledWith('ada@example.test', { redirectTo: `${window.location.origin}/reset-password` })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy()
  })

  it('says to wait when too many emails were sent', async () => {
    const sb = fakeClient()
    sb.auth.resetPasswordForEmail.mockResolvedValue({ error: { message: 'For security purposes, you can only request this after 52 seconds.' } })
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Forgot password' }))
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Wait a few minutes/)
  })
})

describe('email confirmation', () => {
  it('after creating an account, asks the person to confirm by email and can send the link again', async () => {
    const sb = fakeClient()
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Create an account' }))
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough-pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect((await screen.findByRole('status')).textContent).toMatch(/Check your email\. We sent a link to ada@example.test/)
    expect(sb.auth.signUp.mock.calls[0][0].options.emailRedirectTo).toBe(window.location.origin)
    fireEvent.click(screen.getByRole('button', { name: 'Send the confirmation email again' }))
    await waitFor(() => expect(sb.auth.resend).toHaveBeenCalledWith({ type: 'signup', email: 'ada@example.test', options: { emailRedirectTo: window.location.origin } }))
    expect((await screen.findByRole('status')).textContent).toMatch(/new confirmation link/)
  })

  it('signing in before confirming explains why and offers the link again', async () => {
    const sb = fakeClient()
    sb.auth.signInWithPassword.mockResolvedValue({ error: { message: 'Email not confirmed' } })
    setSupabaseForTests(sb as never)
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'ada@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough-pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Confirm your email first/)
    expect(screen.getByRole('button', { name: 'Send the confirmation email again' })).toBeTruthy()
  })
})

describe('set a new password', () => {
  it('a reset link opens the new-password page; mismatched passwords are caught; the new one is saved', async () => {
    const sb = fakeClient({ session })
    setSupabaseForTests(sb as never)
    render(<App />)
    await waitFor(() => expect(sb.listeners.length).toBeGreaterThan(0))
    sb.listeners[0]('PASSWORD_RECOVERY', session)
    window.history.pushState({}, '', '/')
    cleanup()
    render(<App />) // after two-step verification the app opens again with the recovery flag set
    expect(await screen.findByRole('heading', { name: 'Set a new password' })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'brand-new-pass' } })
    fireEvent.change(screen.getByLabelText('Type it again'), { target: { value: 'brand-new-pas' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Set a new password' }))
    expect((await screen.findByRole('alert')).textContent).toBe('The two passwords are not the same.')
    fireEvent.change(screen.getByLabelText('Type it again'), { target: { value: 'brand-new-pass' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Set a new password' }))
    expect((await screen.findByRole('status')).textContent).toMatch(/Your password has been changed/)
    expect(sb.auth.updateUser).toHaveBeenCalledWith({ password: 'brand-new-pass' })
  })

  it('Profile links to Change password', async () => {
    setSupabaseForTests(fakeClient({ session }) as never)
    window.history.pushState({}, '', '/profile')
    render(<App />)
    expect((await screen.findByRole('link', { name: 'Change password' })).getAttribute('href')).toBe('/reset-password')
  })
})
