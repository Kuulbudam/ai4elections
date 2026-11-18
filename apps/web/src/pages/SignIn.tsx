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
        <h1 id="auth-heading">{mode === 'signin' ? 'Sign in' : 'Create an account'}</h1>
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
          <button type="submit" className="primary" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>
        <button type="button" className="link" onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}>
          {mode === 'signin' ? 'New here? Create an account' : 'Already have an account? Sign in'}
        </button>
    </AuthLayout>
  )
}
