import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { CATEGORY, DECISION, INDICATOR, SEVERITY, pct, type Finding, type FindingPage } from '../lib/findings'
import { getSupabase } from '../lib/supabase'

type Filters = Record<'category' | 'indicator' | 'severity' | 'confidence' | 'review_status' | 'state' | 'lga' | 'ward', string>
const EMPTY: Filters = { category: '', indicator: '', severity: '', confidence: '', review_status: '', state: '', lga: '', ward: '' }

export default function Findings() {
  const { projectId = '' } = useParams()
  const [project, setProject] = useState<{ election_name: string } | null>(null)
  const [units, setUnits] = useState<{ state: string; lga: string; ward: string }[]>([])
  const [filters, setFilters] = useState<Filters>(EMPTY)
  const [items, setItems] = useState<Finding[] | null>(null)
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [next, setNext] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const sb = getSupabase()
    if (!sb) return
    sb.from('projects').select('election_name').eq('id', projectId).maybeSingle().then(({ data }) => setProject(data))
    sb.from('admin_units').select('state, lga, ward').eq('project_id', projectId).order('state').order('lga').order('ward').limit(1000)
      .then(({ data }) => setUnits(data ?? []))
  }, [projectId])

  const page = cursors[cursors.length - 1]
  const load = useCallback(async () => {
    setError(null)
    const q = new URLSearchParams({ project_id: projectId, limit: '50' })
    for (const [k, v] of Object.entries(filters)) if (v) q.set(k, v)
    if (page) q.set('cursor', page)
    try {
      const res = await api<FindingPage>('GET', `/findings?${q}`)
      setItems(res.items)
      setNext(res.next_cursor ?? null)
    } catch (err) {
      setItems([])
      setError(err instanceof ApiError ? err.message : 'Could not load findings.')
    }
  }, [projectId, filters, page])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  function set(k: keyof Filters, v: string) {
    const f = { ...filters, [k]: v }
    if (k === 'state') f.lga = f.ward = ''
    if (k === 'lga') f.ward = ''
    if (k === 'category' && (v === 'data_quality_issue' || v === 'cross_check')) f.indicator = ''
    setFilters(f)
    setCursors([null])
  }

  const states = useMemo(() => [...new Set(units.map((u) => u.state))], [units])
  const lgas = useMemo(() => [...new Set(units.filter((u) => u.state === filters.state).map((u) => u.lga))], [units, filters.state])
  const wards = useMemo(() => units.filter((u) => u.state === filters.state && u.lga === filters.lga).map((u) => u.ward), [units, filters.state, filters.lga])

  return (
    <section className="page" aria-labelledby="findings-heading">
      <p>
        <Link to={`/projects/${projectId}`}>← Project</Link> · <Link to={`/projects/${projectId}/claims`}>Claims</Link>
      </p>
      <h1 id="findings-heading">Findings{project ? `: ${project.election_name}` : ''}</h1>

      <form className="filters card" aria-label="Filter findings" onSubmit={(e) => e.preventDefault()}>
        <Select label="Type" value={filters.category} onChange={(v) => set('category', v)} options={CATEGORY} />
        <Select label="Measure" value={filters.indicator} onChange={(v) => set('indicator', v)} options={INDICATOR} disabled={filters.category === 'data_quality_issue' || filters.category === 'cross_check'} />
        <Select label="How unusual" value={filters.severity} onChange={(v) => set('severity', v)} options={SEVERITY} />
        <Select label="How sure" value={filters.confidence} onChange={(v) => set('confidence', v)} options={{ high: 'High', medium: 'Medium', low: 'Low' }} />
        <Select label="Review" value={filters.review_status} onChange={(v) => set('review_status', v)} options={DECISION} />
        {states.length > 0 ? (
          <>
            <Select label="State" value={filters.state} onChange={(v) => set('state', v)} options={Object.fromEntries(states.map((s) => [s, s]))} />
            <Select label="LGA" value={filters.lga} onChange={(v) => set('lga', v)} options={Object.fromEntries(lgas.map((s) => [s, s]))} disabled={!filters.state} />
            <Select label="Ward" value={filters.ward} onChange={(v) => set('ward', v)} options={Object.fromEntries(wards.map((s) => [s, s]))} disabled={!filters.lga} />
          </>
        ) : (
          <label className="inline-label">
            LGA
            <input value={filters.lga} onChange={(e) => set('lga', e.target.value)} placeholder="Exact name" />
          </label>
        )}
        <button type="button" className="link" onClick={() => { setFilters(EMPTY); setCursors([null]) }}>
          Clear filters
        </button>
      </form>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {items === null ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="empty">Nothing matches these filters. If you haven’t analysed a file yet, do that first.</p>
      ) : (
        <>
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Finding</th>
                <th scope="col">Type</th>
                <th scope="col">Where</th>
                <th scope="col">This unit / nearby</th>
                <th scope="col">How unusual</th>
                <th scope="col">How sure</th>
                <th scope="col">Review</th>
              </tr>
            </thead>
            <tbody>
              {items.map((f) => (
                <tr key={f.id}>
                  <td>
                    <Link to={`/findings/${f.id}`}>{f.title}</Link>
                    {(f.note_count ?? 0) + (f.claim_count ?? 0) > 0 && (
                      <span className="muted small">
                        {' '}
                        · {f.note_count ?? 0} notes · {f.claim_count ?? 0} claims
                      </span>
                    )}
                  </td>
                  <td className="small">{CATEGORY[f.category]}{f.rule_id ? ` (${f.rule_id})` : ''}</td>
                  <td className="small">
                    {f.lga} / {f.ward}
                  </td>
                  <td className="small">
                    {f.statistics ? (
                      <>
                        {pct(f.statistics.value)} vs {pct(f.statistics.median)}
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>
                    <span className={`sev sev-${f.severity}`}>{SEVERITY[f.severity]}</span>
                  </td>
                  <td>{f.confidence}</td>
                  <td className="small">{DECISION[f.review_status]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row-between small">
            <span>Page {cursors.length} · most unusual first</span>
            <span>
              <button type="button" disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>
                Previous
              </button>{' '}
              <button type="button" disabled={!next} onClick={() => next && setCursors([...cursors, next])}>
                Next
              </button>
            </span>
          </div>
        </>
      )}
    </section>
  )
}

function Select({ label, value, onChange, options, disabled }: { label: string; value: string; onChange: (v: string) => void; options: Record<string, string>; disabled?: boolean }) {
  return (
    <label className="inline-label">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        <option value="">All</option>
        {Object.entries(options).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
    </label>
  )
}
