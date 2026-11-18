import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, sleep, type Job } from '../lib/api'
import { friendlyError, getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type Summary = {
  rows: number
  row_status: Record<'eligible' | 'eligible_with_warnings' | 'quarantined_from_specific_analysis', number>
  excluded_from: Record<'turnout_rate' | 'leading_party_share', number>
  issues_by_rule: Record<string, number>
  rows_by_rule: Record<string, number>
  reference_list_used: boolean
  ruleset_version: string
}
type Version = {
  org_id: string
  project_id: string
  original_filename: string
  version_number: number
  status: string
  mapping_saved_at: string | null
  validated_at: string | null
  validation_summary: Summary | null
}
type Rule = { rule_id: string; definition: { severity: string; description: string; remediation: string; classification: string } }
type IssueRow = { id: string; rule_id: string; severity: string; source_row_number: number | null; source_column: string | null; original_value: string | null; message: string }

const PAGE = 50
const RUNNERS = ['admin', 'analyst', 'researcher']

export default function Validation() {
  const { versionId = '' } = useParams()
  const [version, setVersion] = useState<Version | null | undefined>(undefined)
  const [rules, setRules] = useState<Rule[]>([])
  const [ruleFilter, setRuleFilter] = useState<string>('')
  const [issues, setIssues] = useState<IssueRow[] | null>(null)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const role = useRole(version?.org_id)

  const loadVersion = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const [v, r] = await Promise.all([
      sb.from('dataset_versions').select('org_id, project_id, original_filename, version_number, status, mapping_saved_at, validated_at, validation_summary').eq('id', versionId).maybeSingle(),
      sb.from('validation_rules').select('rule_id, definition').eq('version', '1.0.0').order('rule_id'),
    ])
    if (v.error) setError(friendlyError(v.error))
    setVersion((v.data as Version) ?? null)
    setRules((r.data as Rule[]) ?? [])
  }, [versionId])

  const loadIssues = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    let q = sb
      .from('validation_issues')
      .select('id, rule_id, severity, source_row_number, source_column, original_value, message', { count: 'exact' })
      .eq('dataset_version_id', versionId)
      .order('source_row_number', { ascending: true })
      .order('rule_id')
      .range(page * PAGE, page * PAGE + PAGE - 1)
    if (ruleFilter) q = q.eq('rule_id', ruleFilter)
    const { data, count, error } = await q
    if (error) setError(friendlyError(error))
    setIssues((data as IssueRow[]) ?? [])
    setTotal(count ?? 0)
  }, [versionId, ruleFilter, page])

  useEffect(() => {
    // loaders only set state after awaiting the network, never synchronously
    // oxlint-disable-next-line react/set-state-in-effect
    void loadVersion()
  }, [loadVersion])
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void loadIssues()
  }, [loadIssues])

  async function run() {
    setError(null)
    try {
      setRunning('Queuing validation…')
      let job = await api<Job>('POST', '/jobs', { job_type: 'validate', dataset_version_id: versionId }, { 'Idempotency-Key': crypto.randomUUID() })
      setRunning('Validating (a worker picks this up, usually within a minute)…')
      const deadline = Date.now() + 10 * 60 * 1000
      while ((job.status === 'queued' || job.status === 'running') && Date.now() < deadline) {
        await sleep(3000)
        job = await api<Job>('GET', `/jobs/${job.id}`)
      }
      if (job.status === 'succeeded') {
        setPage(0)
        await loadVersion()
        await loadIssues()
      } else if (job.status === 'failed') setError('Validation failed. Check the column mapping and try again.')
      else setError('Validation is taking longer than expected. It continues in the background; refresh later.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start validation. Please try again.')
    }
    setRunning(null)
  }

  if (version === undefined) return <p className="muted">Loading…</p>
  if (version === null) return <p className="empty">Dataset version not found, or you are not a member of its organisation.</p>
  const s = version.validation_summary
  const canRun = role !== null && RUNNERS.includes(role)

  return (
    <section className="page" aria-labelledby="validation-heading">
      <p>
        <Link to={`/projects/${version.project_id}`}>← Project</Link>
      </p>
      <div className="row-between">
        <h2 id="validation-heading">
          Validation — {version.original_filename} (v{version.version_number})
        </h2>
        {canRun && (
          <button type="button" className="primary" onClick={() => void run()} disabled={!!running || !version.mapping_saved_at}>
            {s ? 'Re-run validation' : 'Run validation'}
          </button>
        )}
      </div>
      {!version.mapping_saved_at && (
        <p className="empty">
          Map the columns first. <Link to={`/versions/${versionId}/mapping`}>Open column mapping</Link>
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

      {!s ? (
        version.mapping_saved_at && !running && <p className="empty">This version has not been validated yet.</p>
      ) : (
        <>
          <div className="stats" role="list" aria-label="Validation summary">
            <Stat label="Rows checked" value={s.rows} />
            <Stat label="Eligible" value={s.row_status.eligible} tone="good" />
            <Stat label="Eligible with warnings" value={s.row_status.eligible_with_warnings} tone="warn" />
            <Stat label="Quarantined from some analysis" value={s.row_status.quarantined_from_specific_analysis} tone="bad" />
          </div>
          <p className="muted small">
            Excluded from turnout analysis: {s.excluded_from.turnout_rate.toLocaleString()} rows · excluded from vote-share analysis:{' '}
            {s.excluded_from.leading_party_share.toLocaleString()} rows. Rows are only excluded from the calculations their problem affects. Rule set{' '}
            {s.ruleset_version}, validated {version.validated_at ? new Date(version.validated_at).toLocaleString() : ''}.
          </p>
          {!s.reference_list_used && (
            <p className="notice warn">
              No reference list of administrative units is loaded for this project, so rule V011 (unmatched state / LGA / ward labels) was not
              checked. Load one on the project page, then re-run validation.
            </p>
          )}

          <h3>Rules</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Severity</th>
                <th scope="col">What it checks</th>
                <th scope="col">Issues</th>
                <th scope="col">Rows</th>
                <th scope="col">How to fix</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => {
                const n = s.issues_by_rule[r.rule_id] ?? 0
                const notChecked = r.rule_id === 'V011' && !s.reference_list_used
                return (
                  <tr key={r.rule_id}>
                    <td>
                      {n > 0 ? (
                        <button type="button" className="link" onClick={() => { setRuleFilter(r.rule_id); setPage(0) }}>
                          {r.rule_id}
                        </button>
                      ) : (
                        r.rule_id
                      )}
                    </td>
                    <td>
                      <span className={`sev sev-${r.definition.severity}`}>{r.definition.severity}</span>
                    </td>
                    <td>{r.definition.description}</td>
                    <td>{notChecked ? 'not checked' : n.toLocaleString()}</td>
                    <td>{notChecked ? '—' : (s.rows_by_rule[r.rule_id] ?? 0).toLocaleString()}</td>
                    <td className="small">{n > 0 ? r.definition.remediation : ''}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="row-between">
            <h3>Issues{ruleFilter ? ` — ${ruleFilter}` : ''}</h3>
            <label className="inline-label">
              Filter by rule
              <select value={ruleFilter} onChange={(e) => { setRuleFilter(e.target.value); setPage(0) }}>
                <option value="">All rules</option>
                {Object.keys(s.issues_by_rule).map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {issues === null ? (
            <p className="muted">Loading…</p>
          ) : issues.length === 0 ? (
            <p className="empty">No issues{ruleFilter ? ' for this rule' : ''}.</p>
          ) : (
            <>
              <table className="table compact">
                <thead>
                  <tr>
                    <th scope="col">Source row</th>
                    <th scope="col">Rule</th>
                    <th scope="col">Column</th>
                    <th scope="col">Original cell</th>
                    <th scope="col">Problem</th>
                  </tr>
                </thead>
                <tbody>
                  {issues.map((i) => (
                    <tr key={i.id}>
                      <td>{i.source_row_number ?? '—'}</td>
                      <td>
                        <span className={`sev sev-${i.severity}`}>{i.rule_id}</span>
                      </td>
                      <td>{i.source_column ?? '—'}</td>
                      <td>{i.original_value == null || i.original_value.trim() === '' ? <span className="muted">(blank)</span> : <code>{i.original_value}</code>}</td>
                      <td>{i.message}</td>
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
          <p className="muted small">Source row numbers count the header as row 1, as in a spreadsheet. Original cells are shown exactly as uploaded.</p>
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
