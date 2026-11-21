import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { friendlyError, getSupabase, ROLES, type Role } from '../lib/supabase'

type Member = { user_id: string; role: Role; email: string | null; display_name: string | null }

export default function Members() {
  const { orgId = '' } = useParams()
  const { session } = useAuth()
  const [orgName, setOrgName] = useState('')
  const [members, setMembers] = useState<Member[] | null>(null)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const [org, ms] = await Promise.all([
      sb.from('organisations').select('name').eq('id', orgId).maybeSingle(),
      sb.from('memberships').select('user_id, role').eq('org_id', orgId).order('created_at'),
    ])
    if (org.error || ms.error) {
      setError(friendlyError(org.error ?? ms.error))
      setMembers([])
      return
    }
    setOrgName(org.data?.name ?? '')
    const ids = (ms.data ?? []).map((m) => m.user_id)
    const profiles = ids.length
      ? await sb.from('profiles').select('id, email, display_name').in('id', ids)
      : { data: [], error: null }
    const byId = new Map((profiles.data ?? []).map((p) => [p.id, p]))
    setMembers(
      (ms.data ?? []).map((m) => ({
        user_id: m.user_id,
        role: m.role as Role,
        email: byId.get(m.user_id)?.email ?? null,
        display_name: byId.get(m.user_id)?.display_name ?? null,
      })),
    )
  }, [orgId])

  useEffect(() => {
    // load() only sets state after awaiting the network, never synchronously
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  const myRole = members?.find((m) => m.user_id === session?.user.id)?.role
  const isAdmin = myRole === 'admin'

  async function run(fn: () => PromiseLike<{ error: { message?: string; code?: string } | null }>) {
    setBusy(true)
    setError(null)
    const { error } = await fn()
    if (error) setError(friendlyError(error))
    await load()
    setBusy(false)
  }

  function add(e: FormEvent) {
    e.preventDefault()
    const sb = getSupabase()!
    void run(() => sb.rpc('add_member', { p_org: orgId, p_email: email.trim(), p_role: role })).then(() => setEmail(''))
  }

  if (members !== null && !orgName && !error) {
    return (
      <section className="page">
        <p className="empty">We couldn’t find this organisation, or you’re not a member.</p>
        <Link to="/">Back to organisations</Link>
      </section>
    )
  }

  return (
    <section className="page" aria-labelledby="members-heading">
      <p>
        <Link to="/">← Organisations</Link>
      </p>
      <h1 id="members-heading">{orgName ? `${orgName}: members` : 'Members'}</h1>
      {members === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Role</th>
              {isAdmin && <th scope="col">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => {
              const sb = getSupabase()!
              return (
                <tr key={m.user_id}>
                  <td>
                    {m.display_name ?? m.email ?? m.user_id}
                    {m.display_name && m.email && <div className="muted small">{m.email}</div>}
                  </td>
                  <td>
                    {isAdmin ? (
                      <select
                        aria-label={`Role for ${m.email ?? m.user_id}`}
                        value={m.role}
                        disabled={busy}
                        onChange={(e) =>
                          void run(() =>
                            sb.rpc('change_member_role', { p_org: orgId, p_user: m.user_id, p_role: e.target.value }),
                          )
                        }
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className={`role role-${m.role}`}>{m.role}</span>
                    )}
                  </td>
                  {isAdmin && (
                    <td>
                      <button
                        type="button"
                        className="danger"
                        disabled={busy}
                        onClick={() => void run(() => sb.rpc('remove_member', { p_org: orgId, p_user: m.user_id }))}
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {isAdmin && (
        <form onSubmit={add} className="form inline card" aria-label="Add member">
          <label>
            Their email (they need an account first)
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Role
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="primary" disabled={busy}>
            Add member
          </button>
        </form>
      )}
      {!isAdmin && members !== null && <p className="muted small">Only admins can add or remove members.</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  )
}
