import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { friendlyError, getSupabase, type Role } from '../lib/supabase'

type OrgRow = { org_id: string; role: Role; organisations: { name: string } | null }

export default function Organisations() {
  const { session } = useAuth()
  const [orgs, setOrgs] = useState<OrgRow[] | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb || !session) return
    const { data, error } = await sb
      .from('memberships')
      .select('org_id, role, organisations(name)')
      .eq('user_id', session.user.id)
      .order('created_at')
    if (error) setError(friendlyError(error))
    setOrgs((data as unknown as OrgRow[]) ?? [])
  }, [session])

  useEffect(() => {
    // load() only sets state after awaiting the network, never synchronously
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function create(e: FormEvent) {
    e.preventDefault()
    const sb = getSupabase()
    if (!sb) return
    setBusy(true)
    setError(null)
    const { error } = await sb.rpc('create_organisation', { p_name: name.trim() })
    if (error) setError(friendlyError(error))
    else {
      setName('')
      await load()
    }
    setBusy(false)
  }

  return (
    <section className="page" aria-labelledby="orgs-heading">
      <h2 id="orgs-heading">Organisations</h2>
      {orgs === null ? (
        <p className="muted">Loading…</p>
      ) : orgs.length === 0 ? (
        <p className="empty">You are not in an organisation yet. Create one below, or ask an admin to add you.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Organisation</th>
              <th scope="col">Your role</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {orgs.map((o) => (
              <tr key={o.org_id}>
                <td>{o.organisations?.name ?? '—'}</td>
                <td>
                  <span className={`role role-${o.role}`}>{o.role}</span>
                </td>
                <td>
                  <Link to={`/orgs/${o.org_id}/members`}>Members</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form onSubmit={create} className="form inline card" aria-label="Create organisation">
        <label>
          New organisation name
          <input required minLength={2} maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Creating…' : 'Create organisation'}
        </button>
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  )
}
