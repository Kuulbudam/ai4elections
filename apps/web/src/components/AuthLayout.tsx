import type { ReactNode } from 'react'
import { Wordmark } from './Logo'
import { ThemeToggle } from '../lib/theme'

/** Split layout for sign-in and two-step screens: the product beside the form. */
export default function AuthLayout({ children, labelledBy }: { children: ReactNode; labelledBy?: string }) {
  return (
    <div className="auth-shell">
      <ThemeToggle className="auth-theme" />
      <aside className="auth-aside">
        <Wordmark size={34} />
        <p className="auth-statement">Check election results, polling unit by polling unit.</p>
        <span />
      </aside>
      <main className="auth-main">
        <section className="auth-card" aria-labelledby={labelledBy}>
          <div className="auth-card-logo">
            <Wordmark size={30} />
          </div>
          {children}
        </section>
      </main>
    </div>
  )
}
