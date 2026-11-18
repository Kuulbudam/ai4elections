import { useEffect, useState } from 'react'
import type { components } from './api/schema'

type Health = components['schemas']['Health']
type ApiState =
  | { kind: 'not_configured' }
  | { kind: 'checking' }
  | { kind: 'ok'; health: Health }
  | { kind: 'unreachable' }

function apiBaseUrl(): string | undefined {
  const value = import.meta.env.VITE_API_BASE_URL as string | undefined
  return value && value.trim() ? value.trim().replace(/\/$/, '') : undefined
}

export default function App() {
  const base = apiBaseUrl()
  const [api, setApi] = useState<ApiState>(base ? { kind: 'checking' } : { kind: 'not_configured' })

  useEffect(() => {
    if (!base) return
    const ctrl = new AbortController()
    fetch(`${base}/health`, { signal: ctrl.signal })
      .then(async (r) => (r.ok ? setApi({ kind: 'ok', health: (await r.json()) as Health }) : setApi({ kind: 'unreachable' })))
      .catch(() => {
        if (!ctrl.signal.aborted) setApi({ kind: 'unreachable' })
      })
    return () => ctrl.abort()
  }, [base])

  return (
    <main className="shell">
      <header>
        <h1>AI4Elections</h1>
        <p className="tagline">Electoral data validation, anomaly signals and evidence management</p>
      </header>

      <section aria-labelledby="status-heading" className="card">
        <h2 id="status-heading">System status</h2>
        <p data-testid="api-status">
          {api.kind === 'not_configured' && 'API address not configured for this deployment.'}
          {api.kind === 'checking' && 'Checking API…'}
          {api.kind === 'ok' && `API online (version ${api.health.version}).`}
          {api.kind === 'unreachable' && 'API unreachable. Try again shortly.'}
        </p>
        <p className="muted">Foundation build (Phase 0). Sign-in and projects arrive in Phase 1.</p>
      </section>

      <footer className="muted">
        Findings in this platform are signals for human review, not verdicts. All data shown during development is
        synthetic.
      </footer>
    </main>
  )
}
