import { useState, type FormEvent } from 'react'
import AuthLayout from '../components/AuthLayout'
import { friendlyError, getSupabase } from '../lib/supabase'

type Mode = 'signin' | 'signup' | 'forgot'
const TITLE: Record<Mode, string> = { signin: 'Sign in', signup: 'Create account', forgot: 'Reset password' }

export default function SignIn() {
  const [mode, setMode] = useState<Mode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // The email still waiting to be confirmed, so the person can ask for the link again.
  const [unconfirmed, setUnconfirmed] = useState<string | null>(null)

  function go(m: Mode) {
    setMode(m)
    setError(null)
    setNotice(null)
    setUnconfirmed(null)
  }

  async function google() {
    const sb = getSupabase()
    if (!sb) return
    setBusy(true)
    setError(null)
    const { error } = await sb.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin } })
    if (error) {
      setError('Google sign-in is not available right now. Use your email and password below.')
      setBusy(false)
    }
  }

  async function resend() {
    const sb = getSupabase()
    if (!sb || !unconfirmed) return
    setBusy(true)
    setError(null)
    const { error } = await sb.auth.resend({ type: 'signup', email: unconfirmed, options: { emailRedirectTo: window.location.origin } })
    if (error) setError(friendlyError(error))
    else setNotice(`We sent a new confirmation link to ${unconfirmed}.`)
    setBusy(false)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const sb = getSupabase()
    if (!sb) return
    setBusy(true)
    setError(null)
    setNotice(null)
    setUnconfirmed(null)
    const address = email.trim()
    if (mode === 'signin') {
      const { error } = await sb.auth.signInWithPassword({ email: address, password })
      if (error) {
        setError(friendlyError(error))
        if ((error.message ?? '').toLowerCase().includes('email not confirmed')) setUnconfirmed(address)
      }
    } else if (mode === 'signup') {
      const { data, error } = await sb.auth.signUp({
        email: address,
        password,
        options: { data: { display_name: displayName.trim() || undefined }, emailRedirectTo: window.location.origin },
      })
      if (error) setError(friendlyError(error))
      else if (!data.session) {
        setNotice(`Check your email. We sent a link to ${address}: open it to confirm your account, then sign in.`)
        setUnconfirmed(address)
        setPassword('')
      }
    } else {
      const { error } = await sb.auth.resetPasswordForEmail(address, { redirectTo: `${window.location.origin}/reset-password` })
      // The same message either way, so the page never reveals whether an email address has an account.
      if (error && /rate limit|security purposes/i.test(error.message ?? '')) setError(friendlyError(error))
      else setNotice(`If ${address} has an account, we sent it a link to set a new password. It works once and expires in an hour.`)
    }
    setBusy(false)
  }

  return (
    <AuthLayout labelledBy="auth-heading">
      <h1 id="auth-heading">{TITLE[mode]}</h1>
      {mode !== 'forgot' && (
        <>
          <button type="button" className="primary wide" onClick={() => void google()} disabled={busy}>
            Continue with Google
          </button>
          <p className="divider">
            <span>or</span>
          </p>
        </>
      )}
      {mode === 'forgot' && <p className="muted">Enter the email you sign in with. We’ll send you a link to set a new password.</p>}
      <form onSubmit={submit} className="form" aria-label={TITLE[mode]}>
        {mode === 'signup' && (
          <label>
            Name (optional)
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={120} autoComplete="name" />
          </label>
        )}
        <label>
          Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </label>
        {mode !== 'forgot' && (
          <label>
            Password
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            />
          </label>
        )}
        {mode === 'signin' && (
          <button type="button" className="link forgot" onClick={() => go('forgot')}>
            Forgot password?
          </button>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {notice && (
          <p className="notice" role="status">
            {notice}
          </p>
        )}
        {unconfirmed && (
          <button type="button" className="link" onClick={() => void resend()} disabled={busy}>
            Send the confirmation email again
          </button>
        )}
        <button type="submit" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : mode === 'signup' ? 'Create account' : 'Send reset link'}
        </button>
      </form>
      {mode === 'forgot' ? (
        <button type="button" className="link" onClick={() => go('signin')}>
          Back to sign in
        </button>
      ) : (
        <button type="button" className="link" onClick={() => go(mode === 'signin' ? 'signup' : 'signin')}>
          {mode === 'signin' ? 'Create account' : 'Sign in'}
        </button>
      )}
    </AuthLayout>
  )
}
