import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Schemas } from '../lib/api'
import { downloadPack } from '../lib/packs'
import { getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'
import { ChartCard, Kpi, RankedBars } from '../components/charts'

type Summary = Schemas['LiveSummary']
type Pack = Schemas['SubmissionPack']
const REFRESH_MS = 15_000

export default function Live() {
  const { projectId = '' } = useParams()
  const [orgId, setOrgId] = useState<string>()
  const role = useRole(orgId)
  const [s, setS] = useState<Summary | null>(null)
  const [packs, setPacks] = useState<Pack[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    void getSupabase()
      ?.from('projects')
      .select('org_id')
      .eq('id', projectId)
      .maybeSingle()
      .then(({ data }) => setOrgId(data?.org_id))
  }, [projectId])

  const load = useCallback(async () => {
    try {
      const [live, list] = await Promise.all([
        api<Summary>('GET', `/projects/${projectId}/live`),
        api<{ items: Pack[] }>('GET', `/projects/${projectId}/submission-packs`),
      ])
      setS(live)
      setPacks(list.items)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the live view.')
    }
  }, [projectId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    if (paused) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, REFRESH_MS)
    return () => clearInterval(t)
  }, [load, paused])

  async function makePack() {
    setBusy(true)
    setError(null)
    try {
      const p = await api<Pack>('POST', `/projects/${projectId}/submission-packs`, undefined, { 'Idempotency-Key': crypto.randomUUID() })
      setPacks((x) => [p, ...x.filter((y) => y.id !== p.id)])
      await downloadPack(p.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not make the pack.')
    } finally {
      setBusy(false)
    }
  }

  if (!s) {
    return (
      <section className="page" aria-labelledby="live-heading">
        <h1 id="live-heading">Live</h1>
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </section>
    )
  }

  const c = s.coverage
  const r = s.results
  return (
    <section className="page live" aria-labelledby="live-heading">
      <div className="page-head">
        <h1 id="live-heading">Live</h1>
        <p className="small muted" role="status">
          Updated {new Date(s.generated_at).toLocaleTimeString()}
          {s.last_report_at && ` · last report ${new Date(s.last_report_at).toLocaleTimeString()}`}{' '}
          <button type="button" className="link" onClick={() => setPaused((p) => !p)}>
            {paused ? 'Resume updates' : 'Pause updates'}
          </button>
        </p>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <div className="kpis" role="list" aria-label="Election day so far">
        <Kpi label="Polling units reporting" value={c.reporting_units} note={c.assigned_units ? `of ${c.assigned_units.toLocaleString()} with an observer` : undefined} />
        <Kpi label="Polls opened" value={c.opened} />
        <Kpi label="Results in" value={c.results} note={r.with_failed_checks ? `${r.with_failed_checks} failed a check` : undefined} />
        <Kpi label="Critical incidents" value={s.incidents.critical} tone={s.incidents.critical ? 'alert' : undefined} note={`${s.incidents.total} incidents in all`} />
        <Kpi label="Open escalations" value={s.incidents.open_escalations} tone={s.incidents.open_escalations ? 'alert' : undefined} />
      </div>

      <div className="live-grid">
        <ChartCard title="Votes so far" subtitle={`From ${r.units.toLocaleString()} confirmed result sheet${r.units === 1 ? '' : 's'}. Not official results.`}>
          {r.party_totals.length === 0 ? (
            <p className="empty">No confirmed result sheets yet.</p>
          ) : (
            <RankedBars unit="votes" rows={r.party_totals.slice(0, 12).map((p) => ({ key: p.party, label: p.party, value: p.votes }))} />
          )}
        </ChartCard>

        <div className="card">
          <h2>Incidents</h2>
          {s.incidents.recent.length === 0 ? (
            <p className="empty">No incidents reported.</p>
          ) : (
            <ul className="incidents" aria-label="Recent incidents">
              {s.incidents.recent.map((i) => (
                <li key={i.id}>
                  <span className={`sev sev-${i.severity === 'critical' ? 'very_high' : i.severity === 'medium' ? 'medium' : 'low'}`}>{i.severity}</span>{' '}
                  <strong>{i.label}</strong> at {i.pu_code}
                  <div className="small muted">
                    {i.lga} / {i.ward} · {new Date(i.captured_at).toLocaleTimeString()}
                    {i.escalation_id && (
                      <>
                        {' · '}
                        <Link to={`/escalations/${i.escalation_id}`}>{i.escalation_status === 'resolved' ? 'Resolved' : 'Escalated'}</Link>
                      </>
                    )}
                  </div>
                  <div className="small">{i.description}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <h2>By LGA</h2>
      {s.areas.length === 0 ? (
        <p className="empty">No polling units assigned or reporting yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">State</th>
                <th scope="col">LGA</th>
                <th scope="col" className="num">With an observer</th>
                <th scope="col" className="num">Reporting</th>
                <th scope="col" className="num">Results in</th>
                <th scope="col" className="num">Incidents</th>
                <th scope="col" className="num">Critical</th>
              </tr>
            </thead>
            <tbody>
              {s.areas.map((a) => (
                <tr key={`${a.state}/${a.lga}`}>
                  <td>{a.state}</td>
                  <td>{a.lga}</td>
                  <td className="num">{a.assigned}</td>
                  <td className="num">{a.reporting}</td>
                  <td className="num">{a.results}</td>
                  <td className="num">{a.incidents}</td>
                  <td className={`num${a.critical ? ' alert-text' : ''}`}>{a.critical}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card pack-card">
        <h2>Submission pack</h2>
        <p className="small muted">
          A zip of everything above: results by polling unit, field reports, incidents and escalations. It is signed, so anyone can check that nothing was changed after it was
          made. Your organisation hands it over itself.
        </p>
        <div className="row-actions">
          {role === 'admin' && (
            <button type="button" className="primary" disabled={busy} onClick={() => void makePack()}>
              {busy ? 'Making pack…' : 'Make signed pack'}
            </button>
          )}
          <Link to="/verify">Check a pack</Link>
        </div>
        {packs.length > 0 && (
          <ul className="packs" aria-label="Packs made">
            {packs.map((p) => (
              <li key={p.id}>
                {new Date(p.created_at).toLocaleString()} · {p.created_by_name ?? 'someone'} · {p.counts.polling_units_with_results} results, {p.counts.field_reports} reports{' '}
                <button type="button" className="link" onClick={() => void downloadPack(p.id).catch((e: Error) => setError(e.message))}>
                  Download
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
