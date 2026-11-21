import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { friendlyError, getSupabase } from '../lib/supabase'

type Note = { id: string; kind: string; title: string; body: string; link: string; created_at: string; read_at: string | null }

export default function Inbox() {
  const [items, setItems] = useState<Note[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const { data, error: err } = await sb.from('notifications').select('id, kind, title, body, link, created_at, read_at').order('created_at', { ascending: false }).limit(100)
    if (err) setError(friendlyError(err))
    setItems((data as Note[]) ?? [])
  }, [])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function open(n: Note) {
    if (!n.read_at) await getSupabase()!.from('notifications').update({ read_at: new Date().toISOString() }).eq('id', n.id)
    navigate(n.link)
  }

  async function readAll() {
    await getSupabase()!.from('notifications').update({ read_at: new Date().toISOString() }).is('read_at', null)
    await load()
  }

  const unread = items?.filter((n) => !n.read_at).length ?? 0
  return (
    <section className="page" aria-labelledby="inbox-heading">
      <div className="row-between">
        <h1 id="inbox-heading">Inbox</h1>
        {unread > 0 && (
          <button type="button" onClick={() => void readAll()}>
            Mark all as read
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {items === null ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="empty">Nothing here yet.</p>
      ) : (
        <ul className="inbox" aria-label="Notifications">
          {items.map((n) => (
            <li key={n.id} className={n.read_at ? '' : 'unread'}>
              <button type="button" className="inbox-item" onClick={() => void open(n)}>
                <span className="inbox-title">{n.title}</span>
                {n.body && <span className="inbox-body">{n.body}</span>}
                <span className="small muted">{new Date(n.created_at).toLocaleString()}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
