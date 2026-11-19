import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError, type Schemas } from '../lib/api'
import { ChartCard, Histogram, Kpi, RankedBars, Scatter, SegmentedBar } from './charts'

type Data = Schemas['VersionDashboard']
type Ind = 'turnout_rate' | 'leading_party_share'
const IND_LABEL: Record<Ind, string> = { turnout_rate: 'Turnout', leading_party_share: 'Leading-party share' }
const pct = (v: number) => `${(v * 100).toFixed(1)}%`

/** Power-BI-style dashboard: KPI row, then charts that all follow one filter bar (indicator + LGA). */
export default function AnalysisDashboard({ versionId, runId, ruleCounts }: { versionId: string; runId: string; ruleCounts?: Record<string, number> }) {
  const navigate = useNavigate()
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ind, setInd] = useState<Ind>('turnout_rate')
  const [lga, setLga] = useState<number | null>(null)

  useEffect(() => {
    let live = true
    api<Data>('GET', `/dataset-versions/${versionId}/dashboard`)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e instanceof ApiError ? e.message : 'Could not load the dashboard.'))
    return () => {
      live = false
    }
  }, [versionId, runId])

  const view = useMemo(() => {
    if (!data) return null
    const col = ind === 'turnout_rate' ? 1 : 2
    const units = data.units.filter((u) => lga === null || u[0] === lga)
    const values = units.map((u) => u[col]).filter((v): v is number => v !== null)
    const flaggedAll = data.flagged.filter((f) => f.indicator === ind)
    const flagged = flaggedAll.filter((f) => lga === null || f.lga === lga)
    const byLga = new Map<number, number>()
    for (const f of flaggedAll) byLga.set(f.lga, (byLga.get(f.lga) ?? 0) + 1)
    const ranked = [...byLga.entries()]
      .sort((a, b) => b[1] - a[1] || data.lgas[a[0]].localeCompare(data.lgas[b[0]]))
      .slice(0, 12)
      .map(([k, v]) => ({ key: String(k), label: data.lgas[k], value: v }))
    const sev = { medium: 0, high: 0, very_high: 0 } as Record<string, number>
    for (const f of flagged) sev[f.severity] = (sev[f.severity] ?? 0) + 1
    return { units, values, flagged, ranked, sev }
  }, [data, ind, lga])

  if (error)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    )
  if (!data || !view) return <p className="muted">Loading dashboard…</p>
  const k = data.kpi
  const dq = Object.entries(ruleCounts ?? data.data_quality_by_rule)
    .sort((a, b) => b[1] - a[1])
    .map(([r, n]) => ({ key: r, label: r, value: n }))

  return (
    <section className="dash" aria-label="Analysis dashboard">
      <div className="kpis" role="list" aria-label="Analysis summary">
        <Kpi label="Polling units" value={k.units ?? 0} note={`${(k.turnout_counted ?? 0).toLocaleString()} usable for turnout`} />
        <Kpi label="Unusual turnout" value={k.unusual_turnout ?? 0} tone="alert" />
        <Kpi label="Unusual leading-party share" value={k.unusual_share ?? 0} tone="alert" />
        <Kpi label="Very high" value={k.very_high ?? 0} note="|robust z| ≥ 7" tone="alert" />
        <Kpi label="High or very high not reviewed" value={k.to_review ?? 0} tone={k.to_review ? 'alert' : 'ok'} />
        <Kpi label="Data quality findings" value={k.data_quality ?? 0} />
      </div>

      <div className="filterbar" role="group" aria-label="Dashboard filters">
        <div className="seg" role="radiogroup" aria-label="Indicator">
          {(Object.keys(IND_LABEL) as Ind[]).map((x) => (
            <button key={x} type="button" role="radio" aria-checked={ind === x} className={ind === x ? 'on' : ''} onClick={() => setInd(x)}>
              {IND_LABEL[x]}
            </button>
          ))}
        </div>
        <label className="inline-label">
          Area (state / LGA)
          <select value={lga ?? ''} onChange={(e) => setLga(e.target.value === '' ? null : Number(e.target.value))}>
            <option value="">All areas</option>
            {data.lgas.map((l, i) => (
              <option key={l} value={i}>
                {l}
              </option>
            ))}
          </select>
        </label>
        {lga !== null && (
          <button type="button" className="link" onClick={() => setLga(null)}>
            Clear area filter
          </button>
        )}
        <span className="muted small">All charts follow these filters. Click a bar in “Unusual units by area” to filter.</span>
      </div>

      <div className="dash-grid">
        <ChartCard
          title={`${IND_LABEL[ind]} across polling units`}
          subtitle={`${view.values.length.toLocaleString()} units${lga !== null ? ` in ${data.lgas[lga]}` : ''}`}
        >
          <Histogram values={view.values} marks={view.flagged.map((f) => ({ value: f.value, severity: f.severity }))} label={IND_LABEL[ind]} />
        </ChartCard>
        <ChartCard
          title="Unusual units against their peers"
          subtitle="Each dot is a polling unit; distance from the diagonal is how far it is from its peer median. Click to open."
        >
          <Scatter
            points={view.flagged.map((f) => ({
              id: f.finding_id,
              x: f.median,
              y: f.value,
              severity: f.severity,
              label: f.pu_code ?? 'unit',
              detail: `${pct(f.value)} vs peers ${pct(f.median)} · z ${f.robust_z > 0 ? '+' : ''}${f.robust_z.toFixed(1)}${f.ward ? ` · ${f.ward}` : ''}`,
            }))}
            onPick={(id) => navigate(`/findings/${id}`)}
            xLabel="Peer group median"
            yLabel={`This unit's ${IND_LABEL[ind].toLowerCase()}`}
          />
        </ChartCard>
        <ChartCard title="Unusual units by area" subtitle={`Top ${view.ranked.length} areas, ${IND_LABEL[ind].toLowerCase()}`}>
          {view.ranked.length ? (
            <RankedBars
              rows={view.ranked}
              selected={lga === null ? undefined : String(lga)}
              onPick={(key) => setLga(lga === Number(key) ? null : Number(key))}
              unit="unusual units"
            />
          ) : (
            <p className="muted small">No unusual units for this indicator.</p>
          )}
        </ChartCard>
        <div className="dash-stack">
          <ChartCard title="How unusual" subtitle={`${view.flagged.length} unusual units${lga !== null ? ' in this area' : ''}`}>
            <SegmentedBar parts={['medium', 'high', 'very_high'].map((s) => ({ key: s, value: view.sev[s] ?? 0 }))} label="How unusual" />
            <p className="small muted">
              Medium: |z| 3.5–5 · High: 5–7 · Very high: 7 or more. How unusual is not the same as how sure: see confidence on each finding.
            </p>
          </ChartCard>
          <ChartCard title="Data quality findings by rule" subtitle="Whole dataset (not filtered)">
            {dq.length ? <RankedBars rows={dq} unit="polling units" /> : <p className="muted small">No data quality findings.</p>}
          </ChartCard>
        </div>
      </div>
    </section>
  )
}
