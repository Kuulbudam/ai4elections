import type { ReactNode } from 'react'
import { Wordmark } from './Logo'
import { ThemeToggle } from '../lib/theme'

/** Split layout for sign-in and two-step screens: the product beside the form. */
export default function AuthLayout({ children, labelledBy }: { children: ReactNode; labelledBy?: string }) {
  return (
    <div className="auth-shell">
      <ThemeToggle className="auth-theme" />
      <aside className="auth-aside">
        <Wordmark size={64} />
        <div className="auth-pitch">
          <p className="auth-statement">Every result checked. Every flag explained.</p>
          <p className="auth-sub">Find the polling units that need a second look, and keep the proof.</p>
        </div>
        <span />
      </aside>
      <main className="auth-main">
        <section className="auth-card" aria-labelledby={labelledBy}>
          <div className="auth-card-logo">
            <Wordmark size={44} />
          </div>
          {children}
        </section>
      </main>
    </div>
  )
}
