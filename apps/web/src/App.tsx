import { useEffect, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import type { components } from './api/schema'
import { AuthProvider, useAuth } from './auth/AuthProvider'
import { getSupabase } from './lib/supabase'
import Mapping from './pages/Mapping'
import Members from './pages/Members'
import Organisations from './pages/Organisations'
import Profile from './pages/Profile'
import ProjectOverview from './pages/ProjectOverview'
import Projects from './pages/Projects'
import SignIn from './pages/SignIn'
import Upload from './pages/Upload'

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
  const { session, loading } = useAuth()

  if (!getSupabase()) {
    return (
      <main className="auth-shell">
        <section className="card auth-card">
          <h1 className="brand">AI4Elections</h1>
          <p role="alert">This deployment is not configured: the Supabase URL and public key are missing.</p>
        </section>
      </main>
    )
  }
  if (loading) return <p className="muted center">Loading…</p>
  if (!session) return <SignIn />

  return (
    <BrowserRouter>
      <div className="layout">
        <header className="topbar">
          <Link to="/" className="brand">
            AI4Elections
          </Link>
          <nav aria-label="Main">
            <NavLink to="/" end>
              Organisations
            </NavLink>
            <NavLink to="/profile">Profile</NavLink>
          </nav>
          <button type="button" className="link" onClick={() => void getSupabase()!.auth.signOut()}>
            Sign out
          </button>
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Organisations />} />
            <Route path="/orgs/:orgId/members" element={<Members />} />
            <Route path="/orgs/:orgId/projects" element={<Projects />} />
            <Route path="/projects/:projectId" element={<ProjectOverview />} />
            <Route path="/projects/:projectId/upload" element={<Upload />} />
            <Route path="/versions/:versionId/mapping" element={<Mapping />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="*" element={<p className="empty">Page not found.</p>} />
          </Routes>
        </main>
        <footer className="muted small footer">
          Findings are signals for human review, not verdicts. All development data is synthetic. <ApiStatus />
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
