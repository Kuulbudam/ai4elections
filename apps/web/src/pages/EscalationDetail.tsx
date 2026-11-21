import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { areaText, EVENT, type Escalation } from '../lib/escalations'
import { LEVEL } from '../lib/findings'

export default function EscalationDetail() {
  const { escalationId = '' } = useParams()
  const [e, setE] = useState<Escalation | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setE(await api<Escalation>('GET', `/escalations/${escalationId}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Couldn’t load this escalation.')
      setE(null)
    }
  }, [escalationId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  if (e === undefined) return <p className="muted">Loading…</p>
  if (e === null) return <p className="empty">{error ?? 'Not found.'}</p>
  const source = e.source_kind === 'finding' ? `/findings/${e.source_id}` : `/projects/${e.project_id}/field`
  return (
    <section className="page" aria-labelledby="escd-heading">
      <p>
        <Link to={`/projects/${e.project_id}/escalations`}>← Escalations</Link>
      </p>
      <h1 id="escd-heading">{e.title}</h1>
      <p>
        <span className={`badge ${e.status === 'open' ? 'badge-unverified' : 'badge-verified'}`}>{e.status === 'open' ? `Open · with ${LEVEL[e.current_level]}` : 'Resolved'}</span>{' '}
        {areaText(e.area) && <span className="small muted">{areaText(e.area)}</span>}
      </p>
      <p>
        <Link to={source}>{e.source_kind === 'finding' ? 'Open the finding' : 'Open the field report'}</Link>
      </p>

      <ol className="history" aria-label="History">
        {e.events.map((x) => (
          <li key={x.id}>
            <strong>{EVENT[x.kind]}</strong>
            {x.kind !== 'resolved' && <span className="small muted"> to {LEVEL[x.level]}</span>} {x.note}
            <div className="small muted">
              {x.actor_name ?? 'A member'}, {new Date(x.created_at).toLocaleString()}
            </div>
          </li>
        ))}
      </ol>

      {e.can_resolve && <Act escalationId={e.id} kind="resolve" onDone={load} />}
      {e.can_send_up && <Act escalationId={e.id} kind="send-up" onDone={load} />}
      {e.status === 'open' && !e.can_resolve && <p className="muted small">Waiting for {LEVEL[e.current_level].toLowerCase()} to act.</p>}
    </section>
  )
}

function Act({ escalationId, kind, onDone }: { escalationId: string; kind: 'resolve' | 'send-up'; onDone: () => Promise<void> }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const resolve = kind === 'resolve'
  async function submit(ev: FormEvent) {
    ev.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api('POST', `/escalations/${escalationId}/${kind}`, { note }, { 'Idempotency-Key': crypto.randomUUID() })
      setNote('')
      await onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That didn’t save. Try again.')
    }
    setBusy(false)
  }
  return (
    <form className="form card" aria-label={resolve ? 'Resolve' : 'Send up'} onSubmit={(ev) => void submit(ev)}>
      <label>
        {resolve ? 'How was it resolved?' : 'Why does it need the next level?'}
        <textarea value={note} onChange={(x) => setNote(x.target.value)} minLength={5} maxLength={4000} rows={3} required />
      </label>
      <button type="submit" className={resolve ? 'primary' : ''} disabled={busy || note.trim().length < 5}>
        {busy ? 'Saving…' : resolve ? 'Resolve' : 'Send up'}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  )
}
