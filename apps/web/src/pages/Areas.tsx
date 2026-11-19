import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError, type Schemas } from '../lib/api'
import { getSupabase } from '../lib/supabase'

type AreaList = Schemas['AreaList']
type Area = Schemas['AreaSummary']
type Level = 'state' | 'lga' | 'ward'
type SortKey = 'name' | 'turnout' | 'share' | 'flags' | 'units'

const BAR = '#2a78d6' // single-series hue (validated reference palette, slot 1)
const pct = (s: string | null | undefined) => (s == null ? '—' : `${(Number(s) * 100).toFixed(1)}%`)
const nameOf = (a: Area, level: Level) => (level === 'state' ? a.state : level === 'lga' ? a.lga : a.ward) ?? '(missing label)'
const flags = (a: Area) => (a.findings ? a.findings.unusual_turnout + a.findings.unusual_share : 0)

export default function Areas() {
  const { versionId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const state = params.get('state') ?? ''
  const lga = params.get('lga') ?? ''
  const level: Level = lga ? 'ward' : state ? 'lga' : 'state'
  const [version, setVersion] = useState<{ project_id: string; original_filename: string; version_number: number } | null>(null)
  const [data, setData] = useState<AreaList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>('name')
  const [picked, setPicked] = useState<string[]>([])

  useEffect(() => {
    getSupabase()
      ?.from('dataset_versions')
      .select('project_id, original_filename, version_number')
      .eq('id', versionId)
      .maybeSingle()
      .then(({ data: v }) => setVersion(v))
  }, [versionId])

  useEffect(() => {
    let live = true
    const q = new URLSearchParams({ dataset_version_id: versionId, level })
    if (state) q.set('state', state)
    if (lga) q.set('lga', lga)
    api<AreaList>('GET', `/areas?${q}`)
      .then((d) => live && (setData(d), setError(null), setPicked([])))
      .catch((err) => live && (setData(null), setError(err instanceof ApiError ? err.message : 'Could not load area totals.')))
    return () => {
      live = false
    }
  }, [versionId, level, state, lga])

  const rows = useMemo(() => {
    const items = [...(data?.items ?? [])]
    const by: Record<SortKey, (a: Area) => number | string> = {
      name: (a) => nameOf(a, level).toLowerCase(),
      turnout: (a) => -(Number(a.turnout.rate ?? -1)),
      share: (a) => -(Number(a.vote_share.leading_share ?? -1)),
      flags: (a) => -flags(a),
      units: (a) => -a.units,
    }
    return items.sort((a, b) => (by[sort](a) < by[sort](b) ? -1 : by[sort](a) > by[sort](b) ? 1 : 0))
  }, [data, sort, level])

  const drill = (a: Area) => {
    if (level === 'state' && a.state) setParams({ state: a.state })
    else if (level === 'lga' && a.lga) setParams({ state, lga: a.lga })
  }
  const keyOf = (a: Area) => [a.state, a.lga, a.ward].join('|')
  const compared = rows.filter((a) => picked.includes(keyOf(a)))
  const parties = [...new Set(compared.flatMap((a) => Object.keys(a.vote_share.party_totals)))].sort()

  return (
    <section className="page" aria-labelledby="areas-heading">
      <p>
        {version && <Link to={`/projects/${version.project_id}`}>← Project</Link>} · <Link to={`/versions/${versionId}/analysis`}>Analysis</Link>
        {version && (
          <>
            {' '}
            · <Link to={`/projects/${version.project_id}/findings`}>Findings</Link>
          </>
        )}
      </p>
      <h1 id="areas-heading">Areas{version ? ` — ${version.original_filename} (v${version.version_number})` : ''}</h1>
      <p className="page-intro">
        Totals by state, LGA and ward, built from the same units the analysis could use. Areas are named exactly as in the data; there is no map.
      </p>
      <nav aria-label="Area level" className="crumbs">
        <button type="button" className="link" onClick={() => setParams({})} disabled={level === 'state'}>
          All states
        </button>
        {state && (
          <>
            {' / '}
            <button type="button" className="link" onClick={() => setParams({ state })} disabled={level === 'lga'}>
              {state}
            </button>
          </>
        )}
        {lga && <> / {lga}</>}
      </nav>
      {error && (
        <p role="alert" className="error">
          {error}{' '}
          {error.includes('validation') && <Link to={`/versions/${versionId}/validation`}>Open validation</Link>}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.items.length === 0 && <p className="empty">No polling units in this area.</p>}
      {data && data.items.length > 0 && (
        <>
          {!data.analytical_run_id && (
            <p className="notice warn">
              No analysis has been run for this version, so finding counts are not shown. <Link to={`/versions/${versionId}/analysis`}>Run analysis</Link>
            </p>
          )}
          <div className="grid2 charts">
            <BarChart title="Turnout" note="accredited ÷ registered, counted units" rows={rows} level={level} value={(a) => (a.turnout.rate == null ? null : Number(a.turnout.rate))} format={(v) => `${(v * 100).toFixed(1)}%`} />
            {data.analytical_run_id && (
              <BarChart title="Unusual polling units" note="turnout + leading-share flags" rows={rows} level={level} value={(a) => flags(a)} format={(v) => String(v)} />
            )}
          </div>

          <div className="row-between">
            <h2>{level === 'state' ? 'States' : level === 'lga' ? `LGAs in ${state}` : `Wards in ${lga}`}</h2>
            <label className="inline-label">
              Sort by
              <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
                <option value="name">Name</option>
                <option value="turnout">Turnout (high first)</option>
                <option value="share">Leading-party share (high first)</option>
                <option value="flags">Unusual units (most first)</option>
                <option value="units">Polling units (most first)</option>
              </select>
            </label>
          </div>
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Compare</span>
                </th>
                <th scope="col">{level === 'state' ? 'State' : level === 'lga' ? 'LGA' : 'Ward'}</th>
                <th scope="col">Polling units</th>
                <th scope="col">Turnout</th>
                <th scope="col">Leading party</th>
                {data.analytical_run_id && (
                  <>
                    <th scope="col">Unusual turnout</th>
                    <th scope="col">Unusual share</th>
                    <th scope="col">Very high</th>
                    <th scope="col">Data issues</th>
                    <th scope="col">Not reviewed</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const k = keyOf(a)
                return (
                  <tr key={k}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Compare ${nameOf(a, level)}`}
                        checked={picked.includes(k)}
                        disabled={!picked.includes(k) && picked.length >= 4}
                        onChange={(e) => setPicked(e.target.checked ? [...picked, k] : picked.filter((x) => x !== k))}
                      />
                    </td>
                    <th scope="row">
                      {level !== 'ward' && (level === 'state' ? a.state : a.lga) ? (
                        <button type="button" className="link" onClick={() => drill(a)}>
                          {nameOf(a, level)}
                        </button>
                      ) : (
                        nameOf(a, level)
                      )}
                      {a.in_reference_list === false && <span className="badge small"> not in reference list</span>}
                    </th>
                    <td>
                      {a.units.toLocaleString()}
                      <span className="muted small"> ({a.turnout.units.toLocaleString()} counted)</span>
                    </td>
                    <td>{pct(a.turnout.rate)}</td>
                    <td>{a.vote_share.leading_party ? `${a.vote_share.leading_party} ${pct(a.vote_share.leading_share)}` : '—'}</td>
                    {a.findings && (
                      <>
                        <td>{a.findings.unusual_turnout}</td>
                        <td>{a.findings.unusual_share}</td>
                        <td>{a.findings.very_high}</td>
                        <td>{a.findings.data_quality}</td>
                        <td>{a.findings.unreviewed}</td>
                      </>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="small muted">Tick up to four areas to compare them side by side. "Counted" units are those the analysis could use for turnout.</p>

          {compared.length >= 2 && (
            <>
              <h2>Comparison</h2>
              <table className="table compact" aria-label="Area comparison">
                <thead>
                  <tr>
                    <th scope="col">Measure</th>
                    {compared.map((a) => (
                      <th key={keyOf(a)} scope="col">
                        {nameOf(a, level)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <Row label="Polling units" cells={compared.map((a) => a.units.toLocaleString())} />
                  <Row label="Registered (counted units)" cells={compared.map((a) => a.turnout.registered_voters.toLocaleString())} />
                  <Row label="Accredited (counted units)" cells={compared.map((a) => a.turnout.accredited_voters.toLocaleString())} />
                  <Row label="Turnout" cells={compared.map((a) => pct(a.turnout.rate))} />
                  <Row label="Valid votes (counted units)" cells={compared.map((a) => a.vote_share.valid_votes.toLocaleString())} />
                  {parties.map((p) => (
                    <Row
                      key={p}
                      label={`${p} share`}
                      cells={compared.map((a) => {
                        const n = a.vote_share.party_totals[p]
                        return n == null || !a.vote_share.valid_votes ? '—' : `${((n / a.vote_share.valid_votes) * 100).toFixed(1)}%`
                      })}
                    />
                  ))}
                  {data.analytical_run_id && <Row label="Unusual units" cells={compared.map((a) => String(flags(a)))} />}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  )
}

function Row({ label, cells }: { label: string; cells: string[] }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      {cells.map((c, i) => (
        <td key={i}>{c}</td>
      ))}
    </tr>
  )
}

/** Horizontal bars, one series: thin bars from a single baseline, hover tooltip, no legend (the title names it). */
function BarChart({ title, note, rows, level, value, format }: { title: string; note: string; rows: Area[]; level: Level; value: (a: Area) => number | null; format: (v: number) => string }) {
  const [hover, setHover] = useState<number | null>(null)
  const shown = rows.slice(0, 30)
  const max = Math.max(0, ...shown.map((a) => value(a) ?? 0)) || 1
  const rowH = 22
  const labelW = 150
  const width = 520
  const plotW = width - labelW - 56
  const height = shown.length * rowH + 8
  return (
    <figure className="chart">
      <figcaption>
        <strong>{title}</strong> <span className="muted small">{note}</span>
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`${title} by area`} onMouseLeave={() => setHover(null)}>
        <line x1={labelW} x2={labelW} y1={0} y2={height} stroke="#d9dcdf" strokeWidth={1} />
        {shown.map((a, i) => {
          const v = value(a)
          const w = v == null ? 0 : Math.max(v > 0 ? 2 : 0, (v / max) * plotW)
          const y = i * rowH + 4
          const name = nameOf(a, level)
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              <rect x={0} y={y - 2} width={width} height={rowH} fill={hover === i ? '#f3f4f5' : 'transparent'} />
              <text x={labelW - 8} y={y + 12} textAnchor="end" fontSize={12} fill="#596371">
                {name.length > 22 ? `${name.slice(0, 21)}…` : name}
              </text>
              {w > 0 && <path d={roundedBar(labelW, y + 3, w, 12)} fill={BAR} />}
              {(hover === i || v === max || v == null) && (
                <text x={labelW + w + 6} y={y + 12} fontSize={11.5} fill="#1d232b">
                  {v == null ? 'n/a' : format(v)}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {rows.length > shown.length && <p className="small muted">Showing the first {shown.length} of {rows.length} in the current sort order; the table lists all.</p>}
    </figure>
  )
}

function roundedBar(x: number, y: number, w: number, h: number) {
  const r = Math.min(4, w, h / 2)
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`
}
