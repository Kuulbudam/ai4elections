import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { friendlyError, getSupabase, type Role } from '../lib/supabase'

type OrgRow = { org_id: string; role: Role; organisations: { name: string } | null }
type ProjectRow = { id: string; org_id: string; election_name: string; election_year: number }
type WorkRow = { project: ProjectRow; versions: number; analysed: number; toReview: number; unverified: number; reports: number }

export default function Organisations() {
  const { session } = useAuth()
  const [orgs, setOrgs] = useState<OrgRow[] | null>(null)
  const [work, setWork] = useState<WorkRow[] | null>(null)
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
    // Dashboard: what needs attention in each project (all reads under RLS).
    const [p, v, f, c, r] = await Promise.all([
      sb.from('projects').select('id, org_id, election_name, election_year').is('archived_at', null).order('created_at', { ascending: false }).limit(50),
      sb.from('dataset_versions').select('project_id, latest_analysis_run_id').is('archived_at', null).limit(1000),
      sb.from('finding_overview').select('project_id, analytical_run_id').eq('category', 'statistical_anomaly').in('severity', ['very_high', 'high']).eq('review_status', 'unreviewed').limit(5000),
      sb.from('claims').select('project_id').eq('status', 'unverified_allegation').limit(5000),
      sb.from('reports').select('project_id').limit(5000),
    ])
    const latest = new Set(((v.data ?? []) as { latest_analysis_run_id: string | null }[]).map((x) => x.latest_analysis_run_id).filter(Boolean))
    const count = <T extends { project_id: string }>(rows: T[] | null, pid: string, keep: (x: T) => boolean = () => true) =>
      (rows ?? []).filter((x) => x.project_id === pid && keep(x)).length
    setWork(
      ((p.data ?? []) as ProjectRow[]).map((pr) => ({
        project: pr,
        versions: count(v.data as { project_id: string }[], pr.id),
        analysed: count(v.data as { project_id: string; latest_analysis_run_id: string | null }[], pr.id, (x) => !!x.latest_analysis_run_id),
        toReview: count(f.data as { project_id: string; analytical_run_id: string }[], pr.id, (x) => latest.has(x.analytical_run_id)),
        unverified: count(c.data as { project_id: string }[], pr.id),
        reports: count(r.data as { project_id: string }[], pr.id),
      })),
    )
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
      <h1 id="orgs-heading">Dashboard</h1>
      <h2>Your projects</h2>
      {work === null ? (
        <p className="muted">Loading…</p>
      ) : work.length === 0 ? (
        <p className="empty">No projects yet. Open an organisation below to create one.</p>
      ) : (
        <table className="table compact" aria-label="Your projects">
          <thead>
            <tr>
              <th scope="col">Project</th>
              <th scope="col">Dataset versions</th>
              <th scope="col">Unusual units to review</th>
              <th scope="col">Unverified allegations</th>
              <th scope="col">Reports</th>
              <th scope="col">
                <span className="visually-hidden">Go to</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {work.map((w) => (
              <tr key={w.project.id}>
                <th scope="row">
                  <Link to={`/projects/${w.project.id}`}>{w.project.election_name}</Link>{' '}
                  <span className="muted small">{w.project.election_year}</span>
                </th>
                <td>
                  {w.versions} <span className="muted small">({w.analysed} analysed)</span>
                </td>
                <td>{w.toReview > 0 ? <Link to={`/projects/${w.project.id}/findings`}>{w.toReview} high or very high</Link> : '0'}</td>
                <td>{w.unverified > 0 ? <Link to={`/projects/${w.project.id}/claims`}>{w.unverified}</Link> : '0'}</td>
                <td>{w.reports > 0 ? <Link to={`/projects/${w.project.id}/reports`}>{w.reports}</Link> : '0'}</td>
                <td className="small">
                  <Link to={`/projects/${w.project.id}/findings`}>Findings</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Organisations</h2>
      <p className="page-intro">The organisations you belong to and your role in each.</p>
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
                  <Link to={`/orgs/${o.org_id}/projects`}>Projects</Link>
                  <span aria-hidden="true">&ensp;</span>
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
