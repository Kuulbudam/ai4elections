import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { friendlyError, getSupabase } from '../lib/supabase'

export default function Profile() {
  const { session } = useAuth()
  const [name, setName] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const sb = getSupabase()
    if (!sb || !session) return
    sb.from('profiles')
      .select('display_name')
      .eq('id', session.user.id)
      .maybeSingle()
      .then(({ data, error }) => {
        if (error) setError(friendlyError(error))
        setName(data?.display_name ?? '')
        setLoaded(true)
      })
  }, [session])

  async function save(e: FormEvent) {
    e.preventDefault()
    const sb = getSupabase()
    if (!sb || !session) return
    setStatus(null)
    setError(null)
    const { error } = await sb
      .from('profiles')
      .update({ display_name: name.trim() || null })
      .eq('id', session.user.id)
    if (error) setError(friendlyError(error))
    else setStatus('Saved.')
  }

  return (
    <section className="page" aria-labelledby="profile-heading">
      <h1 id="profile-heading">Your profile</h1>
      <dl className="facts">
        <dt>Email</dt>
        <dd>{session?.user.email}</dd>
      </dl>
      <form onSubmit={save} className="form card" aria-label="Profile">
        <label>
          Display name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} disabled={!loaded} />
        </label>
        <button type="submit" className="primary" disabled={!loaded}>
          Save
        </button>
        {status && <p className="notice">{status}</p>}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </form>
      <p>
        <Link to="/reset-password">Change password</Link>
      </p>
    </section>
  )
}
