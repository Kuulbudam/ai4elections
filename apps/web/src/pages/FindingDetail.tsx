import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { api, ApiError } from '../lib/api'
import { ClaimCard, ClaimForm, type ClaimRow, type EvidenceRow } from '../components/Claims'
import { CATEGORY, DECISION, INDICATOR, SEVERITY, pct, type Finding } from '../lib/findings'
import { friendlyError, getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type ReviewRow = { id: string; decision: string; reason: string; reviewer_id: string; created_at: string }
type NoteRow = { id: string; body: string; author_id: string; created_at: string }
type AuditRow = { id: string; seq: number; action: string; actor_id: string | null; created_at: string; entity_type: string }
const ACTION: Record<string, string> = {
  'finding_reviews.insert': 'Review decision recorded',
  'investigation_notes.insert': 'Note added',
  'claims.insert': 'Allegation recorded',
  'claims.update': 'Claim verified',
  'claim_evidence.insert': 'Evidence attached',
}

export default function FindingDetail() {
  const { findingId = '' } = useParams()
  const { session } = useAuth()
  const [f, setF] = useState<Finding | null | undefined>(undefined)
  const [orgId, setOrgId] = useState<string | undefined>()
  const [reviews, setReviews] = useState<ReviewRow[]>([])
  const [notes, setNotes] = useState<NoteRow[]>([])
  const [claims, setClaims] = useState<ClaimRow[]>([])
  const [evidence, setEvidence] = useState<EvidenceRow[]>([])
  const [audit, setAudit] = useState<AuditRow[]>([])
  const [people, setPeople] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const role = useRole(orgId)
  const me = session?.user.id

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    let finding: Finding
    try {
      finding = await api<Finding>('GET', `/findings/${findingId}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return setF(null)
      setError(err instanceof ApiError ? err.message : 'Could not load this finding.')
      return setF(null)
    }
    setF(finding)
    const [p, r, n, c] = await Promise.all([
      sb.from('projects').select('org_id').eq('id', finding.project_id).maybeSingle(),
      sb.from('finding_reviews').select('id, decision, reason, reviewer_id, created_at').eq('finding_id', findingId).order('created_at', { ascending: false }),
      sb.from('investigation_notes').select('id, body, author_id, created_at').eq('finding_id', findingId).order('created_at'),
      sb.from('claims').select('id, statement, status, created_by, created_at, verified_by, verified_at, verification_reason').eq('finding_id', findingId).order('created_at'),
    ])
    const err = r.error ?? n.error ?? c.error
    if (err) setError(friendlyError(err))
    setOrgId(p.data?.org_id)
    const rv = (r.data as ReviewRow[]) ?? []
    const nt = (n.data as NoteRow[]) ?? []
    const cl = (c.data as ClaimRow[]) ?? []
    setReviews(rv)
    setNotes(nt)
    setClaims(cl)
    const ev = cl.length ? (((await sb.from('claim_evidence').select('id, claim_id, kind, description, url, file_sha256, added_by, created_at').in('claim_id', cl.map((x) => x.id)).order('created_at')).data as EvidenceRow[]) ?? []) : []
    setEvidence(ev)
    const ids = [findingId, ...rv.map((x) => x.id), ...nt.map((x) => x.id), ...cl.map((x) => x.id), ...ev.map((x) => x.id)]
    const a = await sb.from('audit_events').select('id, seq, action, actor_id, created_at, entity_type').in('entity_id', ids).order('seq')
    setAudit((a.data as AuditRow[]) ?? [])
    const users = [...new Set([...rv.map((x) => x.reviewer_id), ...nt.map((x) => x.author_id), ...cl.flatMap((x) => [x.created_by, x.verified_by ?? '']), ...ev.map((x) => x.added_by), ...((a.data as AuditRow[]) ?? []).map((x) => x.actor_id ?? '')])].filter(Boolean)
    if (users.length) {
      const pr = await sb.from('profiles').select('id, email, display_name').in('id', users)
      setPeople(Object.fromEntries((pr.data ?? []).map((u: { id: string; email: string; display_name: string | null }) => [u.id, u.display_name || u.email])))
    }
  }, [findingId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  if (f === undefined) return <p className="muted">Loading…</p>
  if (f === null)
    return (
      <p className="empty" role="alert">
        {error ?? 'Finding not found, or you are not a member of its organisation.'}
      </p>
    )

  const who = (id: string | null | undefined) => (id ? (people[id] ?? 'a member') : 'system')
  const canReview = role === 'admin' || role === 'analyst'
  const canContribute = canReview || role === 'researcher'
  const ev = f.evidence[0]
  const flagged = new Set(ev?.flagged_columns ?? [])

  return (
    <section className="page" aria-labelledby="finding-heading">
      <p>
        <Link to={`/projects/${f.project_id}/findings`}>← Findings</Link>
      </p>
      <p className="small muted">
        {CATEGORY[f.category]}
        {f.indicator ? ` · ${INDICATOR[f.indicator]}` : ''}
        {f.rule_id ? ` · rule ${f.rule_id}` : ''}
      </p>
      <h1 id="finding-heading">{f.title}</h1>
      <p>
        <span className={`sev sev-${f.severity}`}>{f.category === 'statistical_anomaly' ? 'How unusual: ' : 'Severity: '}{SEVERITY[f.severity]}</span>{' '}
        <span className="badge">Confidence: {f.confidence}</span> <span className="badge">Review: {DECISION[f.review_status]}</span>
      </p>
      <p className="page-intro">{f.explanation}</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {f.statistics && f.peer_group && (
        <div className="card">
          <h2>Comparison with peers</h2>
          <dl className="facts">
            <dt>This unit</dt>
            <dd>{pct(f.statistics.value)}{f.statistics.leading_party ? ` (${f.statistics.leading_party})` : ''}</dd>
            <dt>Peer median</dt>
            <dd>{pct(f.statistics.median)}</dd>
            <dt>Spread (MAD)</dt>
            <dd>{pct(f.statistics.mad)}</dd>
            <dt>Robust z</dt>
            <dd>
              {Number(f.statistics.robust_z) > 0 ? '+' : ''}
              {Number(f.statistics.robust_z).toFixed(2)} (listed at ±{f.statistics.threshold})
            </dd>
            <dt>Peer group</dt>
            <dd>
              {f.peer_group.sample_size} polling units in the same {f.peer_group.level === 'ward' ? 'ward' : 'LGA (the ward was too small)'}: {f.peer_group.key.split('|').join(' / ')}
            </dd>
          </dl>
          {f.exclusions && f.exclusions.length > 0 && (
            <details>
              <summary>{f.exclusions.length} units in this area were left out of the comparison</summary>
              <ul className="small">
                {f.exclusions.map((e) => (
                  <li key={e.raw_row_id}>
                    {e.pu_code ?? 'unit'}: {e.reason.replaceAll('_', ' ')}
                    {e.rules?.length ? ` (${e.rules.join(', ')})` : ''}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      <div className="card">
        <h2>Evidence: source row {ev?.source_row_number}</h2>
        {ev ? (
          <>
            <p className="small muted">The row exactly as uploaded{flagged.size ? '; the cells the check flagged are marked' : ''}.</p>
            <table className="table compact" aria-label="Original cells">
              <tbody>
                {Object.entries(ev.cells).map(([col, val]) => {
                  const isLink = /^https?:\/\//i.test(val)
                  return (
                    <tr key={col} className={flagged.has(col) ? 'flagged' : undefined}>
                      <th scope="row">{col}{flagged.has(col) && <span className="sr-only"> (flagged)</span>}</th>
                      <td>
                        {val.trim() === '' ? <span className="muted">(blank)</span> : isLink ? <a href={val} target="_blank" rel="noreferrer noopener">{val}</a> : <code>{val}</code>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </>
        ) : (
          <p className="empty">No source row is attached to this finding.</p>
        )}
      </div>

      <div className="card">
        <h2>Limitations</h2>
        <p>{f.limitations}</p>
        <p className="small muted">
          Dataset SHA-256 <code>{f.provenance.dataset_sha256.slice(0, 16)}…</code> · engine {f.provenance.engine_version}
          {f.provenance.method_version ? ` · method ${f.provenance.method_version}` : ''}
          {f.provenance.rule_version ? ` · rules ${f.provenance.rule_version}` : ''}
        </p>
      </div>

      <h2>Review</h2>
      {canReview && <ReviewForm findingId={f.id} onDone={load} />}
      {reviews.length === 0 ? (
        <p className="empty">No review decision yet.</p>
      ) : (
        <ol className="history" aria-label="Review history">
          {reviews.map((r, i) => (
            <li key={r.id}>
              <strong>{DECISION[r.decision]}</strong>
              {i === 0 && <span className="badge">current</span>} — {r.reason}
              <div className="small muted">
                {who(r.reviewer_id)}, {new Date(r.created_at).toLocaleString()}
              </div>
            </li>
          ))}
        </ol>
      )}
      <p className="small muted">Decisions are never edited or deleted. A new decision is added on top; the latest one is the current status.</p>

      <h2>Investigation notes</h2>
      {notes.length === 0 ? <p className="empty">No notes yet.</p> : (
        <ul className="history">
          {notes.map((n) => (
            <li key={n.id}>
              {n.body}
              <div className="small muted">
                {who(n.author_id)}, {new Date(n.created_at).toLocaleString()}
              </div>
            </li>
          ))}
        </ul>
      )}
      {canContribute && orgId && <NoteForm orgId={orgId} findingId={f.id} me={me!} onDone={load} />}

      <h2>Claims about this unit</h2>
      <p className="small muted">A claim is an allegation until another admin or analyst verifies it against independent evidence.</p>
      {claims.length === 0 ? <p className="empty">No claims recorded.</p> : (
        claims.map((c) => (
          <ClaimCard key={c.id} c={c} items={evidence.filter((e) => e.claim_id === c.id)} who={who} canReview={canReview} canContribute={canContribute} me={me!} orgId={orgId!} onDone={load} />
        ))
      )}
      {canContribute && <ClaimForm projectId={f.project_id} findingId={f.id} onDone={load} />}

      <h2>Audit history</h2>
      {audit.length === 0 ? <p className="empty">No recorded activity on this finding yet.</p> : (
        <table className="table compact" aria-label="Audit history">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">What</th>
              <th scope="col">Who</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {audit.map((a) => (
              <tr key={a.id}>
                <td>{a.seq}</td>
                <td>{ACTION[a.action] ?? a.action}</td>
                <td>{who(a.actor_id)}</td>
                <td>{new Date(a.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="small muted">Every event is hash-chained to the one before it, so a removed or altered event is detectable.</p>
    </section>
  )
}

function ReviewForm({ findingId, onDone }: { findingId: string; onDone: () => Promise<void> }) {
  const [decision, setDecision] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api('POST', `/findings/${findingId}/reviews`, { decision, reason }, { 'Idempotency-Key': crypto.randomUUID() })
      setDecision('')
      setReason('')
      await onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the decision.')
    }
    setBusy(false)
  }
  return (
    <form className="form card" aria-label="Record a review decision" onSubmit={(e) => void submit(e)}>
      <label>
        Decision
        <select value={decision} onChange={(e) => setDecision(e.target.value)} required>
          <option value="" disabled>
            Choose…
          </option>
          {Object.entries(DECISION).filter(([k]) => k !== 'unreviewed').map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label>
        Reason (required)
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} minLength={10} maxLength={4000} required rows={3} placeholder="What did you check, and what did you find?" />
      </label>
      <button type="submit" className="primary" disabled={busy || !decision || reason.trim().length < 10}>
        {busy ? 'Saving…' : 'Record decision'}
      </button>
      {error && <p role="alert" className="error">{error}</p>}
    </form>
  )
}

function NoteForm({ orgId, findingId, me, onDone }: { orgId: string; findingId: string; me: string; onDone: () => Promise<void> }) {
  const [body, setBody] = useState('')
  const [error, setError] = useState<string | null>(null)
  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const { error: err } = await getSupabase()!.from('investigation_notes').insert({ org_id: orgId, finding_id: findingId, body: body.trim(), author_id: me })
    if (err) return setError(friendlyError(err))
    setBody('')
    await onDone()
  }
  return (
    <form className="form" aria-label="Add a note" onSubmit={(e) => void submit(e)}>
      <label>
        Add a note
        <textarea value={body} onChange={(e) => setBody(e.target.value)} maxLength={4000} rows={2} required />
      </label>
      <button type="submit" disabled={!body.trim()}>
        Add note
      </button>
      {error && <p role="alert" className="error">{error}</p>}
    </form>
  )
}

