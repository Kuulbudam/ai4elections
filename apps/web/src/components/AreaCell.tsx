import { useState, type FormEvent } from 'react'
import { friendlyError, getSupabase, type Role } from '../lib/supabase'

import { areaLabel, LEVELS, type Assignment } from '../lib/areas'

/** A coordinator's state / LGA / ward, or a field agent's polling units; admins add and remove them. */
export default function AreaCell({ orgId, me, member, areas, canEdit, onChange }: {
  orgId: string
  me: string
  member: { user_id: string; role: Role; email: string | null }
  areas: Assignment[]
  canEdit: boolean
  onChange: () => Promise<void>
}) {
  const coordinator = member.role === 'coordinator'
  const agent = member.role === 'field_agent'
  const [open, setOpen] = useState(false)
  const [level, setLevel] = useState<Assignment['level']>(agent ? 'polling_unit' : 'ward')
  const [f, setF] = useState({ state: '', lga: '', ward: '', pu_code: '' })
  const [error, setError] = useState<string | null>(null)
  if (!coordinator && !agent) return <span className="muted small">—</span>

  const need = { lga: level !== 'state', ward: level === 'ward' || level === 'polling_unit', pu_code: level === 'polling_unit' }
  async function add(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const row = {
      org_id: orgId, user_id: member.user_id, level, created_by: me, state: f.state.trim(),
      lga: need.lga ? f.lga.trim() : null, ward: need.ward ? f.ward.trim() : null, pu_code: need.pu_code ? f.pu_code.trim() : null,
    }  // prettier-ignore
    const { error: err } = await getSupabase()!.from('area_assignments').insert(row)
    if (err) return setError(friendlyError(err))
    setF({ state: f.state, lga: f.lga, ward: f.ward, pu_code: '' })
    await onChange()
  }
  async function remove(id: string) {
    const { error: err } = await getSupabase()!.from('area_assignments').delete().eq('id', id)
    if (err) setError(friendlyError(err))
    await onChange()
  }

  return (
    <div className="areas">
      {areas.length === 0 ? (
        <span className="muted small">{agent ? 'No polling unit yet' : 'No area yet'}</span>
      ) : (
        <ul className="area-list">
          {areas.map((a) => (
            <li key={a.id}>
              <span className="small">
                {LEVELS[a.level]}: {areaLabel(a)}
              </span>
              {canEdit && (
                <button type="button" className="link small" aria-label={`Remove ${areaLabel(a)}`} onClick={() => void remove(a.id)}>
                  remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit && !open && (
        <button type="button" className="link small" onClick={() => setOpen(true)}>
          {agent ? 'Add a polling unit' : 'Add an area'}
        </button>
      )}
      {canEdit && open && (
        <form className="area-form" aria-label={`Add an area for ${member.email ?? 'this member'}`} onSubmit={(e) => void add(e)}>
          {coordinator && (
            <label>
              Level
              <select value={level} onChange={(e) => setLevel(e.target.value as Assignment['level'])}>
                <option value="state">State</option>
                <option value="lga">LGA</option>
                <option value="ward">Ward</option>
              </select>
            </label>
          )}
          <label>
            State
            <input required value={f.state} maxLength={120} onChange={(e) => setF({ ...f, state: e.target.value })} />
          </label>
          {need.lga && (
            <label>
              LGA
              <input required value={f.lga} maxLength={120} onChange={(e) => setF({ ...f, lga: e.target.value })} />
            </label>
          )}
          {need.ward && (
            <label>
              Ward
              <input required value={f.ward} maxLength={120} onChange={(e) => setF({ ...f, ward: e.target.value })} />
            </label>
          )}
          {need.pu_code && (
            <label>
              Polling unit code
              <input required value={f.pu_code} maxLength={60} onChange={(e) => setF({ ...f, pu_code: e.target.value })} />
            </label>
          )}
          <span className="area-actions">
            <button type="submit" className="primary">
              Add
            </button>
            <button type="button" onClick={() => setOpen(false)}>
              Done
            </button>
          </span>
          {error && (
            <p role="alert" className="error small">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  )
}
