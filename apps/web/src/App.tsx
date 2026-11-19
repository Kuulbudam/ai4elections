import { useEffect, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import type { components } from './api/schema'
import { AuthProvider, useAuth } from './auth/AuthProvider'
import AuthLayout from './components/AuthLayout'
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
import Areas from './pages/Areas'
import Reports from './pages/Reports'
import ReportView from './pages/ReportView'
import Assistant from './pages/Assistant'
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
  const [text, setText] = useState(base ? 'Checking API…' : 'API address not configured for this deployment.')

  useEffect(() => {
    if (!base) return
    const ctrl = new AbortController()
    fetch(`${base}/health`, { signal: ctrl.signal })
      .then(async (r) => {
        if (!r.ok) return setText('API unreachable. Try again shortly.')
        const h = (await r.json()) as Health
        setText(`API online (version ${h.version}).`)
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setText('API unreachable. Try again shortly.')
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
        <p role="alert">This deployment is not configured: the Supabase URL and public key are missing.</p>
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
              AI4Elections
              <span className="brand-sub">Electoral results validation and evidence review</span>
            </Link>
            <div className="account">
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
          <ProjectShell>
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
              <Route path="/projects/:projectId/reports" element={<Reports />} />
              <Route path="/reports/:reportId" element={<ReportView />} />
              <Route path="/findings/:findingId" element={<FindingDetail />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="*" element={<p className="empty">Page not found.</p>} />
            </Routes>
          </ProjectShell>
        </main>
        <footer className="footer">
          <div className="footer-inner">
            <p>Findings are signals for human review, not verdicts of wrongdoing. Every figure links back to its source row.</p>
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
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}
