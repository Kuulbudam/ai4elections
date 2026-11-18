import type { ReactNode } from 'react'

/** Split layout for sign-in and two-step screens: a statement of purpose beside the form. */
export default function AuthLayout({ children, labelledBy }: { children: ReactNode; labelledBy?: string }) {
  return (
    <div className="auth-shell">
      <aside className="auth-aside">
        <p className="brand">AI4Elections</p>
        <div>
          <p className="auth-statement">Evidence before conclusions.</p>
          <p>
            A workbench for checking polling-unit results: preserve the original, validate every row, and review unusual
            patterns with the source sheet in hand.
          </p>
        </div>
        <p className="small">For authorised analysts and researchers. Findings are signals for review, not verdicts.</p>
      </aside>
      <main className="auth-main">
        <section className="auth-card" aria-labelledby={labelledBy}>
          {children}
        </section>
      </main>
    </div>
  )
}
