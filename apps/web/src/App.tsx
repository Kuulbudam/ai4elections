import { useEffect, useState, type ReactNode } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import type { components } from './api/schema'
import { AuthProvider, useAuth } from './auth/AuthProvider'
import AuthLayout from './components/AuthLayout'
import Logo from './components/Logo'
import { ThemeProvider, ThemeToggle } from './lib/theme'
import { getSupabase } from './lib/supabase'
import Mapping from './pages/Mapping'
import Members from './pages/Members'
import Organisations from './pages/Organisations'
import Profile from './pages/Profile'
import ProjectOverview from './pages/ProjectOverview'
import Projects from './pages/Projects'
import SignIn from './pages/SignIn'
import { TwoStepChallenge, TwoStepEnrol } from './pages/TwoStep'
import Upload from './pages/Upload'
import Validation from './pages/Validation'
import Analysis from './pages/Analysis'
import ProjectShell from './components/ProjectShell'
import ErrorBoundary from './components/ErrorBoundary'
import Areas from './pages/Areas'
import Reports from './pages/Reports'
import ReportView from './pages/ReportView'
import Assistant from './pages/Assistant'
import Sheets from './pages/Sheets'
import SheetDetail from './pages/SheetDetail'
import Collation from './pages/Collation'
import Claims from './pages/Claims'
import FindingDetail from './pages/FindingDetail'
import Findings from './pages/Findings'

type Health = components['schemas']['Health']

function apiBaseUrl(): string | undefined {
  const value = import.meta.env.VITE_API_BASE_URL as string | undefined
  return value && value.trim() ? value.trim().replace(/\/$/, '') : undefined
}

export function ApiStatus() {
  const base = apiBaseUrl()
  const [text, setText] = useState(base ? 'Connecting…' : 'Server address not set.')

  useEffect(() => {
    if (!base) return
    const ctrl = new AbortController()
    fetch(`${base}/health`, { signal: ctrl.signal })
      .then(async (r) => {
        if (!r.ok) return setText('Can’t reach the server. Try again shortly.')
        const h = (await r.json()) as Health
        setText(`Connected · v${h.version}`)
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setText('Can’t reach the server. Try again shortly.')
      })
    return () => ctrl.abort()
  }, [base])

  return <span data-testid="api-status">{text}</span>
}

function Shell() {
  const { session, loading, aal } = useAuth()

  if (!getSupabase()) {
    return (
      <AuthLayout>
        <p role="alert">The app is not set up yet: the database address and public key are missing. Contact your administrator.</p>
      </AuthLayout>
    )
  }
  if (loading) return <p className="muted center">Loading…</p>
  if (!session) return <SignIn />
  if (aal.current !== 'aal2') return aal.next === 'aal2' ? <TwoStepChallenge /> : <TwoStepEnrol />

  return (
    <BrowserRouter>
      <div className="layout">
        <header className="topbar">
          <div className="masthead">
            <Link to="/" className="brand">
              <Logo size={40} />
              <span>
                Election as a System
                <span className="brand-sub">EaaS · election results checking</span>
              </span>
            </Link>
            <div className="account">
              <ThemeToggle />
              <span>{session.user.email}</span>
              <button type="button" className="link" onClick={() => void getSupabase()!.auth.signOut()}>
                Sign out
              </button>
            </div>
          </div>
          <nav className="mainnav" aria-label="Main">
            <NavLink to="/" end>
              Dashboard
            </NavLink>
            <NavLink to="/profile">Profile</NavLink>
          </nav>
        </header>
        <main className="content">
          <PageGuard>
            <ProjectShell>
              <PageGuard>
                <Routes>
                  <Route path="/" element={<Organisations />} />
                  <Route path="/orgs/:orgId/members" element={<Members />} />
                  <Route path="/orgs/:orgId/projects" element={<Projects />} />
                  <Route path="/projects/:projectId" element={<ProjectOverview />} />
                  <Route path="/projects/:projectId/upload" element={<Upload />} />
                  <Route path="/versions/:versionId/mapping" element={<Mapping />} />
                  <Route path="/versions/:versionId/validation" element={<Validation />} />
                  <Route path="/versions/:versionId/analysis" element={<Analysis />} />
                  <Route path="/versions/:versionId/areas" element={<Areas />} />
                  <Route path="/projects/:projectId/findings" element={<Findings />} />
                  <Route path="/projects/:projectId/claims" element={<Claims />} />
                  <Route path="/projects/:projectId/assistant" element={<Assistant />} />
                  <Route path="/projects/:projectId/sheets" element={<Sheets />} />
                  <Route path="/projects/:projectId/sheets/:sheetId" element={<SheetDetail />} />
                  <Route path="/projects/:projectId/collation" element={<Collation />} />
                  <Route path="/projects/:projectId/collation/:checkId" element={<Collation />} />
                  <Route path="/projects/:projectId/reports" element={<Reports />} />
                  <Route path="/reports/:reportId" element={<ReportView />} />
                  <Route path="/findings/:findingId" element={<FindingDetail />} />
                  <Route path="/profile" element={<Profile />} />
                  <Route path="*" element={<p className="empty">Page not found.</p>} />
                </Routes>
              </PageGuard>
            </ProjectShell>
          </PageGuard>
        </main>
        <footer className="footer">
          <div className="footer-inner">
            <p>A flag means a figure needs checking. It is not proof that anyone did anything wrong.</p>
            <p>
              <ApiStatus />
            </p>
          </div>
        </footer>
      </div>
    </BrowserRouter>
  )
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <Shell />
      </AuthProvider>
    </ThemeProvider>
  )
}

/** Any page crash shows a message instead of a blank screen; moving to another page clears it. */
function PageGuard({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  return <ErrorBoundary resetKey={pathname}>{children}</ErrorBoundary>
}
