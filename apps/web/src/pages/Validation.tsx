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
    const v = await sb.from('dataset_versions').select('org_id, project_id, original_filename, version_number, status, mapping_saved_at, validated_at, validation_summary').eq('id', versionId).maybeSingle()
    if (v.error) setError(friendlyError(v.error))
    // Show the rule texts of the ruleset this version was actually validated with.
    const ruleset = (v.data as Version | null)?.validation_summary?.ruleset_version ?? '1.1.0'
    const r = await sb.from('validation_rules').select('rule_id, definition').eq('version', ruleset).order('rule_id')
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
      setRunning('Starting…')
      let job = await api<Job>('POST', '/jobs', { job_type: 'validate', dataset_version_id: versionId }, { 'Idempotency-Key': crypto.randomUUID() })
      setRunning('Checking every row. This usually takes under a minute…')
      const deadline = Date.now() + 10 * 60 * 1000
      while ((job.status === 'queued' || job.status === 'running') && Date.now() < deadline) {
        await sleep(3000)
        job = await api<Job>('GET', `/jobs/${job.id}`)
      }
      if (job.status === 'succeeded') {
        setPage(0)
        await loadVersion()
        await loadIssues()
      } else if (job.status === 'failed') setError('The check didn’t finish. Make sure the columns are matched correctly, then try again.')
      else setError('This is taking longer than usual. It will keep running; refresh the page in a few minutes.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Couldn’t start the check. Try again.')
    }
    setRunning(null)
  }

  if (version === undefined) return <p className="muted">Loading…</p>
  if (version === null) return <p className="empty">We couldn’t find this file, or you’re not on its team.</p>
  const s = version.validation_summary
  const canRun = role !== null && RUNNERS.includes(role)

  return (
    <section className="page" aria-labelledby="validation-heading">
      <p>
        <Link to={`/projects/${version.project_id}`}>← Project</Link>
      </p>
      <div className="row-between">
        <h1 id="validation-heading">
          Data check: {version.original_filename} (v{version.version_number})
        </h1>
        {canRun && (
          <button type="button" className="primary" onClick={() => void run()} disabled={!!running || !version.mapping_saved_at}>
            {s ? 'Check again' : 'Check data'}
          </button>
        )}
      </div>
      {!version.mapping_saved_at && (
        <p className="empty">
          Match the columns first. <Link to={`/versions/${versionId}/mapping`}>Match columns</Link>
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
        version.mapping_saved_at && !running && <p className="empty">This file hasn’t been checked yet.</p>
      ) : (
        <>
          <div className="stats" role="list" aria-label="Validation summary">
            <Stat label="Rows checked" value={s.rows} />
            <Stat label="No problems" value={s.row_status.eligible} tone="good" />
            <Stat label="Minor problems" value={s.row_status.eligible_with_warnings} tone="warn" />
            <Stat label="Left out of some results" value={s.row_status.quarantined_from_specific_analysis} tone="bad" />
          </div>
          <p className="muted small">
            Left out of turnout: {s.excluded_from.turnout_rate.toLocaleString()} rows. Left out of vote share:{' '}
            {s.excluded_from.leading_party_share.toLocaleString()} rows. A row is only left out where its problem matters. Checked{' '}
            {version.validated_at ? new Date(version.validated_at).toLocaleString() : ''} (rules {s.ruleset_version}).
          </p>
          <p>
            <Link to={`/versions/${versionId}/analysis`}>Next: analyse</Link>
          </p>
          {!s.reference_list_used && (
            <p className="notice warn">
              State, LGA and ward names weren’t checked against an official list (rule V011), because this project doesn’t have one yet. Add it on
              the project page, then check again.
            </p>
          )}

          <h3>Rules</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Type</th>
                <th scope="col">What it checks</th>
                <th scope="col">Problems</th>
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
            <h3>Problems{ruleFilter ? `: ${ruleFilter}` : ''}</h3>
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
            <p className="empty">No problems{ruleFilter ? ' for this rule' : ''}.</p>
          ) : (
            <>
              <table className="table compact">
                <thead>
                  <tr>
                    <th scope="col">Row</th>
                    <th scope="col">Rule</th>
                    <th scope="col">Column</th>
                    <th scope="col">Value in file</th>
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
