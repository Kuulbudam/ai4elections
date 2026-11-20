import { useCallback, useEffect, useState, type ChangeEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Kpi } from '../components/charts'
import { api, ApiError, type Schemas } from '../lib/api'
import { getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type Check = Schemas['CollationCheck']
type Area = Schemas['CollationArea']
type Version = { id: string; original_filename: string; version_number: number }

const STATUS: Record<Area['status'], { text: string; tone: string; rank: number }> = {
  mismatch: { text: 'Differs', tone: 'flag', rank: 0 },
  incomplete: { text: 'Not fully checked', tone: 'muted', rank: 1 },
  unmatched: { text: 'Not in the data', tone: 'todo', rank: 2 },
  match: { text: 'Matches', tone: 'ok', rank: 3 },
}
const LEVEL: Record<Area['level'], string> = { ward: 'Ward', lga: 'LGA', state: 'State' }
const name = (a: Area) => [a.state, a.lga, a.ward].filter(Boolean).join(' / ')

export default function Collation() {
  const { projectId = '', checkId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState<{ org_id: string; election_name: string } | null>(null)
  const [versions, setVersions] = useState<Version[]>([])
  const [version, setVersion] = useState('')
  const [checks, setChecks] = useState<Check[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const role = useRole(project?.org_id)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const [p, v] = await Promise.all([
      sb.from('projects').select('org_id, election_name').eq('id', projectId).maybeSingle(),
      sb
        .from('dataset_versions')
        .select('id, original_filename, version_number')
        .eq('project_id', projectId)
        .not('validated_at', 'is', null)
        .is('archived_at', null)
        .order('created_at', { ascending: false }),
    ])
    setProject(p.data)
    const vs = Array.isArray(v.data) ? (v.data as Version[]) : []
    setVersions(vs)
    setVersion((cur) => cur || vs[0]?.id || '')
    try {
      setChecks((await api<{ items: Check[] }>('GET', `/collation-checks?project_id=${projectId}`)).items)
    } catch (err) {
      setChecks([])
      setError(err instanceof ApiError ? err.message : 'Could not load the collation checks.')
    }
  }, [projectId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || !version) return
    if (file.size > 2_000_000) {
      setError('The file is larger than 2 MB. Split it by state.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const csv_text = await file.text()
      const c = await api<Check>('POST', '/collation-checks', { project_id: projectId, dataset_version_id: version, filename: file.name, csv_text }, { 'Idempotency-Key': crypto.randomUUID() })
      setChecks((cs) => [c, ...(cs ?? [])])
      navigate(`/projects/${projectId}/collation/${c.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The file could not be checked. Try again.')
    }
    setBusy(false)
  }

  const canRun = role === 'admin' || role === 'analyst'
  const current = checkId ? checks?.find((c) => c.id === checkId) : checks?.[0]
  return (
    <section className="page" aria-labelledby="collation-heading">
      <h1 id="collation-heading">Collation check{project ? `: ${project.election_name}` : ''}</h1>
      <p className="page-intro">
        Compare the totals declared at ward, LGA or state collation (Forms EC8B, EC8C, EC8D) with the sum of their polling units in the uploaded results.
        Every area where a declared figure differs becomes a finding for review.
      </p>

      {canRun && (
        <div className="card">
          <h2>Check declared totals</h2>
          {versions.length === 0 ? (
            <p className="muted">Upload and validate the polling-unit results first.</p>
          ) : (
            <>
              {versions.length > 1 && (
                <label>
                  Compare with
                  <select value={version} onChange={(e) => setVersion(e.target.value)}>
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number}: {v.original_filename}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="file-drop">
                <span>{busy ? 'Adding up polling units and comparing…' : 'Choose the declared totals (CSV)'}</span>
                <span className="small muted">
                  One row per area: State, LGA, Ward (leave Ward blank for an LGA total, LGA and Ward blank for a state total), any of Registered, Accredited, Valid,
                  Rejected, Total votes cast, and one column per party.
                </span>
                <input type="file" accept=".csv,text/csv" disabled={busy} onChange={(e) => void onFile(e)} />
              </label>
              <p className="small">
                <a href="/samples/SYNTHETIC_declared_totals.csv" download>
                  Download a sample file
                </a>{' '}
                <span className="muted">(synthetic, for the demo data)</span>
              </p>
            </>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {checks === null ? (
        <p className="muted">Loading…</p>
      ) : checks.length === 0 ? (
        <p className="empty">No collation checks yet.</p>
      ) : (
        <>
          {checks.length > 1 && (
            <nav aria-label="Earlier checks" className="small">
              Checks:{' '}
              {checks.map((c, i) => (
                <span key={c.id}>
                  {i > 0 && ' · '}
                  {c.id === current?.id ? (
                    <strong>{new Date(c.created_at).toLocaleString()}</strong>
                  ) : (
                    <Link to={`/projects/${projectId}/collation/${c.id}`}>{new Date(c.created_at).toLocaleString()}</Link>
                  )}
                </span>
              ))}
            </nav>
          )}
          {current && <Results check={current} />}
        </>
      )}
    </section>
  )
}

function Results({ check }: { check: Check }) {
  const s = check.summary
  const areas = [...check.areas].sort((a, b) => STATUS[a.status].rank - STATUS[b.status].rank || a.row - b.row)
  return (
    <div className="collation-results">
      <h2>
        {check.filename} <span className="small muted">checked {new Date(check.created_at).toLocaleString()}</span>
      </h2>
      <div className="kpis" role="list" aria-label="Collation summary">
        <Kpi label="Areas compared" value={s.areas} />
        <Kpi label="Differ from their polling units" value={s.mismatch} tone={s.mismatch ? 'alert' : 'ok'} note={s.mismatch ? 'Sent to the findings queue' : undefined} />
        <Kpi label="Match exactly" value={s.match} />
        <Kpi label="Not fully checked" value={s.incomplete} note="Some polling units lack a figure" />
        <Kpi label="Not in the data" value={s.unmatched} />
      </div>
      {check.skipped.length > 0 && (
        <details className="notice warn">
          <summary>{check.skipped.length} row(s) of the file were not used</summary>
          <ul>
            {check.skipped.map((x) => (
              <li key={x.row}>
                Row {x.row}: {x.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Area</th>
              <th scope="col">Level</th>
              <th scope="col">Polling units</th>
              <th scope="col">Result</th>
              <th scope="col">Differences</th>
            </tr>
          </thead>
          <tbody>
            {areas.map((a) => {
              const st = STATUS[a.status]
              const diffs = a.figures.filter((f) => f.match === false)
              const unchecked = a.figures.filter((f) => f.match === null)
              return (
                <tr key={`${a.level}-${a.row}`} className={a.status === 'mismatch' ? 'row-flag' : undefined}>
                  <td>{name(a)}</td>
                  <td>{LEVEL[a.level]}</td>
                  <td>
                    {a.units.toLocaleString()}
                    {a.duplicate_units > 0 && <span className="small muted"> (+{a.duplicate_units} repeated, counted once)</span>}
                  </td>
                  <td>
                    <span className={`outcome outcome-${st.tone}`}>{st.text}</span>
                  </td>
                  <td>
                    {diffs.length > 0 && (
                      <ul className="diff-list">
                        {diffs.map((f) => (
                          <li key={f.field}>
                            {f.label}: declared {f.declared.toLocaleString()}, units add up to {f.data?.toLocaleString()} (
                            <strong>
                              {(f.diff ?? 0) > 0 ? '+' : ''}
                              {f.diff?.toLocaleString()}
                            </strong>
                            )
                          </li>
                        ))}
                      </ul>
                    )}
                    {unchecked.length > 0 && (
                      <span className="small muted">
                        Not checked: {unchecked.map((f) => `${f.label} (${f.missing_units} unit${f.missing_units === 1 ? '' : 's'} missing it)`).join(', ')}
                      </span>
                    )}
                    {a.finding_id && (
                      <p className="small">
                        <Link to={`/findings/${a.finding_id}`}>Open the finding</Link>
                      </p>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="small muted">
        File SHA-256 <code>{check.sha256.slice(0, 16)}…</code>. A difference can have a recorded reason (results cancelled at collation, units missing from the file,
        corrections); check the collation form before drawing any conclusion.
      </p>
    </div>
  )
}
