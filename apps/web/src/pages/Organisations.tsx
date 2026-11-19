import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { friendlyError, getSupabase, type Role } from '../lib/supabase'

type OrgRow = { org_id: string; role: Role; organisations: { name: string } | null }
type ProjectRow = { id: string; org_id: string; election_name: string; election_year: number }
type VersionRow = {
  id: string
  project_id: string
  mapping_saved_at: string | null
  validated_at: string | null
  latest_analysis_run_id: string | null
}
type Next = { step: number; label: string; action: string; to: string } | null
type WorkRow = { project: ProjectRow; versions: number; toReview: number; unverified: number; reports: number; next: Next }

/** The project's next step, following the same six-step workflow as the project menu. */
function nextStep(pid: string, v: VersionRow | undefined, toReview: number, reports: number): Next {
  if (!v) return { step: 1, label: 'Upload data', action: 'Upload a results file', to: `/projects/${pid}/upload` }
  if (!v.mapping_saved_at) return { step: 2, label: 'Map columns', action: 'Map the columns', to: `/versions/${v.id}/mapping` }
  if (!v.validated_at) return { step: 3, label: 'Check data', action: 'Run the data checks', to: `/versions/${v.id}/validation` }
  if (!v.latest_analysis_run_id) return { step: 4, label: 'Analyse', action: 'Run the analysis', to: `/versions/${v.id}/analysis` }
  if (toReview > 0)
    return { step: 5, label: 'Review findings', action: `Review ${toReview} high or very high finding${toReview > 1 ? 's' : ''}`, to: `/projects/${pid}/findings` }
  if (reports === 0) return { step: 6, label: 'Report', action: 'Generate a report', to: `/projects/${pid}/reports` }
  return null
}

const rows = <T,>(d: unknown) => (Array.isArray(d) ? (d as T[]) : [])

async function fetchWork(sb: NonNullable<ReturnType<typeof getSupabase>>): Promise<WorkRow[]> {
  const [p, v, f, c, r] = await Promise.all([
    sb.from('projects').select('id, org_id, election_name, election_year').is('archived_at', null).order('created_at', { ascending: false }).limit(50),
    sb
      .from('dataset_versions')
      .select('id, project_id, mapping_saved_at, validated_at, latest_analysis_run_id')
      .is('archived_at', null)
      .order('created_at', { ascending: false })
      .limit(1000),
    sb
      .from('finding_overview')
      .select('project_id, analytical_run_id')
      .eq('category', 'statistical_anomaly')
      .in('severity', ['very_high', 'high'])
      .eq('review_status', 'unreviewed')
      .limit(5000),
    sb.from('claims').select('project_id').eq('status', 'unverified_allegation').limit(5000),
    sb.from('reports').select('project_id').limit(5000),
  ])
  const vs = rows<VersionRow>(v.data)
  const fl = rows<{ project_id: string; analytical_run_id: string }>(f.data)
  const cl = rows<{ project_id: string }>(c.data)
  const rp = rows<{ project_id: string }>(r.data)
  return rows<ProjectRow>(p.data).map((pr) => {
    const mine = vs.filter((x) => x.project_id === pr.id)
    const current = mine.find((x) => x.latest_analysis_run_id) ?? mine[0]
    const toReview = fl.filter((x) => x.project_id === pr.id && x.analytical_run_id === current?.latest_analysis_run_id).length
    const reports = rp.filter((x) => x.project_id === pr.id).length
    return {
      project: pr,
      versions: mine.length,
      toReview,
      unverified: cl.filter((x) => x.project_id === pr.id).length,
      reports,
      next: nextStep(pr.id, current, toReview, reports),
    }
  })
}

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
    const { data, error } = await sb.from('memberships').select('org_id, role, organisations(name)').eq('user_id', session.user.id).order('created_at')
    if (error) setError(friendlyError(error))
    setOrgs((data as unknown as OrgRow[]) ?? [])
    // Dashboard: what needs attention in each project (all reads under RLS). Never blocks the page.
    try {
      setWork(await fetchWork(sb))
    } catch {
      setWork([])
    }
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
      {work && work.length > 0 && (
        <div className="kpis" role="list" aria-label="Across your projects">
          <div className="kpi" role="listitem">
            <div className="kpi-value">{work.length}</div>
            <div className="kpi-label">Projects</div>
          </div>
          <div className={`kpi ${work.some((w) => w.toReview) ? 'alert' : 'ok'}`} role="listitem">
            <div className="kpi-value">{work.reduce((a, w) => a + w.toReview, 0)}</div>
            <div className="kpi-label">High or very high findings to review</div>
          </div>
          <div className="kpi" role="listitem">
            <div className="kpi-value">{work.reduce((a, w) => a + w.unverified, 0)}</div>
            <div className="kpi-label">Unverified allegations</div>
          </div>
          <div className="kpi" role="listitem">
            <div className="kpi-value">{work.reduce((a, w) => a + w.reports, 0)}</div>
            <div className="kpi-label">Reports</div>
          </div>
        </div>
      )}
      <h2>What to do next</h2>
      {work === null ? (
        <p className="muted">Loading…</p>
      ) : work.length === 0 ? (
        <p className="empty">No projects yet. Create an organisation below, open it, and create your first project.</p>
      ) : (
        <ul className="todo" aria-label="What to do next">
          {work.map((w) => (
            <li key={w.project.id} className={w.next ? '' : 'todo-done'}>
              <div className="todo-step" aria-hidden="true">
                {w.next ? String(w.next.step).padStart(2, '0') : '✓'}
              </div>
              <div className="todo-body">
                <Link to={`/projects/${w.project.id}`} className="todo-project">
                  {w.project.election_name}
                </Link>
                <span className="small muted">
                  {w.next ? `Step ${w.next.step} of 6 · ${w.next.label}` : 'All steps done'} · {w.versions} dataset version{w.versions === 1 ? '' : 's'}
                  {w.unverified ? ` · ${w.unverified} unverified allegation${w.unverified === 1 ? '' : 's'}` : ''}
                </span>
              </div>
              {w.next ? (
                <Link className="button primary" to={w.next.to}>
                  {w.next.action}
                </Link>
              ) : (
                <Link className="button" to={`/projects/${w.project.id}/findings`}>
                  Open findings
                </Link>
              )}
            </li>
          ))}
        </ul>
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
