import { useEffect, useState, type FormEvent } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { COUNT_FIELDS, TEXT_FIELDS, readingLabel, sumParties, type Check, type Figures, type Sheet } from '../lib/sheets'
import { getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'
import { enqueue, flush, locate, newClientId } from '../lib/outbox'

const blank = (): Figures => ({ party_votes: [] })

export default function SheetDetail() {
  const { projectId = '', sheetId = '' } = useParams()
  const [orgId, setOrgId] = useState<string | undefined>()
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const [fig, setFig] = useState<Figures>(blank())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const role = useRole(orgId)
  const [params] = useSearchParams()
  const reportPu = params.get('report')
  const [sentAsResult, setSentAsResult] = useState(false)

  useEffect(() => {
    getSupabase()
      ?.from('projects')
      .select('org_id')
      .eq('id', projectId)
      .maybeSingle()
      .then(({ data }) => setOrgId(data?.org_id))
    api<Sheet>('GET', `/sheets/${sheetId}`)
      .then((s) => {
        setSheet(s)
        setFig(structuredClone(s.confirmed ?? s.reading.figures))
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load this sheet.'))
  }, [projectId, sheetId])

  async function confirm(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const figures: Figures = { ...fig, party_votes: (fig.party_votes ?? []).filter((p) => p.party.trim()) }
      const s = await api<Sheet>('POST', `/sheets/${sheetId}/confirm`, { figures }, { 'Idempotency-Key': crypto.randomUUID() })
      setSheet(s)
      if (reportPu) {
        // Sent from the field app: record it as this polling unit's result (queued if the signal drops).
        enqueue({ client_id: newClientId(), project_id: projectId, kind: 'result', pu_code: reportPu, captured_at: new Date().toISOString(), location: await locate(), payload: { sheet_id: s.id } })
        await flush()
        setSentAsResult(true)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not confirm the sheet. Try again.')
    }
    setBusy(false)
  }

  if (!sheet)
    return (
      <section className="page">
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </section>
    )

  const editable = sheet.status === 'read' && (role === 'admin' || role === 'analyst' || role === 'researcher' || role === 'coordinator' || role === 'field_agent')
  const conf = sheet.reading.confidence ?? {}
  const flag = (key: string, value: unknown) => {
    if (sheet.status !== 'read' || sheet.reading.status !== 'read') return null
    if (value === null || value === undefined || value === '') return <span className="chk chk-missing">Blank: type it in</span>
    const c = conf[key]
    return c && c !== 'high' ? <span className="chk chk-unsure">Check this one</span> : null
  }
  const setCount = (key: (typeof COUNT_FIELDS)[number]['key'], v: string) =>
    setFig((f) => ({ ...f, [key]: v.trim() === '' ? null : Math.max(0, Math.floor(Number(v))) }))
  const parties = fig.party_votes ?? []
  const partySum = sumParties(fig)

  return (
    <section className="page" aria-labelledby="sheet-heading">
      <p>
        <Link to={`/projects/${projectId}/sheets`}>← Result sheets</Link>
      </p>
      <h1 id="sheet-heading">Result sheet {(sheet.confirmed ?? sheet.reading.figures).pu_code ?? ''}</h1>
      <p className="page-intro">
        {sheet.status === 'read'
          ? 'Check each figure against the photo and fix anything that’s wrong. Nothing is compared until you confirm.'
          : `Confirmed ${sheet.confirmed_at ? new Date(sheet.confirmed_at).toLocaleString() : ''}.`}
      </p>

      <div className="sheet-layout">
        <figure className="sheet-photo">
          {sheet.image_url ? (
            <a href={sheet.image_url} target="_blank" rel="noreferrer">
              <img src={sheet.image_url} alt="Photo of the polling-unit result sheet" />
            </a>
          ) : (
            <p className="muted">The photo has timed out. Refresh the page to see it again.</p>
          )}
          <figcaption className="small muted">
            {readingLabel(sheet.reading.status)}
            {sheet.model ? ` (${sheet.model})` : ''}. Photo SHA-256 <code>{sheet.image_sha256.slice(0, 16)}…</code>
          </figcaption>
          {sheet.reading.notes && <p className="notice warn small">Note from the reader: {sheet.reading.notes}</p>}
        </figure>

        <div className="sheet-side">
          {sheet.status === 'confirmed' ? (
            <>
            {sentAsResult && (
              <p className="notice" role="status">
                Sent as the result for {reportPu}. <Link to={`/projects/${projectId}/field`}>Back to field reports</Link>
              </p>
            )}
            {sheet.escalation_id && (
              <p className="notice warn">
                The sheet doesn’t add up, so your coordinator has been alerted. <Link to={`/escalations/${sheet.escalation_id}`}>See the escalation</Link>
              </p>
            )}
            <Results sheet={sheet} projectId={projectId} />
            </>
          ) : editable ? (
            <form className="form sheet-form" aria-label="Confirm the sheet's figures" onSubmit={(e) => void confirm(e)}>
              <fieldset>
                <legend>Where</legend>
                {TEXT_FIELDS.map((f) => (
                  <label key={f.key}>
                    <span className="row-between">
                      {f.label} {flag(f.key, fig[f.key])}
                    </span>
                    <input value={fig[f.key] ?? ''} maxLength={f.key === 'polling_unit_name' ? 200 : 120} onChange={(e) => setFig((x) => ({ ...x, [f.key]: e.target.value || null }))} />
                  </label>
                ))}
              </fieldset>
              <fieldset>
                <legend>Figures</legend>
                {COUNT_FIELDS.map((f) => (
                  <label key={f.key}>
                    <span className="row-between">
                      {f.label} {flag(f.key, fig[f.key])}
                    </span>
                    <input type="number" inputMode="numeric" min={0} value={fig[f.key] ?? ''} onChange={(e) => setCount(f.key, e.target.value)} />
                  </label>
                ))}
              </fieldset>
              <fieldset>
                <legend>Votes by party</legend>
                {parties.map((p, i) => (
                  <div className="party-row" key={i}>
                    <input aria-label={`Party ${i + 1}`} value={p.party} maxLength={20} onChange={(e) => setFig((x) => ({ ...x, party_votes: parties.map((q, j) => (j === i ? { ...q, party: e.target.value } : q)) }))} />
                    <input
                      aria-label={`${p.party || `Party ${i + 1}`} votes`}
                      type="number"
                      inputMode="numeric"
                      min={0}
                      value={Number.isFinite(p.votes) ? p.votes : ''}
                      onChange={(e) => setFig((x) => ({ ...x, party_votes: parties.map((q, j) => (j === i ? { ...q, votes: Math.max(0, Math.floor(Number(e.target.value) || 0)) } : q)) }))}
                    />
                    {flag(`party:${p.party}`, p.votes)}
                    <button type="button" className="link-button" aria-label={`Remove ${p.party || 'this party'}`} onClick={() => setFig((x) => ({ ...x, party_votes: parties.filter((_, j) => j !== i) }))}>
                      Remove
                    </button>
                  </div>
                ))}
                <button type="button" onClick={() => setFig((x) => ({ ...x, party_votes: [...parties, { party: '', votes: 0 }] }))}>
                  Add a party
                </button>
                <p className={`small ${fig.valid_votes != null && partySum !== fig.valid_votes ? 'warn-text' : 'muted'}`} aria-live="polite">
                  Party votes add up to {partySum.toLocaleString()}
                  {fig.valid_votes != null ? `. Total valid votes: ${fig.valid_votes.toLocaleString()}.` : '.'}
                </p>
              </fieldset>
              {error && (
                <p role="alert" className="error">
                  {error}
                </p>
              )}
              <button type="submit" className="primary" disabled={busy}>
                {busy ? 'Checking…' : 'Confirm and check'}
              </button>
              <p className="small muted">Only confirm what you can see on the photo. Once confirmed, the figures can’t be changed.</p>
            </form>
          ) : (
            <p className="empty">Waiting for a team member to confirm the figures.</p>
          )}
        </div>
      </div>
    </section>
  )
}

function mark(c: Check) {
  return c.ok === true ? (
    <span className="chk chk-ok">✓ OK</span>
  ) : c.ok === false ? (
    <span className="chk chk-flag">✗ Problem</span>
  ) : (
    <span className="chk chk-missing">– Not checked</span>
  )
}

function Results({ sheet, projectId }: { sheet: Sheet; projectId: string }) {
  const cmp = sheet.comparison
  return (
    <div className="sheet-results">
      <h2>Checks</h2>
      <ul className="checks" aria-label="Checks on this sheet">
        {(sheet.checks ?? []).map((c) => (
          <li key={c.id} className={`check check-${c.ok === true ? 'ok' : c.ok === false ? 'flag' : 'missing'}`}>
            <span className="row-between">
              <strong>{c.title}</strong> {mark(c)}
            </span>
            <span className="small">{c.detail}</span>
          </li>
        ))}
      </ul>
      {sheet.finding_ids.length > 0 && (
        <p className="notice warn">
          {sheet.finding_ids.length} finding{sheet.finding_ids.length > 1 ? 's were' : ' was'} added to the review queue:{' '}
          {sheet.finding_ids.map((id, i) => (
            <span key={id}>
              {i > 0 && ', '}
              <Link to={`/findings/${id}`}>finding {i + 1}</Link>
            </span>
          ))}
          . A mismatch is a reason to look again at the sheet and the data, not a conclusion.
        </p>
      )}
      <h2>Sheet vs uploaded results</h2>
      {cmp && cmp.matched ? (
        <>
          <p className="small muted">
            Polling unit {cmp.pu_code}, source row {cmp.source_row_number} of the uploaded file.
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Figure</th>
                  <th scope="col">Sheet</th>
                  <th scope="col">Uploaded</th>
                  <th scope="col">Match</th>
                </tr>
              </thead>
              <tbody>
                {cmp.rows.map((r) => (
                  <tr key={r.field} className={r.match === false ? 'row-flag' : undefined}>
                    <td>{r.label}</td>
                    <td>{r.sheet ?? '–'}</td>
                    <td>{r.data ?? '–'}</td>
                    <td>{r.match === true ? 'Yes' : r.match === false ? <strong>No</strong> : '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="empty">Polling unit {cmp?.pu_code ?? '(no code)'} isn’t in your uploaded results, so only the sheet’s own sums were checked.</p>
      )}
      <p className="small">
        <Link to={`/projects/${projectId}/findings`}>See all findings</Link>
      </p>
    </div>
  )
}
