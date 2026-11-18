import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { getSupabase } from '../lib/supabase'

function codeError(message: string | undefined): string {
  const m = (message ?? '').toLowerCase()
  if (m.includes('invalid') || m.includes('code')) return 'That code is not correct or has expired. Enter the current 6-digit code.'
  return 'Verification failed. Please try again.'
}

function CodeForm({ onSubmit, busy, error }: { onSubmit: (code: string) => void; busy: boolean; error: string | null }) {
  const [code, setCode] = useState('')
  return (
    <form
      className="form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        onSubmit(code.replace(/\s/g, ''))
      }}
    >
      <label>
        6-digit code
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          required
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
        />
      </label>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button type="submit" className="primary" disabled={busy || code.length !== 6}>
        {busy ? 'Verifying…' : 'Verify'}
      </button>
    </form>
  )
}

/** Shown after password sign-in when the user already has an authenticator app set up. */
export function TwoStepChallenge() {
  const { refreshAal } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function verify(code: string) {
    const sb = getSupabase()!
    setBusy(true)
    setError(null)
    const { data: factors } = await sb.auth.mfa.listFactors()
    const factor = factors?.totp.find((f) => f.status === 'verified')
    if (!factor) {
      setError('No authenticator is set up for this account.')
      setBusy(false)
      return
    }
    const { error } = await sb.auth.mfa.challengeAndVerify({ factorId: factor.id, code })
    if (error) setError(codeError(error.message))
    else await refreshAal()
    setBusy(false)
  }

  return (
    <TwoStepShell title="Two-step verification" intro="Open your authenticator app and enter the 6-digit code for AI4Elections.">
      <CodeForm onSubmit={(c) => void verify(c)} busy={busy} error={error} />
    </TwoStepShell>
  )
}

/** Shown after password sign-in when the user has not set up an authenticator app yet (required). */
export function TwoStepEnrol() {
  const { refreshAal } = useAuth()
  const [factor, setFactor] = useState<{ id: string; qr: string; secret: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    const sb = getSupabase()!
    void (async () => {
      // Remove abandoned, never-verified attempts so a fresh QR code can be issued.
      const { data: existing } = await sb.auth.mfa.listFactors()
      for (const f of existing?.all ?? []) {
        if (f.factor_type === 'totp' && f.status !== 'verified') await sb.auth.mfa.unenroll({ factorId: f.id })
      }
      const { data, error } = await sb.auth.mfa.enroll({ factorType: 'totp', friendlyName: `Authenticator ${new Date().toISOString().slice(0, 16)}` })
      if (error || !data) setError('Could not start set-up. Sign out and try again.')
      else setFactor({ id: data.id, qr: data.totp.qr_code, secret: data.totp.secret })
    })()
  }, [])

  async function verify(code: string) {
    if (!factor) return
    setBusy(true)
    setError(null)
    const { error } = await getSupabase()!.auth.mfa.challengeAndVerify({ factorId: factor.id, code })
    if (error) setError(codeError(error.message))
    else await refreshAal()
    setBusy(false)
  }

  return (
    <TwoStepShell
      title="Set up two-step verification"
      intro="Two-step verification is required for every account. Scan this QR code with an authenticator app (for example Google Authenticator or Microsoft Authenticator), then enter the 6-digit code it shows."
    >
      {factor ? (
        <>
          <img className="qr" src={factor.qr} alt="QR code for your authenticator app" width={180} height={180} />
          <details className="small">
            <summary>Can’t scan? Enter this key instead</summary>
            <code className="secret">{factor.secret}</code>
          </details>
          <CodeForm onSubmit={(c) => void verify(c)} busy={busy} error={error} />
        </>
      ) : error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : (
        <p className="muted">Preparing…</p>
      )}
    </TwoStepShell>
  )
}

function TwoStepShell({ title, intro, children }: { title: string; intro: string; children: React.ReactNode }) {
  return (
    <main className="auth-shell">
      <section className="card auth-card" aria-labelledby="twostep-heading">
        <p className="brand">AI4Elections</p>
        <h1 id="twostep-heading">{title}</h1>
        <p className="small">{intro}</p>
        {children}
        <button type="button" className="link" onClick={() => void getSupabase()!.auth.signOut()}>
          Sign out
        </button>
      </section>
    </main>
  )
}
