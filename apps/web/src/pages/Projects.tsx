import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { friendlyError, getSupabase } from '../lib/supabase'
import { LEVELS } from '../lib/format'
import { useRole } from '../lib/useRole'

type Project = {
  id: string
  election_name: string
  election_year: number
  country: string
  election_level: string
  archived_at: string | null
}


export default function Projects() {
  const { orgId = '' } = useParams()
  const { session } = useAuth()
  const role = useRole(orgId)
  const [orgName, setOrgName] = useState('')
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ election_name: '', election_year: '2027', country: '', election_level: 'presidential', description: '' })
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const [org, ps] = await Promise.all([
      sb.from('organisations').select('name').eq('id', orgId).maybeSingle(),
      sb.from('projects').select('id, election_name, election_year, country, election_level, archived_at').eq('org_id', orgId).order('created_at', { ascending: false }),
    ])
    if (ps.error) setError(friendlyError(ps.error))
    setOrgName(org.data?.name ?? '')
    setProjects((ps.data as Project[]) ?? [])
  }, [orgId])

  useEffect(() => {
    // load() only sets state after awaiting the network, never synchronously
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function create(e: FormEvent) {
    e.preventDefault()
    const sb = getSupabase()
    if (!sb || !session) return
    setBusy(true)
    setError(null)
    const { error } = await sb.from('projects').insert({
      org_id: orgId,
      election_name: form.election_name.trim(),
      election_year: Number(form.election_year),
      country: form.country.trim(),
      election_level: form.election_level,
      description: form.description.trim() || null,
      created_by: session.user.id,
    })
    if (error) setError(friendlyError(error))
    else {
      setForm({ ...form, election_name: '', description: '' })
      await load()
    }
    setBusy(false)
  }

  return (
    <section className="page" aria-labelledby="projects-heading">
      <p>
        <Link to="/">← Organisations</Link>
      </p>
      <h1 id="projects-heading">{orgName ? `${orgName}: projects` : 'Projects'}</h1>
      {projects === null ? (
        <p className="muted">Loading…</p>
      ) : projects.length === 0 ? (
        <p className="empty">No projects yet.{role === 'admin' ? ' Create the first one below.' : ' An admin can create one.'}</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Election</th>
              <th scope="col">Year</th>
              <th scope="col">Country</th>
              <th scope="col">Level</th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.id}>
                <td>
                  <Link to={`/projects/${p.id}`}>{p.election_name}</Link>
                  {p.archived_at && <span className="badge">archived</span>}
                </td>
                <td>{p.election_year}</td>
                <td>{p.country}</td>
                <td>{LEVELS.find(([v]) => v === p.election_level)?.[1] ?? p.election_level}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {role === 'admin' && (
        <form onSubmit={create} className="form card" aria-label="Create project">
          <h3>New project</h3>
          <div className="grid2">
            <label>
              Election name
              <input required minLength={2} maxLength={200} value={form.election_name} onChange={(e) => setForm({ ...form, election_name: e.target.value })} />
            </label>
            <label>
              Election year
              <input type="number" required min={1900} max={2100} value={form.election_year} onChange={(e) => setForm({ ...form, election_year: e.target.value })} />
            </label>
            <label>
              Country
              <input required minLength={2} maxLength={100} value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} />
            </label>
            <label>
              Election level
              <select value={form.election_level} onChange={(e) => setForm({ ...form, election_level: e.target.value })}>
                {LEVELS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            Description (optional)
            <textarea maxLength={4000} rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <button type="submit" className="primary" disabled={busy}>
            {busy ? 'Creating…' : 'Create project'}
          </button>
        </form>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  )
}
