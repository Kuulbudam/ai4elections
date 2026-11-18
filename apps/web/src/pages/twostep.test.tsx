import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }

function client(level: { currentLevel: string; nextLevel: string }, verifyError: unknown = null) {
  const aal = vi.fn().mockResolvedValue({ data: level })
  const mfa = {
    getAuthenticatorAssuranceLevel: aal,
    listFactors: vi.fn().mockResolvedValue({
      data: {
        totp: level.nextLevel === 'aal2' ? [{ id: 'f1', status: 'verified' }] : [],
        all: [{ id: 'old', factor_type: 'totp', status: 'unverified' }],
      },
    }),
    unenroll: vi.fn().mockResolvedValue({ error: null }),
    enroll: vi.fn().mockResolvedValue({ data: { id: 'f2', totp: { qr_code: 'data:image/svg+xml;utf8,<svg/>', secret: 'ABCDEF234567' } }, error: null }), // gitleaks:allow (fake test key)
    challengeAndVerify: vi.fn().mockResolvedValue({ error: verifyError }),
  }
  return {
    mfa,
    aal,
    sb: {
      auth: {
        getSession: vi.fn().mockResolvedValue({ data: { session } }),
        onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
        signOut: vi.fn(),
        mfa,
      },
      from: vi.fn(),
      rpc: vi.fn(),
    },
  }
}

afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
})

describe('two-step verification', () => {
  it('asks for the authenticator code when one is set up, and verifies it', async () => {
    const c = client({ currentLevel: 'aal1', nextLevel: 'aal2' })
    setSupabaseForTests(c.sb as never)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Two-step verification' })).toBeTruthy()
    expect(screen.queryByText('Organisations')).toBeNull() // nothing else is reachable yet
    fireEvent.change(screen.getByLabelText('6-digit code'), { target: { value: '123 456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    await waitFor(() => expect(c.mfa.challengeAndVerify).toHaveBeenCalledWith({ factorId: 'f1', code: '123456' }))
  })

  it('requires set-up with a QR code when no authenticator exists, clearing abandoned attempts', async () => {
    const c = client({ currentLevel: 'aal1', nextLevel: 'aal1' })
    setSupabaseForTests(c.sb as never)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Set up two-step verification' })).toBeTruthy()
    expect(await screen.findByAltText('QR code for your authenticator app')).toBeTruthy()
    expect(c.mfa.unenroll).toHaveBeenCalledWith({ factorId: 'old' })
    expect(screen.getByText('ABCDEF234567')).toBeTruthy()
  })

  it('shows a plain message for a wrong code', async () => {
    const c = client({ currentLevel: 'aal1', nextLevel: 'aal2' }, { message: 'Invalid TOTP code entered' })
    setSupabaseForTests(c.sb as never)
    render(<App />)
    fireEvent.change(await screen.findByLabelText('6-digit code'), { target: { value: '000000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/not correct or has expired/)
  })
})
