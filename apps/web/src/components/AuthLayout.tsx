import type { ReactNode } from 'react'
import Logo from './Logo'
import { ThemeToggle } from '../lib/theme'

/** Split layout for sign-in and two-step screens: the product beside the form. */
export default function AuthLayout({ children, labelledBy }: { children: ReactNode; labelledBy?: string }) {
  return (
    <div className="auth-shell">
      <ThemeToggle className="auth-theme" />
      <aside className="auth-aside">
        <div className="auth-brand">
          <Logo size={56} />
          <p className="brand">
            Election as a System <span className="brand-short">EaaS</span>
          </p>
        </div>
        <div>
          <p className="auth-statement">Check election results, polling unit by polling unit.</p>
          <ul className="auth-points">
            <li>Upload results and see every row with a problem</li>
            <li>Spot polling units that look out of line with their neighbours</li>
            <li>Read result-sheet photos and compare them with the data</li>
            <li>Review each flag as a team and share a report you can stand behind</li>
          </ul>
        </div>
        <p className="small">Sign-in is for members of observer groups, newsrooms and research teams.</p>
      </aside>
      <main className="auth-main">
        <section className="auth-card" aria-labelledby={labelledBy}>
          <div className="auth-card-logo">
            <Logo size={40} />
            <span>Election as a System</span>
          </div>
          {children}
        </section>
      </main>
    </div>
  )
}
