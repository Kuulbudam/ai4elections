import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, sleep, type Job } from '../lib/api'
import { friendlyError, getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type Indicator = 'turnout_rate' | 'leading_party_share'
type IndicatorSummary = {
  eligible: number
  scored: number
  flagged: number
  lga_fallback: number
  not_scored: Record<string, number>
  flagged_by_severity: Record<'medium' | 'high' | 'very_high', number>
}
type RunSummary = {
  units: number
  indicators: Record<Indicator, IndicatorSummary>
  statistical_findings: number
  data_quality_findings: number
  data_quality_by_rule: Record<string, number>
}
type Run = {
  id: string
  engine_version: string
  method_version: string
  parameters: { min_peers: number; threshold: number }
  content_hash: string
  completed_at: string
  summary: RunSummary
  not_scored: { indicator: Indicator; pu_code: string | null; source_row_number: number; reason: string; rules?: string[]; detail?: string; n?: number; ward_n?: number; lga_n?: number }[]
}
type Version = {
  org_id: string
  project_id: string
  original_filename: string
  version_number: number
  validated_at: string | null
  latest_analysis_run_id: string | null
}
type FindingRow = {
  id: string
  indicator: Indicator
  pu_code: string | null
  lga: string | null
  ward: string | null
  severity: 'medium' | 'high' | 'very_high'
  confidence: 'low' | 'medium' | 'high'
  peer_group: { level: 'ward' | 'lga'; n: number }
  statistics: { value: number; median: number; robust_z: number; leading_party?: string }
}

const PAGE = 50
const RUNNERS = ['admin', 'analyst', 'researcher']
const LABEL: Record<Indicator, string> = { turnout_rate: 'Turnout', leading_party_share: 'Leading-party share' }
const SEV: Record<string, string> = { medium: 'Medium', high: 'High', very_high: 'Very high' }
const REASON: Record<string, string> = {
  excluded_by_validation: 'Excluded by a data check',
  not_computable: 'Cannot be calculated (missing or zero count)',
  missing_geography: 'Missing state, LGA or ward',
  insufficient_peers: 'Too few comparable units (ward and LGA)',
  zero_mad: 'Peers are all the same (no spread to measure against)',
}
const pct = (x: number) => `${(x * 100).toFixed(1)}%`

export default function Analysis() {
  const { versionId = '' } = useParams()
  const [version, setVersion] = useState<Version | null | undefined>(undefined)
  const [run, setRun] = useState<Run | null>(null)
  const [indicator, setIndicator] = useState<Indicator | ''>('')
  const [severity, setSeverity] = useState<string>('very_high')
  const [rows, setRows] = useState<FindingRow[] | null>(null)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const role = useRole(version?.org_id)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const v = await sb
      .from('dataset_versions')
      .select('org_id, project_id, original_filename, version_number, validated_at, latest_analysis_run_id')
      .eq('id', versionId)
      .maybeSingle()
    if (v.error) setError(friendlyError(v.error))
    setVersion((v.data as Version) ?? null)
    const runId = (v.data as Version | null)?.latest_analysis_run_id
    if (!runId) return setRun(null)
    const r = await sb
      .from('analytical_runs')
      .select('id, engine_version, method_version, parameters, content_hash, completed_at, summary, not_scored')
      .eq('id', runId)
      .maybeSingle()
    if (r.error) setError(friendlyError(r.error))
    setRun((r.data as Run) ?? null)
  }, [versionId])

  const loadFindings = useCallback(async () => {
    const sb = getSupabase()
    if (!sb || !run) return
    let q = sb
      .from('findings')
      .select('id, indicator, pu_code, lga, ward, severity, confidence, peer_group, statistics', { count: 'exact' })
      .eq('analytical_run_id', run.id)
      .eq('category', 'statistical_anomaly')
      .order('lga')
      .order('ward')
      .order('pu_code')
      .range(page * PAGE, page * PAGE + PAGE - 1)
    if (indicator) q = q.eq('indicator', indicator)
    if (severity) q = q.eq('severity', severity)
    const { data, count, error: e } = await q
    if (e) setError(friendlyError(e))
    setRows((data as FindingRow[]) ?? [])
    setTotal(count ?? 0)
  }, [run, indicator, severity, page])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void loadFindings()
  }, [loadFindings])

  async function start() {
    setError(null)
    try {
      setRunning('Queuing analysis…')
      let job = await api<Job>('POST', '/jobs', { job_type: 'analyse', dataset_version_id: versionId }, { 'Idempotency-Key': crypto.randomUUID() })
      setRunning('Analysing (a worker picks this up, usually within a minute)…')
      const deadline = Date.now() + 10 * 60 * 1000
      while ((job.status === 'queued' || job.status === 'running') && Date.now() < deadline) {
        await sleep(3000)
        job = await api<Job>('GET', `/jobs/${job.id}`)
      }
      if (job.status === 'succeeded') {
        setPage(0)
        await load()
      } else if (job.status === 'failed') setError('Analysis failed. Re-run validation, then try again.')
      else setError('Analysis is taking longer than expected. It continues in the background; refresh later.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start analysis. Please try again.')
    }
    setRunning(null)
  }

  if (version === undefined) return <p className="muted">Loading…</p>
  if (version === null) return <p className="empty">Dataset version not found, or you are not a member of its organisation.</p>
  const canRun = role !== null && RUNNERS.includes(role)
  const s = run?.summary

  return (
    <section className="page" aria-labelledby="analysis-heading">
      <p>
        <Link to={`/projects/${version.project_id}`}>← Project</Link> · <Link to={`/versions/${versionId}/validation`}>Validation</Link> ·{' '}
        <Link to={`/projects/${version.project_id}/findings`}>All findings</Link> · <Link to={`/versions/${versionId}/areas`}>Areas</Link> ·{' '}
        <Link to={`/projects/${version.project_id}/reports`}>Reports</Link>
      </p>
      <div className="row-between">
        <h1 id="analysis-heading">
          Analysis — {version.original_filename} (v{version.version_number})
        </h1>
        {canRun && (
          <button type="button" className="primary" onClick={() => void start()} disabled={!!running || !version.validated_at}>
            {run ? 'Re-run analysis' : 'Run analysis'}
          </button>
        )}
      </div>
      <p className="page-intro">
        Each polling unit is compared with the other units in its ward. A unit is listed when its turnout or leading-party share is far from its
        peers. That is a reason to check the result sheet, not a conclusion about what happened.
      </p>
      {!version.validated_at && (
        <p className="empty">
          Run validation first. <Link to={`/versions/${versionId}/validation`}>Open validation</Link>
        </p>
      )}
      {running && (
        <p className="card" aria-live="polite">
          {running}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {!run || !s ? (
        version.validated_at && !running && <p className="empty">This version has not been analysed yet.</p>
      ) : (
        <>
          <div className="stats" role="list" aria-label="Analysis summary">
            <Stat label="Polling units" value={s.units} />
            <Stat label="Unusual turnout" value={s.indicators.turnout_rate.flagged} tone="warn" />
            <Stat label="Unusual leading-party share" value={s.indicators.leading_party_share.flagged} tone="warn" />
            <Stat label="Data quality findings" value={s.data_quality_findings} tone="bad" />
          </div>

          <h3>How each indicator was scored</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Indicator</th>
                <th scope="col">Scored</th>
                <th scope="col">Compared with LGA</th>
                <th scope="col">Medium</th>
                <th scope="col">High</th>
                <th scope="col">Very high</th>
                <th scope="col">Not scored</th>
              </tr>
            </thead>
            <tbody>
              {(Object.keys(LABEL) as Indicator[]).map((k) => {
                const x = s.indicators[k]
                const ns = Object.values(x.not_scored).reduce((a, b) => a + b, 0)
                return (
                  <tr key={k}>
                    <th scope="row">{LABEL[k]}</th>
                    <td>{x.scored.toLocaleString()}</td>
                    <td>{x.lga_fallback.toLocaleString()}</td>
                    <td>{x.flagged_by_severity.medium}</td>
                    <td>{x.flagged_by_severity.high}</td>
                    <td>{x.flagged_by_severity.very_high}</td>
                    <td>
                      {ns.toLocaleString()}
                      {ns > 0 && (
                        <span className="muted small">
                          {' '}
                          ({Object.entries(x.not_scored).map(([r, n]) => `${REASON[r] ?? r}: ${n}`).join('; ')})
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="row-between">
            <h3>Unusual polling units</h3>
            <div className="filters">
              <label className="inline-label">
                Indicator
                <select value={indicator} onChange={(e) => { setIndicator(e.target.value as Indicator | ''); setPage(0) }}>
                  <option value="">Both</option>
                  <option value="turnout_rate">Turnout</option>
                  <option value="leading_party_share">Leading-party share</option>
                </select>
              </label>
              <label className="inline-label">
                How unusual
                <select value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(0) }}>
                  <option value="">All</option>
                  <option value="very_high">Very high</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                </select>
              </label>
            </div>
          </div>
          {rows === null ? (
            <p className="muted">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="empty">No polling units match these filters.</p>
          ) : (
            <>
              <table className="table compact">
                <thead>
                  <tr>
                    <th scope="col">Polling unit</th>
                    <th scope="col">LGA / ward</th>
                    <th scope="col">Indicator</th>
                    <th scope="col">Value</th>
                    <th scope="col">Peer median</th>
                    <th scope="col">Robust z</th>
                    <th scope="col">How unusual</th>
                    <th scope="col">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((f) => (
                    <tr key={f.id}>
                      <td>
                        <Link to={`/findings/${f.id}`}>
                          <code>{f.pu_code}</code>
                        </Link>
                      </td>
                      <td className="small">
                        {f.lga} / {f.ward}
                        {f.peer_group.level === 'lga' && <span className="muted"> (compared with LGA)</span>}
                      </td>
                      <td>
                        {LABEL[f.indicator]}
                        {f.statistics.leading_party && <span className="muted small"> ({f.statistics.leading_party})</span>}
                      </td>
                      <td>{pct(f.statistics.value)}</td>
                      <td>
                        {pct(f.statistics.median)} <span className="muted small">of {f.peer_group.n}</span>
                      </td>
                      <td>{f.statistics.robust_z > 0 ? '+' : ''}{f.statistics.robust_z.toFixed(2)}</td>
                      <td>
                        <span className={`sev sev-${f.severity}`}>{SEV[f.severity]}</span>
                      </td>
                      <td>{f.confidence}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row-between small">
                <span>
                  {page * PAGE + 1}–{Math.min(total, page * PAGE + PAGE)} of {total.toLocaleString()}
                </span>
                <span>
                  <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>
                    Previous
                  </button>{' '}
                  <button type="button" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>
                    Next
                  </button>
                </span>
              </div>
            </>
          )}

          <details className="card">
            <summary>Polling units that were not scored ({run.not_scored.length.toLocaleString()})</summary>
            <table className="table compact">
              <thead>
                <tr>
                  <th scope="col">Source row</th>
                  <th scope="col">Polling unit</th>
                  <th scope="col">Indicator</th>
                  <th scope="col">Why</th>
                </tr>
              </thead>
              <tbody>
                {run.not_scored.slice(0, 500).map((n) => (
                  <tr key={`${n.indicator}-${n.source_row_number}`}>
                    <td>{n.source_row_number}</td>
                    <td>{n.pu_code ?? '—'}</td>
                    <td>{LABEL[n.indicator]}</td>
                    <td>
                      {REASON[n.reason] ?? n.reason}
                      {n.rules && n.rules.length > 0 && <span className="muted"> ({n.rules.join(', ')})</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {run.not_scored.length > 500 && <p className="muted small">Showing the first 500.</p>}
          </details>

          <p className="muted small">
            Method {run.method_version}, engine {run.engine_version}: peers are the same ward (the LGA when the ward has fewer than{' '}
            {run.parameters.min_peers} usable units); a unit is listed when |robust z| ≥ {run.parameters.threshold}. Run completed{' '}
            {new Date(run.completed_at).toLocaleString()}. Content hash <code>{run.content_hash.slice(0, 16)}…</code> — the same data always gives the
            same hash.
          </p>
        </>
      )}
    </section>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'good' | 'warn' | 'bad' }) {
  return (
    <div className={`stat ${tone ?? ''}`} role="listitem">
      <div className="stat-value">{value.toLocaleString()}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}
