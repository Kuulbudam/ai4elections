import { useState, type ReactNode } from 'react'
import { C, SEV_LABEL } from './chartTheme'

/* Chart set for the analysis dashboard. Method: dataviz skill (validated reference palette).
   - One series per chart in the series blue; "unusual" is shown as emphasis, not a new hue.
   - How unusual uses the reserved status steps (warning / serious / critical), always with a text label.
   - Thin marks, rounded data ends, hairline grid, hover tooltips; a table elsewhere on the page carries the detail. */


export function Kpi({ label, value, note, tone }: { label: string; value: number | string; note?: string; tone?: 'alert' | 'ok' }) {
  return (
    <div className={`kpi ${tone ?? ''}`} role="listitem">
      <div className="kpi-value">{typeof value === 'number' ? value.toLocaleString() : value}</div>
      <div className="kpi-label">{label}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  )
}

export function ChartCard({ title, subtitle, children, action }: { title: string; subtitle?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <figure className="chart-card">
      <figcaption>
        <span className="chart-title">{title}</span>
        {subtitle && <span className="chart-sub">{subtitle}</span>}
        {action}
      </figcaption>
      {children}
    </figure>
  )
}

type Tip = { x: number; y: number; lines: string[] } | null
function Tooltip({ tip, width }: { tip: Tip; width: number }) {
  if (!tip) return null
  const w = Math.max(...tip.lines.map((l) => l.length)) * 6.6 + 16
  const h = tip.lines.length * 16 + 10
  const x = Math.min(Math.max(tip.x - w / 2, 2), width - w - 2)
  const y = tip.y - h - 10 < 0 ? tip.y + 14 : tip.y - h - 10
  return (
    <g pointerEvents="none">
      <rect x={x} y={y} width={w} height={h} rx={3} fill="#fff" stroke={C.axis} />
      {tip.lines.map((l, i) => (
        <text key={i} x={x + 8} y={y + 17 + i * 16} fontSize={12} fill={i === 0 ? C.ink : C.ink2} fontWeight={i === 0 ? 600 : 400}>
          {l}
        </text>
      ))}
    </g>
  )
}

const pct = (v: number, d = 0) => `${(v * 100).toFixed(d)}%`

/** Distribution of a 0–1 rate across polling units, with the unusual units' values marked on a rug below the axis. */
export function Histogram({ values, marks, label }: { values: number[]; marks: { value: number; severity: string }[]; label: string }) {
  const [tip, setTip] = useState<Tip>(null)
  const W = 420, H = 210, L = 38, R = 10, T = 10, B = 46 // prettier-ignore
  const bins = 40
  const counts = new Array(bins).fill(0)
  for (const v of values) counts[Math.min(bins - 1, Math.max(0, Math.floor(v * bins)))]++
  const max = Math.max(1, ...counts)
  const pw = (W - L - R) / bins
  const y = (c: number) => T + (H - T - B) * (1 - c / max)
  const ticks = [0, 0.25, 0.5, 0.75, 1]
  const yt = niceTicks(max)
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      role="img"
      aria-label={`${label}: distribution across ${values.length.toLocaleString()} polling units`}
      onMouseLeave={() => setTip(null)}
    >
      {yt.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke={C.grid} />
          <text x={L - 6} y={y(t) + 4} fontSize={11} textAnchor="end" fill={C.ink2}>
            {t.toLocaleString()}
          </text>
        </g>
      ))}
      {counts.map((c, i) => {
        const x = L + i * pw + 1
        const top = y(c)
        return (
          <g
            key={i}
            onMouseEnter={() =>
              setTip({ x: x + pw / 2, y: top, lines: [`${pct(i / bins, 1)}–${pct((i + 1) / bins, 1)}`, `${c.toLocaleString()} polling units`] })
            }
          >
            <rect x={x - 1} y={T} width={pw} height={H - T - B} fill="transparent" />
            {c > 0 && <path d={roundTop(x, top, pw - 2, H - B - top)} fill={C.series} />}
          </g>
        )
      })}
      <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke={C.axis} />
      {ticks.map((t) => (
        <text key={t} x={L + t * (W - L - R)} y={H - B + 14} fontSize={11} textAnchor="middle" fill={C.ink2}>
          {pct(t)}
        </text>
      ))}
      {marks.map((m, i) => (
        <line
          key={i}
          x1={L + m.value * (W - L - R)}
          x2={L + m.value * (W - L - R)}
          y1={H - B + 22}
          y2={H - B + 34}
          stroke={C.sev[m.severity] ?? C.ink2}
          strokeWidth={2}
        >
          <title>{`Unusual unit at ${pct(m.value, 1)} (${SEV_LABEL[m.severity] ?? m.severity})`}</title>
        </line>
      ))}
      <text x={L} y={H - 2} fontSize={11} fill={C.ink2}>
        Marks below the axis: unusual units, coloured by how unusual
      </text>
      <Tooltip tip={tip} width={W} />
    </svg>
  )
}

export type Point = { id: string; x: number; y: number; severity: string; label: string; detail: string }

