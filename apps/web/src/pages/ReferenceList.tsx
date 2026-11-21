import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { parseSimpleCsv } from '../lib/csv'
import { friendlyError, getSupabase } from '../lib/supabase'

/** Load the project's reference list of valid state / LGA / ward labels (used by rule V011). */
export default function ReferenceList({ projectId, orgId, canEdit }: { projectId: string; orgId: string; canEdit: boolean }) {
  const { session } = useAuth()
  const [count, setCount] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const { count } = await sb.from('admin_units').select('id', { count: 'exact', head: true }).eq('project_id', projectId)
    setCount(count ?? 0)
  }, [projectId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function upload(file: File) {
    const sb = getSupabase()
    if (!sb || !session) return
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const rows = parseSimpleCsv(await file.text())
      const header = rows[0]?.map((h) => h.trim().toLowerCase()) ?? []
      const [si, li, wi] = ['state', 'lga', 'ward'].map((h) => header.indexOf(h))
      if (si < 0 || li < 0 || wi < 0) throw new Error('The file needs columns called state, lga and ward.')
      const units = rows
        .slice(1)
        .map((r) => ({ state: (r[si] ?? '').trim(), lga: (r[li] ?? '').trim(), ward: (r[wi] ?? '').trim() }))
        .filter((u) => u.state && u.lga && u.ward)
      if (units.length === 0) throw new Error('No rows had all three of state, LGA and ward.')
      if (units.length > 50000) throw new Error('That list is too long (over 50,000 rows).')
      // Insert only units not already present (plain INSERT; no UPDATE privilege needed).
      // The Data API returns at most 1,000 rows per request, so read existing units page by page.
      const existing: { state: string; lga: string; ward: string }[] = []
      for (let from = 0; ; from += 1000) {
        const { data, error: readErr } = await sb.from('admin_units').select('state, lga, ward').eq('project_id', projectId).order('id').range(from, from + 999)
        if (readErr) throw readErr
        existing.push(...(data ?? []))
        if (!data || data.length < 1000) break
      }
      const key = (u: { state: string; lga: string; ward: string }) => `${u.state}\u0000${u.lga}\u0000${u.ward}`
      const have = new Set(existing.map(key))
      const seen = new Set<string>()
      const fresh = units.filter((u) => {
        const k = key(u)
        if (have.has(k) || seen.has(k)) return false
        seen.add(k)
        return true
      })
      for (let i = 0; i < fresh.length; i += 500) {
        const { error } = await sb
          .from('admin_units')
          .insert(fresh.slice(i, i + 500).map((u) => ({ ...u, org_id: orgId, project_id: projectId, created_by: session.user.id })))
        if (error) throw error
      }
      setMessage(`Added ${fresh.length.toLocaleString()} new units (${(units.length - fresh.length).toLocaleString()} already present). Check the data again to use them.`)
      await load()
    } catch (err) {
      setError(err instanceof Error && !('code' in err) ? err.message : friendlyError(err as { message?: string; code?: string }))
    }
    setBusy(false)
  }

  return (
    <div className="card">
      <h4>Official list of states, LGAs and wards</h4>
      <p className="small">
        {count === null ? 'Loading…' : count === 0 ? 'None added yet. Without it, place names in your results can’t be checked for spelling.' : `${count.toLocaleString()} wards on the list.`}
      </p>
      {canEdit && (
        <label>
          Add from a CSV with columns state, lga and ward
          <input type="file" accept=".csv,text/csv" disabled={busy} onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
        </label>
      )}
      {busy && <p className="muted small">Loading…</p>}
      {message && <p className="notice">{message}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </div>
  )
}
