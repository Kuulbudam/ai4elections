import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Schemas } from '../lib/api'
import { useRole } from '../lib/useRole'
import { ReadAloud } from './Voice'

type Summary = Schemas['FindingsSummary']
type Lang = Schemas['AnswerLanguage']
const LANGS: { code: Lang; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'ha', label: 'Hausa' },
  { code: 'yo', label: 'Yoruba' },
  { code: 'ig', label: 'Igbo' },
  { code: 'pcm', label: 'Nigerian Pidgin' },
]
const KEY = 'ai4e.answerLanguage'
function saved(): Lang {
  try {
    const v = localStorage.getItem(KEY)
    return LANGS.some((l) => l.code === v) ? (v as Lang) : 'en'
  } catch {
    return 'en'
  }
}

/** The AI summary at the end of the Findings page. The counts come from the server; the AI only words them. */
export default function FindingsSummary({ projectId, orgId }: { projectId: string; orgId: string | undefined }) {
  const role = useRole(orgId)
  const [language, setLanguage] = useState<Lang>(saved)
  const [s, setS] = useState<Summary | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const canWrite = role === 'admin' || role === 'analyst' || role === 'researcher' || role === 'coordinator'

  const load = useCallback(async () => {
    try {
      setS(await api<Summary>('GET', `/projects/${projectId}/findings-summary?language=${language}`))
    } catch (err) {
      setS(err instanceof ApiError && err.status === 404 ? null : undefined)
      if (!(err instanceof ApiError && err.status === 404)) setError(err instanceof ApiError ? err.message : 'Could not load the summary.')
    }
  }, [projectId, language])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function writeNew() {
    setBusy(true)
    setError(null)
    try {
      setS(await api<Summary>('POST', `/projects/${projectId}/findings-summary`, { language }, { 'Idempotency-Key': crypto.randomUUID() }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not write the summary. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card summary-card" aria-labelledby="summary-heading">
      <div className="row-between">
        <h2 id="summary-heading">Summary</h2>
        <label className="inline-label small">
          In
          <select
            value={language}
            onChange={(e) => {
              const v = e.target.value as Lang
              setLanguage(v)
              try {
                localStorage.setItem(KEY, v)
              } catch {
                /* private browsing */
              }
            }}
          >
            {LANGS.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {s === undefined && !error && <p className="muted">Loading…</p>}
      {s === null && <p className="empty">No summary yet.{canWrite ? ' Write one from the findings above.' : ''}</p>}
      {s && (
        <>
          <p className="summary-text">{s.summary}</p>
          <p className="small muted">
            Written by AI from the counts on this page, {new Date(s.created_at).toLocaleString()}
            {s.created_by_name ? ` for ${s.created_by_name}` : ''}.{' '}
            {s.grounded ? 'Every number was checked against the findings.' : 'Some numbers could not be checked. Use the findings list, not this text.'}
            {s.language !== 'en' && ' Translations can contain mistakes.'}
          </p>
          {s.stale && <p className="notice warn">The findings have changed since this was written.</p>}
          <ReadAloud text={s.summary} language={s.language} />
        </>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {canWrite && (
        <div className="row-actions">
          <button type="button" className={s ? '' : 'primary'} disabled={busy} onClick={() => void writeNew()}>
            {busy ? 'Writing…' : s ? 'Write again' : 'Write summary'}
          </button>
        </div>
      )}
    </section>
  )
}
