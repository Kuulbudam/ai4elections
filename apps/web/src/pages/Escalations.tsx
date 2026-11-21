import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { areaText, STATUS, type Escalation } from '../lib/escalations'
import { LEVEL } from '../lib/findings'

export default function Escalations() {
  const { projectId = '' } = useParams()
  const [status, setStatus] = useState<'open' | 'resolved'>('open')
  const [items, setItems] = useState<Escalation[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<{ items: Escalation[] }>('GET', `/escalations?project_id=${projectId}&status=${status}`)
      .then((r) => live && setItems(r.items))
      .catch((err) => live && setError(err instanceof ApiError ? err.message : 'Couldn’t load escalations.'))
    return () => {
      live = false
    }
  }, [projectId, status])

  return (
    <section className="page" aria-labelledby="esc-heading">
      <h1 id="esc-heading">Escalations</h1>
      <div className="seg" role="radiogroup" aria-label="Show">
        {(['open', 'resolved'] as const).map((s) => (
          <button key={s} type="button" role="radio" aria-checked={status === s} className={status === s ? 'on' : ''} onClick={() => { setItems(null); setStatus(s) }}>
            {STATUS[s]}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {items === null ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="empty">{status === 'open' ? 'Nothing waiting.' : 'Nothing resolved yet.'}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Where</th>
                <th scope="col">{status === 'open' ? 'With' : 'Resolved'}</th>
                <th scope="col">Raised</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/escalations/${e.id}`}>{e.title}</Link>
                    {e.can_resolve && <span className="badge badge-unverified">your turn</span>}
                  </td>
                  <td className="small">{areaText(e.area) || '—'}</td>
                  <td className="small">{status === 'open' ? LEVEL[e.current_level] : e.resolved_at ? new Date(e.resolved_at).toLocaleString() : ''}</td>
                  <td className="small">
                    {e.raised_by_name ?? 'A member'}, {new Date(e.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
