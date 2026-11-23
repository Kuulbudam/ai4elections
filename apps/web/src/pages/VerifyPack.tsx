import { useState, type ChangeEvent } from 'react'
import { api, ApiError, type Schemas } from '../lib/api'
import { fileToBase64 } from '../lib/packs'

type Result = Schemas['PackCheck']

export default function VerifyPack() {
  const [result, setResult] = useState<Result | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setBusy(true)
    setError(null)
    setResult(null)
    setName(file.name)
    try {
      if (file.size > 20 * 1024 * 1024) throw new Error('That file is over 20 MB, so it is not a pack made here.')
      const zip_base64 = await fileToBase64(file)
      setResult(await api<Result>('POST', '/submission-packs/verify', { zip_base64 }))
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : 'Could not check the file.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="page" aria-labelledby="verify-heading">
      <h1 id="verify-heading">Check a pack</h1>
      <p className="muted">Choose a submission pack (.zip) to see whether it is complete and unchanged since it was made.</p>
      <label className="button file-button">
        {busy ? 'Checking…' : 'Choose a pack'}
        <input type="file" accept=".zip,application/zip" className="visually-hidden" disabled={busy} onChange={(e) => void onFile(e)} />
      </label>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {result && (
        <div className="card" aria-label="Result">
          <h2 className={result.valid ? 'ok-text' : 'alert-text'}>{result.valid ? 'This pack is intact' : 'This pack did not pass'}</h2>
          <p className="small muted">
            {name}
            {result.election && ` · ${result.election}`}
            {result.organisation && ` · ${result.organisation}`}
            {result.generated_at && ` · made ${new Date(result.generated_at).toLocaleString()}`}
          </p>
          <ul className="checks">
            {result.checks.map((c) => (
              <li key={c.id} className={`check ${c.ok ? 'check-ok pass' : 'check-flag fail'}`}>
                <strong>
                  {c.ok ? 'Pass' : 'Fail'}: {c.title}
                </strong>
                <div className="small">{c.detail}</div>
              </li>
            ))}
          </ul>
          {result.counts && (
            <p className="small muted">
              {result.counts.polling_units_with_results} polling units with results, {result.counts.field_reports} field reports, {result.counts.incidents} incidents,{' '}
              {result.counts.escalations} escalations.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
