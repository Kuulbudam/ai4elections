import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiText, ApiError } from '../lib/api'
import { CATEGORY, DECISION, INDICATOR, SEVERITY } from '../lib/findings'
import { friendlyError, getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type Stat = { value: string | null; median: string | null; robust_z: string | null; leading_party?: string | null } | null
type RFinding = {
  finding_id: string
  category: string
  indicator: string | null
  rule_id: string | null
  pu_code: string | null
  lga: string | null
  ward: string | null
  severity: string
  confidence: string
  title: string
  statistics: Stat
  peer_group: { n: number; fallback_used: boolean } | null
  evidence: { source_row_number: number | null }
  review_status: string
  review_history: { decision: string; reason: string; reviewer: string | null; at: string }[]
}
type Body = {
  project: { election_name: string; election_year: number; country: string; election_level: string }
  dataset: { name: string; version: number; file: string; sha256: string; rows: number; source_type: string; source_description: string; uploaded_at: string }
  analysis: { run_id: string; engine_version: string; method_version: string; rule_versions: { ruleset?: string }; parameters: { min_peers: number; threshold: number }; run_content_hash: string; completed_at: string }
  methodology: string
  validation: { rows: number; row_status: Record<string, number>; issues_by_rule: Record<string, number>; rows_by_rule: Record<string, number>; ruleset_version: string }
  findings: RFinding[]
  claims: { claim_id: string; statement: string; label: string; status: string; recorded_by: string | null; verified_by: string | null; verification_reason: string | null; evidence: unknown[] }[]
  limitations: string
}
type Row = { id: string; org_id: string; project_id: string; title: string; body: Body; content_hash: string; audit_event_id: string; audit_event_hash: string; generated_at: string; generated_by: string }

const pct = (s: string | null | undefined) => (s == null ? '—' : `${(Number(s) * 100).toFixed(1)}%`)
const SEV_ORDER: Record<string, number> = { very_high: 0, high: 1, medium: 2, low: 3, info: 4 }

export default function ReportView() {
  const { reportId = '' } = useParams()
  const [r, setR] = useState<Row | null | undefined>(undefined)
  const [by, setBy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const role = useRole(r?.org_id)

  useEffect(() => {
    const sb = getSupabase()
    if (!sb) return
    sb.from('reports')
      .select('id, org_id, project_id, title, body, content_hash, audit_event_id, audit_event_hash, generated_at, generated_by')
      .eq('id', reportId)
      .maybeSingle()
      .then(async ({ data, error: e }) => {
        if (e) setError(friendlyError(e))
        setR((data as Row) ?? null)
        if (data) {
          const p = await sb.from('profiles').select('email, display_name').eq('id', (data as Row).generated_by).maybeSingle()
          setBy(p.data?.display_name || p.data?.email || '')
        }
      })
  }, [reportId])

  async function download(format: 'csv' | 'md') {
    setError(null)
    try {
      const { text } = await apiText(`/reports/${reportId}/export?format=${format}`)
      const url = URL.createObjectURL(new Blob([text], { type: format === 'csv' ? 'text/csv' : 'text/markdown' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `ai4elections-report-${reportId}.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Download failed.')
    }
  }

  if (r === undefined) return <p className="muted">Loading…</p>
  if (r === null) return <p className="empty">{error ?? 'Report not found, or you are not a member of its organisation.'}</p>
  const b = r.body
  const stat = b.findings.filter((f) => f.category === 'statistical_anomaly').sort((x, y) => SEV_ORDER[x.severity] - SEV_ORDER[y.severity] || Math.abs(Number(y.statistics?.robust_z ?? 0)) - Math.abs(Number(x.statistics?.robust_z ?? 0)))
  const dq = b.findings.filter((f) => f.category === 'data_quality_issue')
  const dqByRule = dq.reduce<Record<string, number>>((acc, f) => ({ ...acc, [f.rule_id ?? '?']: (acc[f.rule_id ?? '?'] ?? 0) + 1 }), {})
  const reviewed = b.findings.filter((f) => f.review_history.length > 0)
  const canExport = role === 'admin' || role === 'analyst'

  return (
    <article className="page report" aria-labelledby="report-heading">
      <p className="no-print">
        <Link to={`/projects/${r.project_id}/reports`}>← Reports</Link>
      </p>
      <div className="row-between no-print">
        <span />
        <span className="actions">
          <button type="button" onClick={() => window.print()}>
            Print / save as PDF
          </button>
          {canExport && (
            <>
              <button type="button" onClick={() => void download('csv')}>
                Download CSV
              </button>
              <button type="button" onClick={() => void download('md')}>
                Download Markdown
              </button>
            </>
          )}
        </span>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <h1 id="report-heading">{r.title}</h1>
      <p className="page-intro">
        {b.project.election_name} · {b.project.election_year} · {b.project.country}
      </p>
      <aside className="notice warn">{b.limitations}</aside>

      <dl className="facts card">
        <dt>Generated</dt>
        <dd>
          {new Date(r.generated_at).toLocaleString()}
          {by ? ` by ${by}` : ''}
        </dd>
        <dt>Report content hash</dt>
        <dd>
          <code className="hash">{r.content_hash}</code>
        </dd>
        <dt>Audit checkpoint</dt>
        <dd>
          event <code className="hash">{r.audit_event_id}</code>, hash <code className="hash">{r.audit_event_hash}</code>
        </dd>
      </dl>

      <h2>Dataset and provenance</h2>
      <dl className="facts">
        <dt>Dataset</dt>
        <dd>
          {b.dataset.name}, version {b.dataset.version} ({b.dataset.file}), {b.dataset.rows.toLocaleString()} rows
        </dd>
        <dt>SHA-256</dt>
        <dd>
          <code className="hash">{b.dataset.sha256}</code>
        </dd>
        <dt>Source</dt>
        <dd>
          {b.dataset.source_type.replaceAll('_', ' ')}: {b.dataset.source_description}
        </dd>
      </dl>

      <h2>Analysis</h2>
      <dl className="facts">
        <dt>Run</dt>
        <dd>
          <code className="hash">{b.analysis.run_id}</code>, completed {new Date(b.analysis.completed_at).toLocaleString()}
        </dd>
        <dt>Versions</dt>
        <dd>
          engine {b.analysis.engine_version}, method {b.analysis.method_version}, rules {b.analysis.rule_versions?.ruleset ?? '—'}
        </dd>
        <dt>Parameters</dt>
        <dd>
          peers ≥ {b.analysis.parameters.min_peers}, listed when |robust z| ≥ {b.analysis.parameters.threshold}
        </dd>
        <dt>Run hash</dt>
        <dd>
          <code className="hash">{b.analysis.run_content_hash}</code>
        </dd>
      </dl>

      <h2>Methodology</h2>
      <p>{b.methodology}</p>

      <h2>Validation summary</h2>
      <p>
        {b.validation.rows.toLocaleString()} rows checked with rule set {b.validation.ruleset_version}.{' '}
        {Object.entries(b.validation.row_status ?? {})
          .map(([k, n]) => `${k.replaceAll('_', ' ')}: ${n.toLocaleString()}`)
          .join('; ')}
        .
      </p>
      <table className="table compact">
        <thead>
          <tr>
            <th scope="col">Rule</th>
            <th scope="col">Issues</th>
            <th scope="col">Rows</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(b.validation.issues_by_rule ?? {}).map(([k, n]) => (
            <tr key={k}>
              <th scope="row">{k}</th>
              <td>{n.toLocaleString()}</td>
              <td>{(b.validation.rows_by_rule?.[k] ?? 0).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Statistical outliers ({stat.length})</h2>
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
            <th scope="col">Review</th>
            <th scope="col">Row</th>
          </tr>
        </thead>
        <tbody>
          {stat.map((f) => (
            <tr key={f.finding_id}>
              <td>
                <Link to={`/findings/${f.finding_id}`}>{f.pu_code}</Link>
              </td>
              <td className="small">
                {f.lga} / {f.ward}
              </td>
              <td>
                {INDICATOR[f.indicator ?? ''] ?? ''}
                {f.statistics?.leading_party ? ` (${f.statistics.leading_party})` : ''}
              </td>
              <td>{pct(f.statistics?.value)}</td>
              <td>
                {pct(f.statistics?.median)}
                {f.peer_group ? <span className="muted small"> of {f.peer_group.n}{f.peer_group.fallback_used ? ' (LGA)' : ''}</span> : null}
              </td>
              <td>{f.statistics?.robust_z}</td>
              <td>{SEVERITY[f.severity]}</td>
              <td>{f.confidence}</td>
              <td className="small">{DECISION[f.review_status]}</td>
              <td>{f.evidence.source_row_number}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>{CATEGORY.data_quality_issue}s ({dq.length})</h2>
      <p>
        {Object.entries(dqByRule)
          .sort()
          .map(([k, n]) => `${k}: ${n.toLocaleString()}`)
          .join('; ') || 'None'}
        . The CSV export lists every one with its source row.
      </p>

      <h2>Review decisions</h2>
      {reviewed.length === 0 ? (
        <p>No review decisions have been recorded.</p>
      ) : (
        reviewed.map((f) => (
          <div key={f.finding_id} className="review-block">
            <p>
              <strong>{f.title}</strong>: current status <em>{DECISION[f.review_status]}</em>
            </p>
            <ul className="small">
              {f.review_history.map((h, i) => (
                <li key={i}>
                  {new Date(h.at).toLocaleString()}: {DECISION[h.decision]} by {h.reviewer ?? 'a member'}: {h.reason}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}

      <h2>Claims</h2>
      {b.claims.length === 0 ? (
        <p>No claims have been recorded.</p>
      ) : (
        <ul>
          {b.claims.map((c) => (
            <li key={c.claim_id}>
              <span className={`badge ${c.status === 'verified_fact' ? 'badge-verified' : 'badge-unverified'}`}>{c.label}</span> {c.statement}{' '}
              <span className="muted small">
                (recorded by {c.recorded_by ?? 'a member'}
                {c.verified_by ? `; verified by ${c.verified_by}: ${c.verification_reason}` : ''}; {c.evidence.length} evidence item(s))
              </span>
            </li>
          ))}
        </ul>
      )}

      <h2>Limitations</h2>
      <p>{b.limitations}</p>
      <p className="small muted">This report is a fixed snapshot. Reports are never edited; generate a new one to include later reviews.</p>
    </article>
  )
}
