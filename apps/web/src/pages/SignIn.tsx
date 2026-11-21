import { useState, type FormEvent } from 'react'
import AuthLayout from '../components/AuthLayout'
import { friendlyError, getSupabase } from '../lib/supabase'

export default function SignIn() {
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

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

  async function submit(e: FormEvent) {
    e.preventDefault()
    const sb = getSupabase()
    if (!sb) return
    setBusy(true)
    setError(null)
    setNotice(null)
    if (mode === 'signin') {
      const { error } = await sb.auth.signInWithPassword({ email, password })
      if (error) setError(friendlyError(error))
    } else {
      const { data, error } = await sb.auth.signUp({
        email,
        password,
        options: { data: { display_name: displayName.trim() || undefined } },
      })
      if (error) setError(friendlyError(error))
      else if (!data.session) setNotice('Account created. Check your email to confirm, then sign in.')
    }
    setBusy(false)
  }

  return (
    <AuthLayout labelledBy="auth-heading">
        <h1 id="auth-heading">{mode === 'signin' ? 'Sign in' : 'Create account'}</h1>
        <button type="button" className="primary wide" onClick={() => void google()} disabled={busy}>
          Continue with Google
        </button>
        <p className="divider">
          <span>or</span>
        </p>
        <form onSubmit={submit} className="form">
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
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {notice && <p className="notice">{notice}</p>}
          <button type="submit" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>
        <button type="button" className="link" onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}>
          {mode === 'signin' ? 'Create account' : 'Sign in'}
        </button>
    </AuthLayout>
  )
}
