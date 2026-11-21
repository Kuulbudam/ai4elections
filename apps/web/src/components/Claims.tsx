import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { CLAIM_STATUS, EVIDENCE_KIND, sha256File, type Claim } from '../lib/findings'
import { friendlyError, getSupabase } from '../lib/supabase'

export type ClaimRow = { id: string; statement: string; status: string; created_by: string; created_at: string; verified_by: string | null; verified_at: string | null; verification_reason: string | null; finding_id?: string | null }
export type EvidenceRow = { id: string; claim_id: string; kind: string; description: string; url: string | null; file_sha256: string | null; added_by: string; created_at: string }
type EvidenceDraft = { kind: 'link' | 'document_reference' | 'file'; description: string; url: string; file_sha256: string }

const BLANK_EVIDENCE: EvidenceDraft = { kind: 'link', description: '', url: '', file_sha256: '' }

export function EvidenceFields({ value, onChange }: { value: EvidenceDraft; onChange: (v: EvidenceDraft) => void }) {
  const [hashing, setHashing] = useState(false)
  return (
    <div className="grid2">
      <label>
        Evidence type
        <select value={value.kind} onChange={(e) => onChange({ ...BLANK_EVIDENCE, kind: e.target.value as EvidenceDraft['kind'] })}>
          {Object.entries(EVIDENCE_KIND).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label>
        Description
        <input value={value.description} maxLength={2000} onChange={(e) => onChange({ ...value, description: e.target.value })} placeholder="e.g. Observer group statement, 26 Feb" />
      </label>
      {value.kind === 'link' && (
        <label>
          Link
          <input type="url" value={value.url} onChange={(e) => onChange({ ...value, url: e.target.value })} placeholder="https://" />
        </label>
      )}
      {value.kind === 'file' && (
        <label>
          File (stays on your device; only its SHA-256 fingerprint is recorded)
          <input
            type="file"
            onChange={async (e) => {
              const file = e.target.files?.[0]
              if (!file) return
              setHashing(true)
              const h = await sha256File(file)
              setHashing(false)
              onChange({ ...value, file_sha256: h, description: value.description || file.name })
            }}
          />
          {hashing ? <span className="small muted">Computing fingerprint…</span> : value.file_sha256 && <code className="small">{value.file_sha256}</code>}
        </label>
      )}
    </div>
  )
}

const evidenceReady = (e: EvidenceDraft) => e.description.trim() && (e.kind !== 'link' || /^https?:\/\//i.test(e.url)) && (e.kind !== 'file' || e.file_sha256)
const evidencePayload = (e: EvidenceDraft) => ({ kind: e.kind, description: e.description.trim(), url: e.kind === 'link' ? e.url.trim() : null, file_sha256: e.kind === 'file' ? e.file_sha256 : null })

export function ClaimForm({ projectId, findingId, onDone }: { projectId: string; findingId: string | null; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [statement, setStatement] = useState('')
  const [ev, setEv] = useState<EvidenceDraft>(BLANK_EVIDENCE)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)}>
        Add a claim
      </button>
    )
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api<Claim>('POST', '/claims', { project_id: projectId, finding_id: findingId, statement: statement.trim(), evidence: evidenceReady(ev) ? [evidencePayload(ev)] : [] }, { 'Idempotency-Key': crypto.randomUUID() })
      setStatement('')
      setEv(BLANK_EVIDENCE)
      setOpen(false)
      await onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Couldn’t save the claim. Try again.')
    }
    setBusy(false)
  }
  return (
    <form className="form card" aria-label="Add a claim" onSubmit={(e) => void submit(e)}>
      <label>
        What was reported?
        <textarea value={statement} onChange={(e) => setStatement(e.target.value)} minLength={10} maxLength={4000} rows={3} required />
      </label>
      <p className="small muted">Evidence (you can add this later, but it’s needed before the claim can be verified)</p>
      <EvidenceFields value={ev} onChange={setEv} />
      <button type="submit" className="primary" disabled={busy || statement.trim().length < 10}>
        {busy ? 'Saving…' : 'Save claim'}
      </button>
      {error && <p role="alert" className="error">{error}</p>}
    </form>
  )
}

export function ClaimCard({ c, items, who, canReview, canContribute, me, orgId, onDone }: { c: ClaimRow; items: EvidenceRow[]; who: (id: string | null) => string; canReview: boolean; canContribute: boolean; me: string; orgId: string; onDone: () => Promise<void> }) {
  const [ev, setEv] = useState<EvidenceDraft>(BLANK_EVIDENCE)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const verified = c.status === 'verified_fact'

  async function addEvidence(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const { error: err } = await getSupabase()!.from('claim_evidence').insert({ org_id: orgId, claim_id: c.id, added_by: me, ...evidencePayload(ev) })
    if (err) return setError(friendlyError(err))
    setEv(BLANK_EVIDENCE)
    await onDone()
  }
  async function verify(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await api('POST', `/claims/${c.id}/verify`, { reason: reason.trim() }, { 'Idempotency-Key': crypto.randomUUID() })
      await onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Couldn’t verify the claim. Try again.')
    }
  }

  return (
    <article className={`card claim ${verified ? 'claim-verified' : 'claim-unverified'}`} aria-label={CLAIM_STATUS[c.status]}>
      <p>
        <span className={`badge ${verified ? 'badge-verified' : 'badge-unverified'}`}>{CLAIM_STATUS[c.status]}</span>
      </p>
      <p>{c.statement}</p>
      <p className="small muted">
        Recorded by {who(c.created_by)}, {new Date(c.created_at).toLocaleString()}
          {c.finding_id && (
          <>
            {' · '}
            <Link to={`/findings/${c.finding_id}`}>open the finding</Link>
          </>
        )}
        {verified && ` · verified by ${who(c.verified_by)}, ${new Date(c.verified_at!).toLocaleString()}: ${c.verification_reason}`}
      </p>
      {items.length > 0 ? (
        <ul className="small">
          {items.map((e) => (
            <li key={e.id}>
              {EVIDENCE_KIND[e.kind]}: {e.description}
              {e.url && (
                <>
                  {' '}
                  <a href={e.url} target="_blank" rel="noreferrer noopener">
                    open
                  </a>
                </>
              )}
              {e.file_sha256 && <code> {e.file_sha256.slice(0, 16)}…</code>} <span className="muted">({who(e.added_by)})</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small muted">No evidence yet.</p>
      )}
      {canContribute && (
        <form className="form" aria-label="Add evidence" onSubmit={(e) => void addEvidence(e)}>
          <EvidenceFields value={ev} onChange={setEv} />
          <button type="submit" disabled={!evidenceReady(ev)}>
            Add evidence
          </button>
        </form>
      )}
      {canReview && !verified && c.created_by !== me && (
        <form className="form" aria-label="Verify claim" onSubmit={(e) => void verify(e)}>
          <label>
            Why is this now a verified fact?
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} minLength={10} />
          </label>
          <button type="submit" disabled={items.length === 0 || reason.trim().length < 10}>
            Mark as verified
          </button>
          {items.length === 0 && <span className="small muted"> Add evidence first.</span>}
        </form>
      )}
      {canReview && !verified && c.created_by === me && <p className="small muted">You added this claim, so someone else has to verify it.</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </article>
  )
}