/** Each unusual unit's value against its peer group's median. Points far from the diagonal differ most from their peers. */
export function Scatter({ points, onPick, xLabel, yLabel }: { points: Point[]; onPick: (id: string) => void; xLabel: string; yLabel: string }) {
  const [tip, setTip] = useState<Tip>(null)
  const W = 420, H = 280, L = 44, R = 12, T = 10, B = 40 // prettier-ignore
  const sx = (v: number) => L + v * (W - L - R)
  const sy = (v: number) => H - B - v * (H - T - B)
  const ticks = [0, 0.25, 0.5, 0.75, 1]
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      role="group"
      aria-label={`${yLabel} against ${xLabel} for ${points.length} unusual units`}
      onMouseLeave={() => setTip(null)}
    >
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} stroke={C.grid} />
          <line x1={sx(t)} x2={sx(t)} y1={T} y2={H - B} stroke={C.grid} />
          <text x={L - 6} y={sy(t) + 4} fontSize={11} textAnchor="end" fill={C.ink2}>
            {pct(t)}
          </text>
          <text x={sx(t)} y={H - B + 14} fontSize={11} textAnchor="middle" fill={C.ink2}>
            {pct(t)}
          </text>
        </g>
      ))}
      <line x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)} stroke={C.axis} />
      <text x={sx(0.98)} y={sy(0.98) + 14} fontSize={10.5} textAnchor="end" fill={C.ink2}>
        same as peers
      </text>
      <text x={(L + W - R) / 2} y={H - 4} fontSize={11.5} textAnchor="middle" fill={C.ink2}>
        {xLabel}
      </text>
      <text x={12} y={(T + H - B) / 2} fontSize={11.5} textAnchor="middle" fill={C.ink2} transform={`rotate(-90 12 ${(T + H - B) / 2})`}>
        {yLabel}
      </text>
      {points.map((p) => (
        <circle
          key={p.id}
          cx={sx(p.x)}
          cy={sy(p.y)}
          r={5}
          fill={C.sev[p.severity] ?? C.series}
          stroke="#fff"
          strokeWidth={2}
          style={{ cursor: 'pointer' }}
          tabIndex={0}
          role="link"
          aria-label={`${p.label}: ${p.detail}`}
          onMouseEnter={() => setTip({ x: sx(p.x), y: sy(p.y), lines: [p.label, p.detail, `${SEV_LABEL[p.severity] ?? p.severity} · click to open`] })}
          onClick={() => onPick(p.id)}
          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onPick(p.id)}
        />
      ))}
      <Tooltip tip={tip} width={W} />
    </svg>
  )
}

/** Ranked horizontal bars, one hue; the selected bar is emphasised and others recede. */
export function RankedBars({
  rows,
  selected,
  onPick,
  unit,
}: {
  rows: { key: string; label: string; value: number }[]
  selected?: string
  onPick?: (key: string) => void
  unit: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 420, rowH = 24, L = 150, R = 36 // prettier-ignore
  const H = rows.length * rowH + 6
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role={onPick ? 'group' : 'img'} aria-label={`Ranked by ${unit}`} onMouseLeave={() => setHover(null)}>
      {rows.map((r, i) => {
        const y = i * rowH + 4
        const w = (r.value / max) * (W - L - R)
        const dim = selected && selected !== r.key
        return (
          <g
            key={r.key}
            onMouseEnter={() => setHover(i)}
            onClick={() => onPick?.(r.key)}
            style={{ cursor: onPick ? 'pointer' : 'default' }}
            role={onPick ? 'button' : undefined}
            tabIndex={onPick ? 0 : undefined}
            aria-label={onPick ? `${r.label}: ${r.value} ${unit}. Filter the dashboard to this area` : undefined}
            onKeyDown={(e) => onPick && (e.key === 'Enter' || e.key === ' ') && onPick(r.key)}
          >
            <rect x={0} y={y - 2} width={W} height={rowH} fill={hover === i ? '#f3f5f7' : 'transparent'} />
            <text x={L - 8} y={y + 13} fontSize={12} textAnchor="end" fill={dim ? C.ink2 : C.ink} fontWeight={selected === r.key ? 700 : 400}>
              {r.label.length > 23 ? `${r.label.slice(0, 22)}…` : r.label}
            </text>
            {w > 0 && <path d={roundRight(L, y + 3, Math.max(2, w), 13)} fill={dim ? C.muted : C.series} />}
            <text x={L + w + 6} y={y + 13} fontSize={11.5} fill={C.ink}>
              {r.value.toLocaleString()}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

/** One whole split into a few ordered parts (how unusual), with labels on the blocks and a legend. */
export function SegmentedBar({ parts, label }: { parts: { key: string; value: number }[]; label: string }) {
  const total = parts.reduce((a, b) => a + b.value, 0)
  if (!total) return <p className="muted small">No unusual units.</p>
  return (
    <div className="segbar">
      <div className="segbar-track" role="img" aria-label={`${label}: ${parts.map((p) => `${SEV_LABEL[p.key]} ${p.value}`).join(', ')}`}>
        {parts.map((p) => {
          const w = (p.value / total) * 100
          return p.value > 0 ? (
            <span key={p.key} style={{ width: `${w}%`, background: C.sev[p.key], color: p.key === 'medium' ? '#1d232b' : '#fff' }} title={`${SEV_LABEL[p.key]}: ${p.value}`}>
              {w > 9 ? p.value : ''}
            </span>
          ) : null
        })}
      </div>
      <ul className="legend">
        {parts.map((p) => (
          <li key={p.key}>
            <i style={{ background: C.sev[p.key] }} /> {SEV_LABEL[p.key]} <strong>{p.value}</strong>
          </li>
        ))}
      </ul>
    </div>
  )
}

function roundTop(x: number, y: number, w: number, h: number) {
  const r = Math.min(3, w / 2, h)
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`
}
function roundRight(x: number, y: number, w: number, h: number) {
  const r = Math.min(4, w, h / 2)
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`
}
function niceTicks(max: number) {
  const step = Math.pow(10, Math.floor(Math.log10(max)))
  const s = max / step > 5 ? step * 2 : max / step > 2 ? step : step / 2
  const out = []
  for (let t = 0; t <= max; t += Math.max(1, Math.round(s))) out.push(t)
  return out
}
