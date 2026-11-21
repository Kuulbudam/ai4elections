import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, type Schemas } from '../lib/api'
import { discard, enqueue, flush, locate, newClientId, pending, type Kind, type Pending } from '../lib/outbox'
import { photoToBase64 } from '../lib/sheets'
import { useRole } from '../lib/useRole'
import { CATEGORY, KIND_LABEL } from '../lib/field'
import { getSupabase } from '../lib/supabase'

type Report = Schemas['FieldReport']
type Unit = Schemas['FieldAssignment']

export default function Field() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState<{ org_id: string; election_name: string } | null>(null)
  const role = useRole(project?.org_id)
  const [units, setUnits] = useState<Unit[] | null>(null)
  const [pu, setPu] = useState('')
  const [kind, setKind] = useState<Kind | null>(null)
  const [sent, setSent] = useState<Report[]>([])
  const [waiting, setWaiting] = useState<Pending[]>(pending())
  const [online, setOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    void getSupabase()
      ?.from('projects')
      .select('org_id, election_name')
      .eq('id', projectId)
      .maybeSingle()
      .then(({ data }) => live && setProject(data ?? null))
    api<{ items: Unit[] }>('GET', `/field/assignments?project_id=${projectId}`)
      .then((r) => {
        if (!live) return
        setUnits(r.items)
        if (r.items.length === 1) setPu(r.items[0].pu_code)
      })
      .catch(() => live && setUnits([]))
    return () => {
      live = false
    }
  }, [projectId])

  const refresh = useCallback(async () => {
    const out = await flush()
    setWaiting(pending())
    if (out.sent) setNotice(`${out.sent} report${out.sent > 1 ? 's' : ''} sent.`)
    try {
      const r = await api<{ items: Report[] }>('GET', `/field-reports?project_id=${projectId}`)
      setSent(r.items)
    } catch {
      /* offline: show what is on the phone */
    }
  }, [projectId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void refresh()
    const up = () => {
      setOnline(true)
      void refresh()
    }
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    const t = setInterval(() => void refresh(), 20_000)
    return () => {
      window.removeEventListener('online', up)
      window.removeEventListener('offline', down)
      clearInterval(t)
    }
  }, [refresh])

  async function submit(payload: Record<string, unknown>) {
    if (!kind || !pu) return
    setBusy(true)
    setError(null)
    const item: Pending = { client_id: newClientId(), project_id: projectId, kind, pu_code: pu, captured_at: new Date().toISOString(), location: await locate(), payload }
    enqueue(item)
    setKind(null)
    setNotice(navigator.onLine ? 'Sending…' : 'Saved on this phone. It will be sent when you have signal.')
    await refresh()
    setBusy(false)
  }

  async function onPhoto(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || !pu) return
    setBusy(true)
    setError(null)
    try {
      const image_base64 = await photoToBase64(file)
      const s = await api<Schemas['ResultSheet']>('POST', '/sheets', { project_id: projectId, image_base64 }, { 'Idempotency-Key': crypto.randomUUID() })
      navigate(`/projects/${projectId}/sheets/${s.id}?report=${encodeURIComponent(pu)}`)
    } catch (err) {
      setError(err instanceof ApiError && err.status !== 0 ? err.message : 'The photo needs signal to upload. Try again when you’re online.')
      setBusy(false)
    }
  }

  const canType = role === 'admin' || role === 'coordinator'
  return (
    <section className="page field" aria-labelledby="field-heading">
      <h1 id="field-heading">Field reports</h1>
      <p className={`net ${online ? 'net-on' : 'net-off'}`} role="status">
        {online ? 'Online' : 'No signal. Reports are saved on this phone and sent when you’re back online.'}
        {waiting.filter((w) => !w.error).length > 0 && ` · ${waiting.filter((w) => !w.error).length} waiting to send`}
      </p>

      {units === null ? (
        <p className="muted">Loading…</p>
      ) : units.length === 0 && !canType ? (
        <p className="empty">You haven’t been given a polling unit yet. Ask your coordinator or admin.</p>
      ) : (
        <label className="pu-pick">
          Polling unit
          {units.length > 0 ? (
            <select value={pu} onChange={(e) => setPu(e.target.value)}>
              <option value="" disabled>
                Choose…
              </option>
              {units.map((u) => (
                <option key={u.pu_code} value={u.pu_code}>
                  {u.pu_code} · {u.ward}
                </option>
              ))}
            </select>
          ) : (
            <input value={pu} onChange={(e) => setPu(e.target.value)} placeholder="e.g. 31/05/13/130" maxLength={60} />
          )}
        </label>
      )}

      {pu && !kind && (
        <div className="tiles" role="group" aria-label="What are you reporting?">
          {(Object.keys(KIND_LABEL) as Kind[]).map((k) =>
            k === 'result' ? (
              <label key={k} className={`tile${busy ? ' busy' : ''}`}>
                <span>{busy ? 'Uploading…' : KIND_LABEL[k]}</span>
                <input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={busy} onChange={(e) => void onPhoto(e)} />
              </label>
            ) : (
              <button key={k} type="button" className={`tile${k === 'incident' ? ' tile-alert' : ''}`} onClick={() => setKind(k)}>
                {KIND_LABEL[k]}
              </button>
            ),
          )}
        </div>
      )}

      {kind === 'arrival' && <ArrivalForm busy={busy} onCancel={() => setKind(null)} onSubmit={submit} />}
      {kind === 'accreditation' && <AccreditationForm busy={busy} onCancel={() => setKind(null)} onSubmit={submit} />}
      {kind === 'incident' && <IncidentForm busy={busy} onCancel={() => setKind(null)} onSubmit={submit} />}
      {kind === 'closing' && <ClosingForm busy={busy} onCancel={() => setKind(null)} onSubmit={submit} />}

      {notice && <p className="notice">{notice}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {waiting.some((w) => w.error) && (
        <div className="card">
          <h2>Not accepted</h2>
          {waiting
            .filter((w) => w.error)
            .map((w) => (
              <p key={w.client_id} className="small">
                {KIND_LABEL[w.kind]} at {w.pu_code}: {w.error}{' '}
                <button type="button" className="link" onClick={() => { discard(w.client_id); setWaiting(pending()) }}>
                  Remove
                </button>
              </p>
            ))}
        </div>
      )}

      <h2>Sent</h2>
      {sent.length === 0 ? (
        <p className="empty">Nothing sent yet.</p>
      ) : (
        <ul className="sent" aria-label="Sent reports">
          {sent.slice(0, 30).map((r) => (
            <li key={r.id}>
              <strong>{KIND_LABEL[r.kind]}</strong> · {r.pu_code}
              {r.kind === 'incident' && <span className={`sev sev-${r.severity === 'critical' ? 'very_high' : 'medium'}`}>{r.severity}</span>}
              {r.escalation_id && <span className="small muted"> · sent to your coordinator</span>}
              <div className="small muted">{new Date(r.captured_at).toLocaleTimeString()}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function YesNo({ label, value, onChange }: { label: string; value: boolean | null; onChange: (v: boolean) => void }) {
  return (
    <fieldset className="yesno">
      <legend>{label}</legend>
      {[true, false].map((v) => (
        <label key={String(v)} className={value === v ? 'on' : ''}>
          <input type="radio" name={label} checked={value === v} onChange={() => onChange(v)} required />
          {v ? 'Yes' : 'No'}
        </label>
      ))}
    </fieldset>
  )
}

function Shell({ title, busy, ready, onCancel, onSubmit, children }: { title: string; busy: boolean; ready: boolean; onCancel: () => void; onSubmit: () => void; children: ReactNode }) {
  return (
    <form
      className="form card field-form"
      aria-label={title}
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      <h2>{title}</h2>
      {children}
      <div className="row-actions">
        <button type="submit" className="primary" disabled={busy || !ready}>
          Send
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

type FormProps = { busy: boolean; onCancel: () => void; onSubmit: (p: Record<string, unknown>) => void }
const num = (v: string) => (v.trim() === '' ? null : Math.max(0, Math.floor(Number(v))))

function ArrivalForm({ busy, onCancel, onSubmit }: FormProps) {
  const [opened, setOpened] = useState<boolean | null>(null)
  const [at, setAt] = useState('')
  const [officials, setOfficials] = useState('')
  const [bvas, setBvas] = useState<boolean | null>(null)
  const [materials, setMaterials] = useState<boolean | null>(null)
  const [security, setSecurity] = useState<boolean | null>(null)
  const ready = opened !== null && bvas !== null && materials !== null && security !== null && officials !== ''
  return (
    <Shell title="Polls opened" busy={busy} ready={ready} onCancel={onCancel}
      onSubmit={() => onSubmit({ opened, opened_at: at || null, officials_present: num(officials), bvas_working: bvas, materials_complete: materials, security_present: security })}>
      <YesNo label="Has voting started?" value={opened} onChange={setOpened} />
      <label>
        Time it started
        <input type="time" value={at} onChange={(e) => setAt(e.target.value)} />
      </label>
      <label>
        Polling officials present
        <input type="number" inputMode="numeric" min={0} max={50} value={officials} onChange={(e) => setOfficials(e.target.value)} required />
      </label>
      <YesNo label="Is the BVAS working?" value={bvas} onChange={setBvas} />
      <YesNo label="Are all materials there?" value={materials} onChange={setMaterials} />
      <YesNo label="Is security present?" value={security} onChange={setSecurity} />
    </Shell>
  )
}

function AccreditationForm({ busy, onCancel, onSubmit }: FormProps) {
  const [count, setCount] = useState('')
  const [bvas, setBvas] = useState<boolean | null>(null)
  const [queue, setQueue] = useState('')
  return (
    <Shell title="Accreditation count" busy={busy} ready={count !== '' && bvas !== null} onCancel={onCancel}
      onSubmit={() => onSubmit({ accredited_on_bvas: num(count), bvas_working: bvas, voters_waiting: num(queue) })}>
      <label>
        Number accredited, as shown on the BVAS
        <input type="number" inputMode="numeric" min={0} value={count} onChange={(e) => setCount(e.target.value)} required />
      </label>
      <YesNo label="Is the BVAS working?" value={bvas} onChange={setBvas} />
      <label>
        People still waiting (optional)
        <input type="number" inputMode="numeric" min={0} value={queue} onChange={(e) => setQueue(e.target.value)} />
      </label>
    </Shell>
  )
}

function IncidentForm({ busy, onCancel, onSubmit }: FormProps) {
  const [category, setCategory] = useState('')
  const [severity, setSeverity] = useState('')
  const [text, setText] = useState('')
  const [ongoing, setOngoing] = useState<boolean | null>(null)
  return (
    <Shell title="Incident" busy={busy} ready={!!category && !!severity && text.trim().length >= 5 && ongoing !== null} onCancel={onCancel}
      onSubmit={() => onSubmit({ category, severity, description: text.trim(), ongoing })}>
      <label>
        What kind?
        <select value={category} onChange={(e) => setCategory(e.target.value)} required>
          <option value="" disabled>
            Choose…
          </option>
          {Object.entries(CATEGORY).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label>
        How serious?
        <select value={severity} onChange={(e) => setSeverity(e.target.value)} required>
          <option value="" disabled>
            Choose…
          </option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="critical">Critical: alert my coordinator now</option>
        </select>
      </label>
      <label>
        What happened?
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={2000} required />
      </label>
      <YesNo label="Is it still happening?" value={ongoing} onChange={setOngoing} />
    </Shell>
  )
}

function ClosingForm({ busy, onCancel, onSubmit }: FormProps) {
  const [counting, setCounting] = useState<boolean | null>(null)
  const [posted, setPosted] = useState<boolean | null>(null)
  const [upload, setUpload] = useState<boolean | null>(null)
  const [signed, setSigned] = useState<boolean | null>(null)
  return (
    <Shell title="Closing" busy={busy} ready={[counting, posted, upload, signed].every((x) => x !== null)} onCancel={onCancel}
      onSubmit={() => onSubmit({ counting_done: counting, result_posted_publicly: posted, bvas_upload_seen: upload, agents_signed: signed })}>
      <YesNo label="Is counting finished?" value={counting} onChange={setCounting} />
      <YesNo label="Was the result posted where people can see it?" value={posted} onChange={setPosted} />
      <YesNo label="Did you see the result uploaded with the BVAS?" value={upload} onChange={setUpload} />
      <YesNo label="Did the party agents sign the result sheet?" value={signed} onChange={setSigned} />
    </Shell>
  )
}
