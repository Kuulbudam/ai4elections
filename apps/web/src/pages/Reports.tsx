import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, type Schemas } from '../lib/api'
import { friendlyError, getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type ReportRow = { id: string; title: string; content_hash: string; generated_at: string; analytical_run_id: string }
type Analysed = { id: string; original_filename: string; version_number: number; latest_analysis_run_id: string }

export default function Reports() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState<{ org_id: string; election_name: string } | null>(null)
  const [rows, setRows] = useState<ReportRow[] | null>(null)
  const [versions, setVersions] = useState<Analysed[]>([])
  const [run, setRun] = useState('')
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const role = useRole(project?.org_id)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const [p, r, v] = await Promise.all([
      sb.from('projects').select('org_id, election_name').eq('id', projectId).maybeSingle(),
      sb.from('reports').select('id, title, content_hash, generated_at, analytical_run_id').eq('project_id', projectId).order('generated_at', { ascending: false }),
      sb.from('dataset_versions').select('id, original_filename, version_number, latest_analysis_run_id').eq('project_id', projectId).not('latest_analysis_run_id', 'is', null).is('archived_at', null).order('created_at', { ascending: false }),
    ])
    if (r.error) setError(friendlyError(r.error))
    setProject(p.data)
    setRows((r.data as ReportRow[]) ?? [])
    const vs = (v.data as Analysed[]) ?? []
    setVersions(vs)
    setRun((cur) => cur || vs[0]?.latest_analysis_run_id || '')
  }, [projectId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function generate() {
    setBusy(true)
    setError(null)
    try {
      const rep = await api<Schemas['Report']>('POST', '/reports', { project_id: projectId, analytical_run_id: run, title: title.trim() || null }, { 'Idempotency-Key': crypto.randomUUID() })
      navigate(`/reports/${rep.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not generate the report.')
    }
    setBusy(false)
  }

  const canGenerate = role === 'admin' || role === 'analyst'
  return (
    <section className="page" aria-labelledby="reports-heading">
      <p>
        <Link to={`/projects/${projectId}`}>← Project</Link>
      </p>
      <h1 id="reports-heading">Reports{project ? `: ${project.election_name}` : ''}</h1>
      {canGenerate && (
        <div className="card form" aria-label="Generate a report">
          {versions.length === 0 ? (
            <p className="empty">Analyse a results file first.</p>
          ) : (
            <>
              <label>
                Analysis
                <select value={run} onChange={(e) => setRun(e.target.value)}>
                  {versions.map((v) => (
                    <option key={v.id} value={v.latest_analysis_run_id}>
                      {v.original_filename} (v{v.version_number}), latest analysis
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Title (optional)
                <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
              </label>
              <button type="button" className="primary" disabled={busy || !run} onClick={() => void generate()}>
                {busy ? 'Making report…' : 'Make report'}
              </button>
            </>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="empty">No reports yet.</p>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th scope="col">Report</th>
              <th scope="col">Made</th>
              <th scope="col">Report ID</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>
                  <Link to={`/reports/${r.id}`}>{r.title}</Link>
                </td>
                <td className="small">{new Date(r.generated_at).toLocaleString()}</td>
                <td>
                  <code className="small">{r.content_hash.slice(0, 16)}…</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
