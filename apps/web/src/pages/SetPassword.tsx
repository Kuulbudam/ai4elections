import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { friendlyError, getSupabase } from '../lib/supabase'

/** New password: reached from a "reset your password" email (after two-step verification) or from Profile. */
export default function SetPassword() {
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < 8) return setError('Use at least 8 characters.')
    if (password !== again) return setError('The two passwords are not the same.')
    const sb = getSupabase()
    if (!sb) return
    setBusy(true)
    const { error } = await sb.auth.updateUser({ password })
    setBusy(false)
    if (error) return setError(friendlyError(error))
    setDone(true)
    setPassword('')
    setAgain('')
  }

  return (
    <section className="page narrow" aria-labelledby="pw-heading">
      <h1 id="pw-heading">Set a new password</h1>
      {done ? (
        <>
          <p className="notice" role="status">
            Your password has been changed. Use it next time you sign in with your email.
          </p>
          <p>
            <Link to="/">Go to the dashboard</Link>
          </p>
        </>
      ) : (
        <form className="form card" onSubmit={submit} aria-label="Set a new password">
          <label>
            New password
            <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </label>
          <label>
            Type it again
            <input type="password" required minLength={8} value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" />
          </label>
          <p className="small muted">At least 8 characters. Your two-step code is still needed when you sign in.</p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button type="submit" className="primary" disabled={busy}>
            {busy ? 'Saving…' : 'Save password'}
          </button>
        </form>
      )}
    </section>
  )
}
