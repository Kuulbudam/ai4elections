import { useState, type FormEvent } from 'react'
import AuthLayout from '../components/AuthLayout'
import { friendlyError, getSupabase } from '../lib/supabase'

type Mode = 'signin' | 'signup' | 'forgot'
const TITLE: Record<Mode, string> = {
  signin: 'Sign in',
  signup: 'Create account',
  forgot: 'Reset password',
}

export default function SignIn() {
  const [mode, setMode] = useState<Mode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
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
    const { error } = await sb.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    })
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
    const { error } = await sb.auth.resend({
      type: 'signup',
      email: unconfirmed,
      options: { emailRedirectTo: window.location.origin },
    })
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
      const { error } = await sb.auth.signInWithPassword({
        email: address,
        password,
      })
      if (error) {
        setError(friendlyError(error))
        if ((error.message ?? '').toLowerCase().includes('email not confirmed')) setUnconfirmed(address)
      }
    } else if (mode === 'signup') {
      const { data, error } = await sb.auth.signUp({
        email: address,
        password,
        options: {
          data: { display_name: displayName.trim() || undefined },
          emailRedirectTo: window.location.origin,
        },
      })
      if (error) setError(friendlyError(error))
      else if (!data.session) {
        setNotice(`Check your email. We sent a link to ${address}: open it to confirm your account, then sign in.`)
        setUnconfirmed(address)
        setPassword('')
      }
    } else {
      const { error } = await sb.auth.resetPasswordForEmail(address, {
        redirectTo: `${window.location.origin}/reset-password`,
      })
      // The same message either way, so the page never reveals whether an email address has an account.
      if (error && /rate limit|security purposes/i.test(error.message ?? '')) setError(friendlyError(error))
      else
        setNotice(
          `If ${address} has an account, we sent it a link to set a new password. It works once and expires in an hour.`,
        )
    }
    setBusy(false)
  }

  return (
    <AuthLayout labelledBy="auth-heading">
      <h1 id="auth-heading">{TITLE[mode]}</h1>
      {mode !== 'forgot' && (
        <>
          <button type="button" className="wide google" onClick={() => void google()} disabled={busy}>
            <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
              <path
                fill="#EA4335"
                d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z"
              />
              <path
                fill="#4285F4"
                d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.3 5.7c4.3-4 7-9.9 7-17.1z"
              />
              <path
                fill="#FBBC05"
                d="M10.5 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.8-6.1z"
              />
              <path
                fill="#34A853"
                d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.3-5.7c-2 1.4-4.7 2.3-8.6 2.3-6.2 0-11.5-4.2-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z"
              />
            </svg>
            Continue with Google
          </button>
          <p className="divider">
            <span>or</span>
          </p>
        </>
      )}
      {mode === 'forgot' && (
        <p className="muted">Enter the email you sign in with. We’ll send you a link to set a new password.</p>
      )}
      <form onSubmit={submit} className="form" aria-label={TITLE[mode]}>
        {mode === 'signup' && (
          <label>
            Name (optional)
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={120}
              autoComplete="name"
            />
          </label>
        )}
        <label>
          Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </label>
        {mode !== 'forgot' && (
          <div className="field">
            <div className="field-head">
              <label htmlFor="password">Password</label>
              {mode === 'signin' && (
                <button type="button" className="text-link" onClick={() => go('forgot')}>
                  Forgot password
                </button>
              )}
            </div>
            <div className="password-box">
              <input
                id="password"
                type={showPassword ? 'text' : 'password'}
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              />
              <button
                type="button"
                className="text-link reveal"
                aria-pressed={showPassword}
                onClick={() => setShowPassword((v) => !v)}
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
            {mode === 'signup' && <p className="field-hint">At least 8 characters.</p>}
          </div>
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
          <button type="button" className="text-link" onClick={() => void resend()} disabled={busy}>
            Send the confirmation email again
          </button>
        )}
        <button type="submit" className="primary wide" disabled={busy}>
          {busy
            ? 'Please wait…'
            : mode === 'signin'
              ? 'Sign in'
              : mode === 'signup'
                ? 'Create account'
                : 'Send reset link'}
        </button>
      </form>
      <p className="auth-switch">
        {mode === 'forgot' ? 'Remembered it?' : mode === 'signin' ? 'New to EaaS?' : 'Already have an account?'}{' '}
        <button type="button" className="text-link" onClick={() => go(mode === 'signin' ? 'signup' : 'signin')}>
          {mode === 'signin' ? 'Create an account' : 'Sign in'}
        </button>
      </p>
    </AuthLayout>
  )
}
